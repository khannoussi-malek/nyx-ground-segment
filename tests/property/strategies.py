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

from gs.alarms.checks.limit import LimitCheck, deadband, floor
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
    above_floor_pct: (
        int  # every limit check's deadband = floor x (1 + this / 100); 0 = at the floor
    )
    window_s: float
    faults: frozenset[str]
    mode_checks: bool = False  # SAFE checks (floor rule too) and an unmonitored FAULT (D-EXT-06)


def tunings() -> st.SearchStrategy[Tuning]:
    return st.builds(
        Tuning,
        persistence=st.integers(1, 5),
        above_floor_pct=st.one_of(st.just(0), st.integers(0, 200)),  # floor end is the chatter end
        window_s=st.sampled_from((0.5, 0.75, 1.0, 1.5, 2.0)),
        faults=st.sets(st.sampled_from(FAULT_KINDS), min_size=1).map(frozenset),  # swarm
        mode_checks=st.sampled_from((True,) * 5 + (False,)),  # the mode path is the point
    )


LEAK = "cabin_leak_rate_kpa_s"
LEAK_LIMIT = {"type": "limit", "red_low": -0.6, "yellow_low": -0.3, "hysteresis_abs": 0.02}
MODES = ("SAFE", "STANDBY", "TEST", "FAULT")
MODE_PARAMS = ("cabin_pressure_kpa", "bus_voltage_v")
_MODE_BIAS = MODES + ("SAFE", "FAULT", "SAFE", "FAULT", "TEST")


@lru_cache(maxsize=512)
def mission_for(t: Tuning, derived: bool = False) -> MissionDb:
    """The shipped limits with this tuning applied, through the real mission-db loader.
    `derived`: also the shipped derived parameters (the rate_of window is the tuning's), limits on
    them and a calibration table on cabin_temp_c (out-of-table raws give NULL, D-EXT-04)."""
    doc: dict[str, Any] = yaml.safe_load((MISSION_DIR / "limits.yaml").read_bytes())
    if not derived:
        doc["limits"].pop("power_w", None)  # files() is the raw-only base
    else:
        doc["limits"][LEAK] = {"checks": [dict(LEAK_LIMIT)]}
    d = doc["defaults"]
    doc["defaults"] = {"hysteresis_pct": d["hysteresis_pct"], "persistence": t.persistence}
    for entry in doc["limits"].values():
        entry["checks"] = [_tune_check(c, t) for c in entry["checks"]]
        modes = entry.pop("checks_by_mode", {})
        if t.mode_checks and modes:
            entry["checks_by_mode"] = {
                m: [_tune_check(c, t) for c in cs] for m, cs in modes.items()
            }
    if t.mode_checks:
        doc["limits"]["bus_voltage_v"]["checks_by_mode"] = {"FAULT": []}  # not monitored
    over = {"limits": yaml.safe_dump(doc, sort_keys=False).encode()}
    if derived:
        over["parameters"] = _derived_parameters(t, doc["limits"]["cabin_temp_c"]["checks"][0])
    return ld(files(**over))


def _derived_parameters(t: Tuning, temp_limit: dict[str, Any]) -> bytes:
    """Identity table on cabin_temp_c just wide enough for the loader (red limits plus deadband,
    D-EXT-04), so the faults that overshoot a limit land outside it (NULL, out_of_range)."""
    doc: dict[str, Any] = yaml.safe_load((MISSION_DIR / "parameters.yaml").read_bytes())
    doc["derived"][LEAK]["window_s"] = t.window_s
    c = LimitCheck.model_validate(temp_limit)
    lo, hi = c.red_low - deadband(c), c.red_high + deadband(c)  # type: ignore[operator]
    doc["parameters"]["cabin_temp_c"]["calibration"] = {"table": [[lo, lo], [hi, hi]]}
    return yaml.safe_dump(doc, sort_keys=False).encode()


def _tune_check(c: dict[str, Any], t: Tuning) -> dict[str, Any]:
    out = dict(c)
    if out["type"] == "roc":
        out["window_s"] = t.window_s
        return out
    # D-ALM-02 floor of this check (it does not depend on the hysteresis), then draw above it
    lo = floor(LimitCheck.model_validate({**c, "hysteresis_pct": None, "hysteresis_abs": 1.0}))
    d = lo * (1 + t.above_floor_pct / 100)
    out.pop("hysteresis_pct", None)
    out.pop("hysteresis_abs", None)
    if "hysteresis_abs" in c:
        out["hysteresis_abs"] = d
    else:
        bounds = [out[k] for k in ("red_low", "yellow_low", "yellow_high", "red_high") if k in out]
        out["hysteresis_pct"] = d / (max(bounds) - min(bounds)) * 100
    return out


