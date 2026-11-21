"""Raw frame values -> engineering values (D-EXT-04): calibrate, pass uncalibrated through,
then `expr` derived parameters (D-EXT-05). Pure; never raises into the frame path."""

from collections.abc import Mapping
from dataclasses import dataclass

from gs.alarms.checks.roc import RocState, push, slope
from gs.common import calibration
from gs.common.interfaces import FrameHeader
from gs.common.missiondb import DerivedSpec, MissionDb
from gs.ingest.seq import NOMINAL_PERIOD_S


@dataclass(frozen=True)
class Computed:
    values: Mapping[str, float | None]  # engineering, every param_names entry
    raws: Mapping[str, float | None]  # the frame's values, every raw name
    nulls: tuple[
        tuple[str, str], ...
    ]  # (param, reason): calibration or derived (missing, div0, ...)


def _calibrated(table: calibration.Table, raw: float) -> tuple[float | None, str | None]:
    try:
        eng = calibration.apply(table, raw)
        if eng is not None:
            return eng, None
        return None, "out_of_range" if calibration.side(table, raw) else "nonfinite"
    except Exception:  # noqa: BLE001  EX-11: a calibration never raises into the frame path
        return None, "error"


def _rate(
    spec: DerivedSpec,
    w: RocState,
    t: float,
    x: float | None,
    late: bool,
    reset: bool,
    period_s: float,
) -> tuple[float | None, str | None, RocState]:
    """(value, NULL reason, window after) of one rate_of parameter. `slope` is the ROC check's."""
    assert spec.window_s is not None
    if late:  # a stored late frame is no sample of the stream: NULL, window untouched (EX-13)
        return None, "late", w
    if x is None:  # NULL input resets the window, never the last value (R23 M4)
        return None, "missing", RocState((), ())
    w = push(RocState((), ()) if reset else w, t, x, spec.window_s)
    try:
        v = slope(w, spec.window_s, period_s)
    except Exception:  # noqa: BLE001  EX-11: a derived value never raises into the frame path
        return None, "error", w
    return v, None if v is not None else "samples", w


def compute(
    mdb: MissionDb,
    frame: FrameHeader,
    windows: Mapping[str, RocState] | None = None,
    late: bool = False,
    *,
    reset: bool = False,
    period_s: float = NOMINAL_PERIOD_S,
) -> tuple[Computed, Mapping[str, RocState]]:
    """`windows`: the rate_of windows by derived name (input untouched); `late`: a stored
    DUP_OR_LATE frame; `reset`: GAP, SEQ_RESET or data-STALE, as for the ROC check."""
    raws: dict[str, float | None] = {n: getattr(frame, n) for n in mdb.raw_names}
    values: dict[str, float | None] = dict(raws)
    nulls: list[tuple[str, str]] = []
    new: dict[str, RocState] = {}
    for name, table in mdb.calibrations.items():
        raw = raws[name]
        if raw is None:  # a missing value is NULL already (D-ING-20), not a calibration failure
            continue
        values[name], why = _calibrated(table, raw)
        if why:
            nulls.append((name, why))
    for name, spec in mdb.derived.items():  # file order: a derived may use an earlier one
        values[name], why = None, None
        if spec.expr:
            values[name], why = spec.expr.eval(values)
        elif spec.rate_of:
            w = (windows or {}).get(name, RocState((), ()))
            t = frame.t_onboard.timestamp()
            values[name], why, new[name] = _rate(
                spec, w, t, values.get(spec.rate_of), late, reset, period_s
            )
        if why:  # NULL input, never the last value (R23 M4)
            nulls.append((name, why))
    return Computed(values, raws, tuple(nulls)), new
