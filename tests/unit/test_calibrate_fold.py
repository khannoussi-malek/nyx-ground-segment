"""Calibration in the frame path: compute() (D-EXT-04)."""

from datetime import UTC, datetime, timedelta

from gs.alarms.contract import Level
from gs.alarms.engine import compile_set
from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.missiondb import MissionDb
from gs.ingest.compute import compute
from gs.ingest.fold import VehicleState, fold
from tests.support.mission import calibrated
from tests.support.recompute import _frame, empty_state, recompute_columns

T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
P = "bus_current_a"
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


def _fold(mdb: MissionDb, raws: list[float], vs: VehicleState | None = None) -> VehicleState:
    cset = compile_set(mdb)
    model = build_frame_model(mdb.raw_names)
    first = 0 if vs is None or vs.seq is None else vs.seq.last_seq + 1
    frames = [
        model(
            vehicle="NYX-TA1", seq=first + i, t_onboard=T0 + timedelta(seconds=(first + i) / 10),
            mode="TEST", last_cmd_id=None, **({p: 1.0 for p in mdb.raw_names} | {P: r}),
        )
        for i, r in enumerate(raws)
    ]  # fmt: skip
    return fold(vs or empty_state(cset), "NYX-TA1", frames, mdb, cset)[0]


def test_engine_sees_engineering_not_raw() -> None:
    vs = _fold(MDB, [5.0] * 4)  # raw 5 is nominal (limit red_high 10); engineering 50 is RED
    (a,) = [a for p, a in vs.engine.alarms.items() if p == P]
    assert a.severity == Level.RED and a.trigger_check == "limit"


def test_out_of_table_sample_is_a_null_for_the_limit_check() -> None:
    vs = _fold(MDB, [3.0, 3.0, 11.0])  # eng 30 twice (ring 2), then NULL: ring restarts
    assert vs.engine.slots[(P, "limit")].ring == ()
    assert vs.engine.slots[(P, "calibration_range")].ring == (
        Level.NOMINAL,
        Level.NOMINAL,
        Level.RED,
    )


def test_recompute_frame_carries_the_stored_raw_for_a_calibrated_parameter() -> None:
    row = {"vehicle": "NYX-TA1", "seq": 0, "t_onboard": T0, "mode": "TEST", "last_cmd_id": None}
    row |= {p: 1.0 for p in MDB.raw_names} | {P: 50.0, f"{P}_raw": 5.0}  # stored eng 50, raw 5
    f = _frame(row, MDB)
    assert getattr(f, P) == 5.0 and compute(MDB, f).values[P] == 50.0  # calibrated once
    old = row | {f"{P}_raw": None}  # history from before the column existed: the stored value
    assert getattr(_frame(old, MDB), P) == 50.0
    assert recompute_columns([MDB]) == [*MDB.param_names, f"{P}_raw"]
