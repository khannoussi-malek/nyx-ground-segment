"""The chaos invariants I1, I2, I3, I6, I7, I8 as pure checks over data the soak collected
(design 11.5). A frame is "due" once now >= max(frame time + 30 s, end of the last infrastructure
action + 60 s); a command is due by max(QUEUED + 20 s, that same action end + 60 s) (EX-04)."""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from gs.alarms.quality import FAULT_PARAMS, FaultWindow, QEvent, Quality, _covers, quality

FRAME_GRACE = timedelta(seconds=30)
ACTION_GRACE = timedelta(seconds=60)
COMMAND_GRACE = timedelta(seconds=20)
DLQ_SLACK = timedelta(seconds=5)  # an entry's id time trails its frame's t_onboard by ms
INJECTED_DLQ = frozenset({"schema", "json_invalid"})
WINDOW_PAD = (timedelta(seconds=1), timedelta(seconds=2))  # clock skew sim vs host, alarm lag

Key = tuple[str, datetime, int]  # (vehicle, t_onboard, seq)


@dataclass(frozen=True)
class LedgerLine:
    vehicle: str
    seq: int
    t: datetime
    fate: str  # sent, dropout, malformed, duplicate, overflow


@dataclass(frozen=True)
class AlarmRow:
    alarm_id: str
    vehicle: str
    param: str
    kind: str


@dataclass(frozen=True)
class Command:
    queued_at: datetime
    state: str
    failure_reason: str | None


@dataclass(frozen=True)
class Gap:  # a GAP event: first missing seq and how many were lost
    vehicle: str
    from_seq: int
    lost: int


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str = ""


def cutoff(now: datetime, infra_end: datetime | None) -> datetime | None:
    """Frames with t_onboard <= the result are due; None while the last action is still within
    its 60 s grace (pass `now` as `infra_end` while one is in flight)."""
    if infra_end is not None and now < infra_end + ACTION_GRACE:
        return None
    return now - FRAME_GRACE


def _sample(items: Iterable[object], n: int = 3) -> str:
    shown = [str(x) for _, x in zip(range(n), items, strict=False)]
    return ", ".join(shown)


def _expected_gaps(ledger: Sequence[LedgerLine], due: datetime) -> Counter[Gap]:
    """Each dropout run that the next frame follows in the same count (seq + 1) is one GAP."""
    out: Counter[Gap] = Counter()
    run: dict[str, list[LedgerLine]] = {}
    for ln in sorted(ledger, key=lambda x: x.t):  # `sent` lines come later than the tick's
        if ln.fate == "dropout":
            run.setdefault(ln.vehicle, []).append(ln)
        elif ln.fate == "sent" and (r := run.pop(ln.vehicle, [])):
            if ln.seq == (r[-1].seq + 1) % 2**32 and ln.t <= due:
                out[Gap(ln.vehicle, r[0].seq, len(r))] += 1
    return out


def check_i1(
    ledger: Sequence[LedgerLine], stored: Collection[Key], gaps: Sequence[Gap], due: datetime
) -> Result:
    """Every due `sent` frame is stored, every dropout run is one GAP of its length, and the
    simulator dropped nothing (overflow = 0)."""
    have = set(stored)
    lost = [
        x for x in ledger if x.fate == "sent" and x.t <= due and (x.vehicle, x.t, x.seq) not in have
    ]
    overflow = sum(x.fate == "overflow" for x in ledger)
    unseen = _expected_gaps(ledger, due) - Counter(gaps)
    if lost or overflow or unseen:
        first = _sample((x.vehicle, x.seq, x.t.time()) for x in lost)
        detail = (
            f"{len(lost)} sent frames not stored ({first}), "
            f"{overflow} overflow, {sum(unseen.values())} dropout runs without a GAP "
            f"({_sample(unseen)})"
        )
        return Result("I1", False, detail)
    return Result("I1", True, "0 lost")


def check_i2(ledger: Sequence[LedgerLine], stored: Sequence[Key], due: datetime) -> Result:
    """Stored rows of due frames = the distinct frames the simulator sent, at most one row per
    (vehicle, epoch, seq). The epoch comes from the ledger: it starts anew when a seq goes down."""
    sent = sorted((x for x in ledger if x.fate == "sent" and x.t <= due), key=lambda x: x.t)
    epoch: dict[Key, int] = {}
    cur: dict[str, tuple[int, int]] = {}  # vehicle -> (epoch, last seq)
    for x in sent:
        n, last = cur.get(x.vehicle, (0, x.seq))
        n += x.seq < last
        cur[x.vehicle] = (n, x.seq)
        epoch[(x.vehicle, x.t, x.seq)] = n
    rows = [k for k in stored if k[1] <= due]
    extra = [k for k in rows if k not in epoch]
    per = Counter((k[0], epoch[k], k[2]) for k in rows if k in epoch)
    dupes = sum(n - 1 for n in per.values())
    if extra or dupes:
        return Result("I2", False, f"{len(extra)} rows not sent ({_sample(extra)}), {dupes} twice")
    return Result("I2", True, f"{len(rows)} rows")


def check_i3_recompute(
    recorded: Mapping[str, Sequence[str]], recomputed: Mapping[str, Sequence[str]]
) -> Result:
    """The end-of-run determinism check (D-REP-05 b): per vehicle, the transition keys step()
    derives from the stored frames equal the recorded ones, same order. I3 passes only when this
    and the continuous path check both do."""
    for v in sorted(recorded):
        a, b = list(recorded[v]), list(recomputed.get(v, []))
        if a != b:
            i = next(
                (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y),
                min(len(a), len(b)),
            )
            return Result(
                "I3",
                False,
                f"recompute differs for {v}: {len(a)} recorded, {len(b)} recomputed, first "
                f"difference at index {i} (recorded {a[i : i + 1]}, recomputed {b[i : i + 1]})",
            )
    return Result(
        "I3", True, f"recompute equals recorded ({sum(map(len, recorded.values()))} keys)"
    )


