"""Generators for the property suite (D-EXT-14, A-EXT-15).

Frames are built from the simulator's fault model (D-SIM-07), never from arbitrary bytes, so the
true answer (how many frames were dropped, where the restarts and jumps are) comes from the
generator itself.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import yaml
from hypothesis import strategies as st

from gs.common.missiondb import MissionDb, load_dir
from gs.ingest.seq import MOD, RESET_SEQ_MAX
from tests.property import profiles  # noqa: F401  (registers and loads the profiles)
from tests.support.mission import MISSION_DIR, files, ld

T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
TICK_S = 0.1
MIN_JUMP = 3  # below this a jump is indistinguishable from a 1-2 frame gap (D-ING-14 tolerance)
MAX_DROP_RUN = 100  # a 10 s dropout at 10 Hz
FAULT_KINDS = ("step", "wobble", "ramp", "null", "dropout", "spike")


# ---- sequence tracker: P1, P2 ------------------------------------------------------------------
@dataclass(frozen=True)
class Delivery:
    seq: int
    t: datetime
    epoch: int  # true boot index
    resend: bool  # a byte-identical redelivery of an earlier frame


@dataclass(frozen=True)
class Scenario:
    deliveries: tuple[Delivery, ...]
    epoch_starts: tuple[datetime, ...]  # t of each boot's first delivered frame
    dropped: tuple[int, ...]  # per boot: frames the counter skipped before a delivered one
    jumps: int
    restarts: int

    @property
    def distinct(self) -> int:
        return sum(not d.resend for d in self.deliveries)


_OPS = st.tuples(
    st.sampled_from(("ok", "ok", "ok", "drop", "dup", "jump")), st.integers(1, MAX_DROP_RUN)
)


def _at(tick: int) -> datetime:
    return T0 + timedelta(milliseconds=round(tick * TICK_S * 1000))


def _build_epoch(
    idx: int, seq0: int, tick0: int, ops: list[tuple[str, int]]
) -> tuple[list[Delivery], int, int, int, int]:
    """One boot: deliveries, dropped, jumps, last tick and last delivered seq."""
    out = [Delivery(seq0 % MOD, _at(tick0), idx, False)]
    seq, tick, run, dropped, jumps, delivered_last = seq0, tick0, 0, 0, 0, True
    pending: list[tuple[int, Delivery]] = []  # (frames still to wait, frame)

    def deliver(jump: int = 0) -> Delivery:
        nonlocal seq, tick, run, dropped, delivered_last
        seq, tick = seq + 1 + jump, tick + 1
        dropped, run, delivered_last = dropped + run, 0, True
        d = Delivery(seq % MOD, _at(tick), idx, False)
        out.append(d)
        return d

    def resend_due() -> None:
        nonlocal pending
        due = [f for w, f in pending if w <= 0]
        pending = [(w - 1, f) for w, f in pending if w > 0]
        out.extend(replace_resend(f) for f in due)

    for kind, n in ops:
        if kind == "drop":
            seq, tick, run, delivered_last = seq + n, tick + n, run + n, False
        elif kind == "jump" and delivered_last:
            deliver(MIN_JUMP + n * 7)
            jumps += 1
            resend_due()
        else:
            d = deliver()
            if kind == "dup":
                pending.append((n % 4, d))
            resend_due()
    out.extend(replace_resend(f) for _, f in pending)  # a resend never crosses a restart
    return out, dropped, jumps, tick, max(f.seq for f in out if not f.resend)


def replace_resend(d: Delivery) -> Delivery:
    return Delivery(d.seq, d.t, d.epoch, True)


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    """Boots of in-order frames with dropouts (counter running), resends, restarts, seq jumps."""
    restarts = draw(st.integers(0, 3))
    wrap = restarts == 0 and draw(st.booleans())  # a restart after a wrap is ambiguous (RFC 1982)
    seq0 = MOD - draw(st.integers(1, 100)) if wrap else draw(st.integers(0, 1000))
    deliveries: list[Delivery] = []
    starts: list[datetime] = []
    dropped: list[int] = []
    jumps, tick, last_seq = 0, 0, 0
    for idx in range(restarts + 1):
        lead = 0
        if idx:  # restart with time forward; frames 0..lead-1 of the new boot never arrive
            tick += draw(st.integers(1, 50))
            lead = min(draw(st.integers(0, RESET_SEQ_MAX)), last_seq)
            tick += lead
            seq0 = lead
        ops = draw(st.lists(_OPS, min_size=0, max_size=40))
        out, drop, jmp, tick, last_seq = _build_epoch(idx, seq0, tick, ops)
        deliveries += out
        starts.append(out[0].t)
        dropped.append(drop + lead)
        jumps += jmp
    return Scenario(tuple(deliveries), tuple(starts), tuple(dropped), jumps, restarts)


# ---- alarm engine: tuning and value series -----------------------------------------------------
@dataclass(frozen=True)
class Tuning:
    persistence: int
    hysteresis_k: int  # hysteresis_pct = shipped value x (1 + k / 100), so never below the floor
    window_s: float
    faults: frozenset[str]


def tunings() -> st.SearchStrategy[Tuning]:
    return st.builds(
        Tuning,
        persistence=st.integers(1, 5),
        hysteresis_k=st.integers(0, 100),
        window_s=st.sampled_from((0.5, 0.75, 1.0, 1.5, 2.0)),
        faults=st.sets(st.sampled_from(FAULT_KINDS), min_size=1).map(frozenset),  # swarm
    )


@lru_cache(maxsize=512)
def mission_for(t: Tuning) -> MissionDb:
    """The shipped limits with this tuning applied, through the real mission-db loader."""
    doc: dict[str, Any] = yaml.safe_load((MISSION_DIR / "limits.yaml").read_bytes())
    scale = 1 + t.hysteresis_k / 100
    d = doc["defaults"]
    doc["defaults"] = {"hysteresis_pct": d["hysteresis_pct"] * scale, "persistence": t.persistence}
    for entry in doc["limits"].values():
        entry["checks"] = [_tune_check(c, scale, t.window_s) for c in entry["checks"]]
    return ld(files(limits=yaml.safe_dump(doc, sort_keys=False).encode()))


def _tune_check(c: dict[str, Any], scale: float, window_s: float) -> dict[str, Any]:
    out = dict(c)
    if "hysteresis_pct" in out:
        out["hysteresis_pct"] = out["hysteresis_pct"] * scale
    if out["type"] == "roc":
        out["window_s"] = window_s
    return out


@lru_cache(maxsize=1)
def _bounds() -> dict[str, tuple[float, ...]]:
    from gs.alarms.checks.limit import LimitCheck

    out: dict[str, tuple[float, ...]] = {}
    for p, e in load_dir(MISSION_DIR).limits.items():
        for c in e.checks:
            if isinstance(c, LimitCheck):
                b = (c.red_low, c.yellow_low, c.yellow_high, c.red_high)
                out[p] = tuple(v for v in b if v is not None)
    return out


@dataclass(frozen=True)
class Fault:
    kind: str
    param: str
    start: int
    length: int
    target: float  # a value near one of the param's limits
    period: int  # wobble half-period in frames


@dataclass(frozen=True)
class Run:
    """Per frame: (seq, t seconds, values, reset flag); plus the frames after which the operator
    ACKs every open alarm."""

    frames: tuple[tuple[int, float, dict[str, float | None], bool], ...]
    acks: frozenset[int]


def _faults(kinds: frozenset[str], n: int) -> st.SearchStrategy[list[Fault]]:
    params = sorted(_bounds())

    @st.composite
    def one(draw: st.DrawFn) -> Fault:
        p = draw(st.sampled_from(params))
        b = _bounds()[p]
        span = b[-1] - b[0]
        edge = draw(st.sampled_from(b))
        outward = 1 if edge >= (b[0] + b[-1]) / 2 else -1  # past the edge, sometimes just inside
        target = edge + outward * draw(st.integers(-3, 15)) * span / 100
        return Fault(
            draw(st.sampled_from(sorted(kinds))),
            p,
            draw(st.integers(0, n - 1)),
            draw(st.integers(6, 120)),
            target,
            draw(st.integers(1, 8)),
        )

    return st.lists(one(), min_size=1, max_size=6)


def _value(f: Fault, i: int, nominal: float) -> float | None:
    k = i - f.start
    if f.kind == "null":
        return None
    if f.kind == "ramp":
        return nominal + (f.target - nominal) * min(1.0, (k + 1) / f.length)
    if f.kind == "wobble":
        sw = 0.02 * max(
            1.0, abs(f.target)
        )  # +- a little around the limit, of the order of a deadband
        return f.target + (sw if (k // f.period) % 2 else -sw)
    if f.kind == "spike":
        return f.target if k < 2 else nominal
    return f.target  # step


@st.composite
def runs(draw: st.DrawFn, kinds: frozenset[str]) -> Run:
    """Nominal frames at 10 Hz with the swarm's fault kinds laid over them; `dropout` removes
    frames (the seq counter keeps running) and flags the next frame as a reset."""
    from gs.simulator.nominals import NOMINALS

    n = draw(st.integers(60, 300))
    faults = draw(_faults(kinds, n))
    frames: list[tuple[int, float, dict[str, float | None], bool]] = []
    reset = False
    for i in range(n):
        vals: dict[str, float | None] = {p: nom for p, (nom, _) in NOMINALS.items()}
        skipped = False
        for f in faults:
            if f.start <= i < f.start + f.length:
                if f.kind == "dropout":
                    skipped = True
                else:
                    vals[f.param] = _value(f, i, NOMINALS[f.param][0])
        if skipped:
            reset = True
            continue
        frames.append((i, i * TICK_S, vals, reset))
        reset = False
    acks = draw(st.sets(st.integers(0, n - 1), max_size=12))
    return Run(tuple(frames), frozenset(acks))
