"""Built-in check on the RAW value of a calibrated parameter (D-EXT-04): RED LOW/HIGH when it
is outside the table (or the table's output overflows). Added by `compile_set`, not limits.yaml."""

from dataclasses import dataclass
from typing import ClassVar, Literal

from gs.alarms.checks import CheckConfig, Reading, Sample, register
from gs.alarms.contract import Level
from gs.common import calibration


class CalibrationRangeCheck(CheckConfig):
    type: Literal["calibration_range"]
    table: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class CalibrationRangeState:  # stateless
    pass


@register
class CalibrationRange:
    kind: ClassVar[str] = "calibration_range"
    Config: ClassVar[type[CheckConfig]] = CalibrationRangeCheck
    State: ClassVar[type] = CalibrationRangeState
    on_raw: ClassVar[bool] = True

    def __init__(self, cfg: CalibrationRangeCheck) -> None:
        self.table = calibration.Table(cfg.table)

    def initial(self) -> object:
        return CalibrationRangeState()

    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        side = calibration.side(self.table, s.value)
        if side is None and calibration.apply(self.table, s.value) is None:
            side = "HIGH"  # in the table but the output overflows: as unusable as off the end
        return state, Reading(Level.RED if side else Level.NOMINAL, side, s.value)