# kind -> {state before: states after}; None = no open alarm (D-ALM-07/08, as P5)
_PATHS: dict[str, dict[str | None, tuple[str, ...]]] = {
    "RAISE": {None: ("UNACK",), "RTN_UNACK": ("UNACK",)},
    "ESCALATE": {"UNACK": ("UNACK",), "ACKED": ("UNACK", "ACKED")},
    "DEESCALATE": {"UNACK": ("UNACK",), "ACKED": ("ACKED",)},
    "RTN": {"UNACK": ("RTN_UNACK",)},
    "CLEAR": {"ACKED": ("CLEARED",)},
    "ACK": {"UNACK": ("ACKED",), "RTN_UNACK": ("CLEARED",)},
}


def check_i3_paths(rows: Sequence[AlarmRow], before: Collection[str] = ()) -> Result:
    """`rows` in insertion order. Each alarm follows a valid D-ALM-07/08 path and a
    (vehicle, param) never has two open alarms. After an ESCALATE from ACKED the state is
    either one, so the model tracks the set of states it may be in. `before`: alarm ids that
    were already open when the soak started (an earlier run, a person in the console): their first
    row here may start from any state its kind allows."""
    state: dict[str, set[str | None]] = {}
    open_of: dict[tuple[str, str], str] = {}
    seen: set[str] = set()
    for r in rows:
        rules = _PATHS.get(r.kind, {})
        if r.alarm_id in state:
            were = state[r.alarm_id]
        elif r.alarm_id in before and r.alarm_id not in seen:
            were = {b for b in rules if b is not None}  # unknown: whatever the kind allows
        else:
            were = {None}
        seen.add(r.alarm_id)
        if not all(b in rules for b in were) or not were:
            return Result(
                "I3", False, f"{r.kind} on {r.vehicle}/{r.param} from {sorted(map(str, were))}"
            )
        after = {a for b in were for a in rules[b]}
        key = (r.vehicle, r.param)
        if after == {"CLEARED"}:
            open_of.pop(key, None)
            state.pop(r.alarm_id, None)
            continue
        if open_of.setdefault(key, r.alarm_id) != r.alarm_id:
            return Result("I3", False, f"two open alarms on {r.vehicle}/{r.param}")
        state[r.alarm_id] = after  # type: ignore[assignment]  # CLEARED never reaches here
    return Result("I3", True, f"{len(rows)} transitions")


def check_i6(
    dlq: Sequence[tuple[datetime, str]], ledger: Sequence[LedgerLine], due: datetime
) -> Result:
    """`dlq` = (entry time, reason). Only injected bad frames reach the DLQ: schema and
    json_invalid entries equal the ledger's malformed count; any other reason, poison above all,
    is a failure. The count may differ by what the 5 s slack around the cut-off lets through."""
    other = Counter(r for _, r in dlq if r not in INJECTED_DLQ)
    sent = sum(x.fate == "malformed" and x.t <= due for x in ledger)
    lo = sum(r in INJECTED_DLQ and t <= due - DLQ_SLACK for t, r in dlq)
    hi = sum(r in INJECTED_DLQ and t <= due + DLQ_SLACK for t, r in dlq)
    if other or not lo <= sent <= hi:
        return Result(
            "I6", False, f"dlq {dict(other)} other, {lo}..{hi} injected vs {sent} malformed"
        )
    return Result("I6", True, f"poison 0, {sent} malformed")


def check_i7(commands: Sequence[Command], now: datetime, infra_end: datetime | None) -> Result:
    stuck = []
    for c in commands:
        deadline = c.queued_at + COMMAND_GRACE
        if infra_end is not None:
            deadline = max(deadline, infra_end + ACTION_GRACE)
        terminal = c.state == "VERIFIED" or (c.state == "FAILED" and c.failure_reason is not None)
        if now >= deadline and not terminal:
            stuck.append(f"{c.state}@{c.queued_at.time()}")
    if stuck:
        return Result("I7", False, f"{len(stuck)} commands stuck: {', '.join(stuck[:3])}")
    return Result("I7", True, f"{len(commands)} commands")


def fault_window(vehicle: str, fault: str, start: datetime, end: datetime) -> FaultWindow | None:
    """The alarm window of an injected fault, None for those that move no parameter."""
    if fault not in FAULT_PARAMS:
        return None
    return FaultWindow(
        vehicle, fault, start - WINDOW_PAD[0], end + WINDOW_PAD[1], FAULT_PARAMS[fault]
    )


def check_i8(events: Sequence[QEvent], windows: Sequence[FaultWindow]) -> tuple[Result, Quality]:
    """D-ALM-15 over the recorded alarm events with the injected faults' windows. A false raise is
    a RAISE outside every window; a short alarm (Rule 3a) counts only when its alarm was not
    raised by an injected fault, since a fault the operator isolates ends its alarm early."""
    q = quality(events, windows)
    injected = {
        e.alarm_id for e in events if e.kind == "RAISE" and any(_covers(w, e) for w in windows)
    }
    rest = quality([e for e in events if e.alarm_id not in injected], windows)
    bad = q.false_raises or rest.short_t1 or rest.short_t0
    detail = f"{q.raises} raises, {q.false_raises} false, short {rest.short_t1}/{rest.short_t0}"
    return Result("I8", not bad, detail), q
