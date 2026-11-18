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
    window_s: float = Field(gt=0)
    direction: Literal["rising", "falling", "both"] = "both"
    severity: Literal["YELLOW", "RED"]


@dataclass(frozen=True)
class RocState:
    t: tuple[float, ...]  # t_onboard of the samples in the window, oldest first
    v: tuple[float, ...]


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
        st = cast(RocState, state)
        cut = s.t - self.cfg.window_s
        pts = [(t, v) for t, v in zip(st.t, st.v, strict=True) if t >= cut] + [(s.t, s.value)]
        new = RocState(tuple(p[0] for p in pts), tuple(p[1] for p in pts))
        need = MIN_FILL * self.cfg.window_s / s.period_s * (1 - _EPS)
        # and they must span what `need` samples span at the nominal rate: a backlog stamped ms
        # apart after a stall holds enough samples over a few ms, and noise / ms is no rate
        span = max(new.t) - min(new.t)
        if len(pts) < need or len(set(new.t)) < 2 or span < (need - 1) * s.period_s * (1 - _EPS):
            return new, None
        slope = statistics.linear_regression(new.t, new.v).slope
        lim = self.cfg.limit_per_s
        hit = {"falling": slope <= -lim, "rising": slope >= lim, "both": abs(slope) >= lim}
        if not hit[self.cfg.direction]:
            return new, Reading(Level.NOMINAL, None, slope)
        side: Side = "RISING" if slope > 0 else "FALLING"
        return new, Reading(Level[self.cfg.severity], side, slope)
