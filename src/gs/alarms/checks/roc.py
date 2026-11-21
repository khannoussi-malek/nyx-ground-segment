"""Rate-of-change check: least-squares slope over `window_s` of t_onboard (D-ALM-05)."""

import statistics
from dataclasses import dataclass
from typing import ClassVar, Literal, cast

from pydantic import Field

from gs.alarms.checks import CheckConfig, Reading, Sample, Side, register
from gs.alarms.contract import Level

MIN_FILL = 0.8  # share of the expected samples the window must hold
_EPS = 1e-9  # 0.8 * 1.0 / 0.1 is 8.000000000000002 in floats; 8 samples must count


class RocCheck(CheckConfig):
    type: Literal["roc"]
    limit_per_s: float = Field(gt=0)
    window_s: float = Field(gt=0, le=3600)
    direction: Literal["rising", "falling", "both"] = "both"
    severity: Literal["YELLOW", "RED"]


@dataclass(frozen=True)
class RocState:
    t: tuple[float, ...]  # t_onboard of the samples in the window, oldest first
    v: tuple[float, ...]


def push(st: RocState, t: float, v: float, window_s: float) -> RocState:
    """The window after sample (t, v): older than `window_s` dropped."""
    cut = t - window_s
    pts = [(a, b) for a, b in zip(st.t, st.v, strict=True) if a >= cut] + [(t, v)]
    return RocState(tuple(p[0] for p in pts), tuple(p[1] for p in pts))


def slope(st: RocState, window_s: float, period_s: float) -> float | None:
    """Least-squares slope of the window, or None below 80 % of the expected samples. Shared with
    the `rate_of` derived parameters (D-EXT-05)."""
    need = MIN_FILL * window_s / period_s * (1 - _EPS)
    # and they must span what `need` samples span at the nominal rate: a backlog stamped ms
    # apart after a stall holds enough samples over a few ms, and noise / ms is no rate
    span = max(st.t) - min(st.t)
    if len(st.t) < need or len(set(st.t)) < 2 or span < (need - 1) * period_s * (1 - _EPS):
        return None
    return statistics.linear_regression(st.t, st.v).slope


@register
class Roc:
    kind: ClassVar[str] = "roc"
    Config: ClassVar[type[CheckConfig]] = RocCheck
    State: ClassVar[type] = RocState

    def __init__(self, cfg: RocCheck) -> None:
        self.cfg = cfg

    def initial(self) -> object:
        return RocState((), ())

    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        new = push(cast(RocState, state), s.t, s.value, self.cfg.window_s)
        sl = slope(new, self.cfg.window_s, s.period_s)
        if sl is None:
            return new, None
        lim = self.cfg.limit_per_s
        hit = {"falling": sl <= -lim, "rising": sl >= lim, "both": abs(sl) >= lim}
        if not hit[self.cfg.direction]:
            return new, Reading(Level.NOMINAL, None, sl)
        side: Side = "RISING" if sl > 0 else "FALLING"
        return new, Reading(Level[self.cfg.severity], side, sl)