@lru_cache(maxsize=1)
def _bounds() -> dict[str, tuple[float, ...]]:
    out: dict[str, tuple[float, ...]] = {}
    db = load_dir(MISSION_DIR)
    for p, e in db.limits.items():
        if p not in db.raw_names:  # the runs feed the engine raw nominals only
            continue
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
    def one(draw: st.DrawFn, only: tuple[str, ...] = tuple(params)) -> Fault:
        p = draw(st.sampled_from(only))
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

    # one fault is always on a param whose checks change with the mode (cabin_pressure_kpa in SAFE,
    # bus_voltage_v in FAULT), so a mode switch often meets an open alarm there
    return st.builds(
        lambda first, rest: [first, *rest], one(MODE_PARAMS), st.lists(one(), max_size=5)
    )


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


@st.composite
def mode_schedule(draw: st.DrawFn, n: int) -> tuple[str, ...]:
    """A mode per frame: short stretches (switches fall at arbitrary frames, mid-batch), biased to
    SAFE and FAULT, the modes whose checks differ (limits.yaml SAFE, FAULT unmonitored)."""
    out: list[str] = []
    while len(out) < n:
        out += [draw(st.sampled_from(_MODE_BIAS))] * draw(st.integers(1, 6))
    return tuple(out[:n])


# ---- derived expressions: P6 -------------------------------------------------------------------
NAMES = ("a", "b", "c", "bus_voltage_v")
MAX_TREE_HEIGHT = 12  # the compiler allows 15 levels, 64 nodes, 256 characters
MAX_TREE_NODES = 50
MAX_TEXT = 240

# An expression tree: ("num", float, text) | ("name", id) | ("neg", t) | ("bin", op, l, r)
# | ("call", fn, (t, ...)). Written here, with its own printer and (in the test) its own evaluator.
Tree = tuple[Any, ...]
_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def _num() -> st.SearchStrategy[Tree]:
    ints = st.integers(0, 10**6).map(lambda i: ("num", float(i), str(i)))
    floats = st.floats(0, 1.7e308, allow_nan=False, allow_infinity=False).map(
        lambda x: ("num", x, repr(x))
    )
    return ints | floats


def _extend(inner: st.SearchStrategy[Tree]) -> st.SearchStrategy[Tree]:
    return (
        st.tuples(st.just("neg"), inner)
        | st.tuples(st.just("bin"), st.sampled_from(tuple(_PREC)), inner, inner)
        | st.tuples(st.just("call"), st.just("abs"), st.tuples(inner))
        | st.tuples(st.just("call"), st.just("sqrt"), st.tuples(inner))
        | st.tuples(
            st.just("call"),
            st.sampled_from(("min", "max")),
            st.lists(inner, min_size=2, max_size=4).map(tuple),
        )
    )


def _kids(t: Tree) -> tuple[Tree, ...]:
    return t[1:2] if t[0] == "neg" else t[2:] if t[0] == "bin" else t[2] if t[0] == "call" else ()


def _height(t: Tree) -> int:
    return 1 + max(map(_height, _kids(t)), default=0)


def _nodes(t: Tree) -> int:
    """Python `ast` nodes of the printed text (operators and Load contexts count), plus the root."""
    match t[0]:
        case "num":
            return 1
        case "name":
            return 2
        case "neg":
            return 2 + _nodes(t[1])
        case "bin":
            return 2 + _nodes(t[2]) + _nodes(t[3])
        case _:
            return 3 + sum(map(_nodes, t[2]))


def render(t: Tree, parent: int = 0, right: bool = False) -> str:
    """Text with the fewest parentheses precedence needs, so the parser's precedence is tested."""
    match t[0]:
        case "num":
            return str(t[2])
        case "name":
            return str(t[1])
        case "neg":
            return f"-({render(t[1])})" if t[1][0] == "bin" else f"-{render(t[1])}"
        case "bin":
            p = _PREC[t[1]]
            text = f"{render(t[2], p)} {t[1]} {render(t[3], p, True)}"
            return f"({text})" if p < parent or (p == parent and right) else text
        case _:
            return f"{t[1]}({', '.join(render(a) for a in t[2])})"


def exprs() -> st.SearchStrategy[Tree]:
    """Grammar-valid expression trees over NAMES (render() gives the text)."""
    leaves = _num() | st.sampled_from(NAMES).map(lambda n: ("name", n))
    return (
        st.recursive(leaves, _extend, max_leaves=10)
        .filter(lambda t: _height(t) <= MAX_TREE_HEIGHT and _nodes(t) <= MAX_TREE_NODES)
        .filter(lambda t: len(render(t)) <= MAX_TEXT)
    )


_ANY = st.floats() | st.none()  # nan and inf too: a non-finite input is NULL (nonfinite)
EXPR_INPUTS = st.dictionaries(st.sampled_from(NAMES), _ANY)  # an absent name is NULL (missing)
