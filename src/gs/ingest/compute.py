"""Raw frame values -> engineering values (D-EXT-04): calibrate, pass uncalibrated through,
derived parameters are NULL until D-EXT-05 lands. Pure; never raises into the frame path."""

from collections.abc import Mapping
from dataclasses import dataclass

from gs.common import calibration
from gs.common.interfaces import FrameHeader
from gs.common.missiondb import MissionDb


@dataclass(frozen=True)
class Computed:
    values: Mapping[str, float | None]  # engineering, every param_names entry
    raws: Mapping[str, float | None]  # the frame's values, every raw name
    nulls: tuple[tuple[str, str], ...]  # (param, reason): out_of_range | nonfinite | error


def _calibrated(table: calibration.Table, raw: float) -> tuple[float | None, str | None]:
    try:
        eng = calibration.apply(table, raw)
        if eng is not None:
            return eng, None
        return None, "out_of_range" if calibration.side(table, raw) else "nonfinite"
    except Exception:  # noqa: BLE001  EX-11: a calibration never raises into the frame path
        return None, "error"


def compute(mdb: MissionDb, frame: FrameHeader) -> Computed:
    raws: dict[str, float | None] = {n: getattr(frame, n) for n in mdb.raw_names}
    values: dict[str, float | None] = dict(raws)
    nulls: list[tuple[str, str]] = []
    for name, table in mdb.calibrations.items():
        raw = raws[name]
        if raw is None:  # a missing value is NULL already (D-ING-20), not a calibration failure
            continue
        values[name], why = _calibrated(table, raw)
        if why:
            nulls.append((name, why))
    values.update(dict.fromkeys(mdb.derived_names))  # ponytail: derived evaluate in Task 6
    return Computed(values, raws, tuple(nulls))
