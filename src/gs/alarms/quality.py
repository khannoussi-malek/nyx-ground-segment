"""Alarm-quality metrics (D-ALM-15): false raises, chatter index, short alarms, flood."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from gs.alarms.contract import TransitionKind

FAULT_PARAMS: Mapping[str, frozenset[str]] = {
    "leak": frozenset({"cabin_pressure_kpa"}),
    "overheat": frozenset({"cabin_temp_c"}),
}
PSI_FLAG = 0.05
SHORT_S = 20.0
WINDOW = timedelta(minutes=10)
FLOOD_START = 10
FLOOD_END = 5


@dataclass(frozen=True)
class QEvent:
    vehicle: str
    param: str
    kind: TransitionKind
    t: datetime  # trigger_t_onboard, or processed_at for an ACK
    alarm_id: UUID


@dataclass(frozen=True)
class FaultWindow:
    vehicle: str
    fault: str
    start: datetime
    end: datetime
    params: frozenset[str]  # FAULT_PARAMS[fault]; a "step" window carries its own param


@dataclass(frozen=True)
class Quality:
    raises: int
    false_raises: int | None
    psi: Mapping[str, float]
    chattering: tuple[str, ...]
    short_t1: int
    short_t0: int
    peak_10min: Mapping[str, int]
    time_in_flood_s: float


def _covers(w: FaultWindow, e: QEvent) -> bool:
    return w.vehicle == e.vehicle and e.param in w.params and w.start <= e.t <= w.end


def _psi(raise_ts: list[datetime]) -> float:
    """Kondaveeti 2010: sum over run lengths r of P(r)/r (r = s between RAISEs)."""
    runs = Counter((b - a).total_seconds() for a, b in zip(raise_ts, raise_ts[1:], strict=False))
    n = sum(runs.values())
    return sum(c / n / r for r, c in runs.items() if r > 0)


def _peak(ts: list[datetime]) -> int:
    # ponytail: O(n^2) per series; fine for session-sized event lists
    return max((sum(1 for u in ts if t - WINDOW < u <= t) for t in ts), default=0)


def _flood_s(ts: list[datetime]) -> float:
    # trailing window is (T - 10 min, T]: a RAISE leaves it exactly 10 min later
    points = sorted([(t, 1, 1) for t in ts] + [(t + WINDOW, 0, -1) for t in ts])
    count, start, total = 0, None, 0.0
    for t, _, d in points:
        count += d
        if start is None and count >= FLOOD_START:
            start = t
        elif start is not None and count < FLOOD_END:
            total += (t - start).total_seconds()
            start = None
    return total


def quality(events: Sequence[QEvent], fault_windows: Sequence[FaultWindow] | None) -> Quality:
    wins = fault_windows or []
    steps = [w for w in wins if w.fault == "step"]
    kept = sorted((e for e in events if not any(_covers(w, e) for w in steps)), key=lambda e: e.t)
    by_key: dict[str, list[QEvent]] = defaultdict(list)
    for e in kept:
        by_key[f"{e.vehicle}/{e.param}"].append(e)

    psi: dict[str, float] = {}
    short_t1 = short_t0 = 0
    chatter: set[str] = set()
    for key, es in by_key.items():
        raise_ts = [e.t for e in es if e.kind == "RAISE"]
        psi[key] = _psi(raise_ts)
        short = 0
        raised: datetime | None = None
        off: datetime | None = None
        for e in es:
            if e.kind == "RAISE":
                if off is not None and (e.t - off).total_seconds() < SHORT_S:
                    short_t0 += 1
                    short += 1
                raised, off = e.t, None
            elif e.kind in ("RTN", "CLEAR") and raised is not None:
                if (e.t - raised).total_seconds() < SHORT_S:
                    short_t1 += 1
                    short += 1
                raised, off = None, e.t
        if psi[key] >= PSI_FLAG or short:
            chatter.add(key)

    raise_evs = [e for e in kept if e.kind == "RAISE"]
    all_ts = [e.t for e in raise_evs]
    peak = {"all": _peak(all_ts)}
    for v in sorted({e.vehicle for e in raise_evs}):
        peak[v] = _peak([e.t for e in raise_evs if e.vehicle == v])

    return Quality(
        raises=len(raise_evs),
        false_raises=None
        if fault_windows is None
        else sum(1 for e in raise_evs if not any(_covers(w, e) for w in wins)),
        psi=psi,
        chattering=tuple(sorted(chatter)),
        short_t1=short_t1,
        short_t0=short_t0,
        peak_10min=peak,
        time_in_flood_s=_flood_s(all_ts),
    )
