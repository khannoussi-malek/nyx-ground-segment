"""Absolute limit check with deadband (D-ALM-01, D-ALM-02)."""

from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from gs.alarms.checks import CheckConfig, Reading, Sample, register
from gs.alarms.contract import Level

FLOOR_FACTOR = 1.5
FLOOR_TOLERANCE = 1e-9  # Review Focus 1: shipped deadbands equal the floor up to the last bit


class LimitCheck(CheckConfig):
    type: Literal["limit"]
    red_low: float | None = None
    yellow_low: float | None = None
    yellow_high: float | None = None
    red_high: float | None = None
    hysteresis_pct: float | None = Field(None, gt=0)
    hysteresis_abs: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _shape(self) -> LimitCheck:
        lows = (self.red_low, self.yellow_low)
        highs = (self.yellow_high, self.red_high)
        bounds = [v for v in (*lows, *highs) if v is not None]
        if not bounds:
            raise ValueError("a limit check needs at least one bound")
        if any(a >= b for a, b in zip(bounds, bounds[1:], strict=False)):
            raise ValueError("bounds must satisfy red_low < yellow_low < yellow_high < red_high")
        if self.hysteresis_pct is not None and self.hysteresis_abs is not None:
            raise ValueError("set hysteresis_pct or hysteresis_abs, not both")
        one_sided = all(v is None for v in lows) or all(v is None for v in highs)
        if one_sided and self.hysteresis_abs is None:
            raise ValueError("a one-sided limit requires hysteresis_abs")
        return self


def _bounds(cfg: LimitCheck) -> list[float]:
    vals = (cfg.red_low, cfg.yellow_low, cfg.yellow_high, cfg.red_high)
    return [v for v in vals if v is not None]


def _span(cfg: LimitCheck) -> float:
    # red_high - red_low; ponytail: outermost bounds when a two-sided check omits a red bound
    b = _bounds(cfg)
    return b[-1] - b[0]


def deadband(cfg: LimitCheck) -> float:
    if cfg.hysteresis_abs is not None:
        return cfg.hysteresis_abs
    if cfg.hysteresis_pct is None:
        raise ValueError("hysteresis not resolved (missiondb fills the default)")
    return cfg.hysteresis_pct / 100 * _span(cfg)


def floor(cfg: LimitCheck) -> float:
    """1.5 x max(1 % of span (two-sided only), 1 % of |each present limit|)."""
    has_low = cfg.red_low is not None or cfg.yellow_low is not None
    two_sided = has_low and (cfg.yellow_high is not None or cfg.red_high is not None)
    one_pct = max(abs(b) for b in _bounds(cfg)) / 100
    if two_sided:
        one_pct = max(one_pct, _span(cfg) / 100)
    return FLOOR_FACTOR * one_pct


def meets_floor(cfg: LimitCheck) -> bool:
    return deadband(cfg) >= floor(cfg) * (1 - FLOOR_TOLERANCE)


@dataclass(frozen=True)
class LimitState:  # stateless: the level is a function of the value and the current level
    pass


@register
class Limit:
    kind: ClassVar[str] = "limit"
    Config: ClassVar[type[CheckConfig]] = LimitCheck
    State: ClassVar[type] = LimitState

    def __init__(self, cfg: LimitCheck) -> None:
        self.cfg = cfg
        self.d = deadband(cfg)
        self.bands = (
            (Level.RED, cfg.red_low, cfg.red_high),
            (Level.YELLOW, cfg.yellow_low, cfg.yellow_high),
        )

    def initial(self) -> object:
        return LimitState()

    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        return state, self._classify(s.value, current)

    def _classify(self, v: float, current: Level) -> Reading:
        # most severe band first; on a limit = the more severe band (D-ALM-01); the deadband
        # only delays a move to a less severe band, so it widens bands at or below `current`
        for lvl, lo, hi in self.bands:
            d = self.d if lvl <= current else 0.0
            if lo is not None and v <= lo + d:
                return Reading(lvl, "LOW")
            if hi is not None and v >= hi - d:
                return Reading(lvl, "HIGH")
        return Reading(Level.NOMINAL, None)
