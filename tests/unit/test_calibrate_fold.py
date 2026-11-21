"""Calibration in the frame path: compute() (D-EXT-04)."""

from datetime import UTC, datetime

from gs.common.interfaces import FrameHeader, build_frame_model
from gs.ingest.compute import compute
from tests.support.mission import calibrated

T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
MDB = calibrated()  # bus_current_a: raw 0..10 -> eng 0..100


def frame(raw: float | None, mdb=MDB) -> FrameHeader:  # type: ignore[no-untyped-def]
    vals = {p: 1.0 for p in mdb.raw_names} | {"bus_current_a": raw}
    return build_frame_model(mdb.raw_names)(
        vehicle="NYX-TA1", seq=0, t_onboard=T0, mode="TEST", last_cmd_id=None, **vals
    )


def test_engineering_values_fed_to_engine() -> None:
    c = compute(MDB, frame(5.0))
    assert c.values["bus_current_a"] == 50.0 and c.raws["bus_current_a"] == 5.0
    assert c.values["cabin_temp_c"] == 1.0 == c.raws["cabin_temp_c"]  # uncalibrated: pass-through
    assert c.nulls == ()


def test_out_of_table_raw_is_null_not_held() -> None:
    c = compute(MDB, frame(11.0))
    assert c.values["bus_current_a"] is None and c.raws["bus_current_a"] == 11.0
    assert c.nulls == (("bus_current_a", "out_of_range"),)


def test_missing_raw_is_null_without_a_reason() -> None:
    c = compute(MDB, frame(None))
    assert c.values["bus_current_a"] is None and c.nulls == ()


def test_overflow_is_null_with_a_reason() -> None:
    m = calibrated("[[0, -1.7e+308], [10, 1.7e+308]]")
    c = compute(m, frame(5.0))
    assert c.values["bus_current_a"] is None and c.nulls == (("bus_current_a", "nonfinite"),)


def test_calibration_exception_is_null_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(*_: object) -> float:
        raise RuntimeError("x")

    monkeypatch.setattr("gs.ingest.compute.calibration.apply", boom)
    c = compute(MDB, frame(5.0))
    assert c.values["bus_current_a"] is None and c.nulls == (("bus_current_a", "error"),)
