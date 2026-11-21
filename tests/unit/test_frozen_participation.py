"""FROZEN runs over the raw values only (D-EXT-09)."""

from datetime import UTC, datetime, timedelta

from gs.alarms.engine import compile_set
from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.missiondb import MissionDb
from gs.ingest.fold import VehicleState, fold
from tests.support.mission import calibrated, files, ld
from tests.support.recompute import empty_state

T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
P = "cabin_temp_c"  # calibrated here, limits yellow 18..27, red 15..30


def frames(mdb: MissionDb, first: int, n: int, raw: float = 20.0) -> list[FrameHeader]:
    model = build_frame_model(mdb.raw_names)
    return [
        model(
            vehicle="NYX-TA1", seq=s, t_onboard=T0 + timedelta(seconds=s / 10), mode="TEST",
            last_cmd_id=None, **({p: 1.0 for p in mdb.raw_names} | {P: raw}),
        )
        for s in range(first, first + n)
    ]  # fmt: skip


def kinds(mdb: MissionDb, fs: list[FrameHeader], vs: VehicleState | None = None) -> list[str]:
    cset = compile_set(mdb)
    _, out = fold(vs or empty_state(cset), "NYX-TA1", fs, mdb, cset)
    return [e.kind for e in out.events]


def test_freeze_detected_on_raw() -> None:
    mdb = calibrated("[[0, 0], [40, 40]]", P)  # a calibrated parameter, still frozen on raw
    assert kinds(mdb, frames(mdb, 0, 25)).count("FROZEN") == 1


def test_calibration_change_does_not_flap_frozen() -> None:
    a, b = calibrated("[[0, 0], [40, 40]]", P), calibrated("[[0, 1], [40, 41]]", P)
    ca, cb = compile_set(a), compile_set(b)
    vs, _ = fold(empty_state(ca), "NYX-TA1", frames(a, 0, 15), a, ca)  # 1.4 s, no FROZEN yet
    _, out = fold(vs, "NYX-TA1", frames(b, 15, 10), b, cb, ca)  # same raw under another table
    assert [e.kind for e in out.events].count("FROZEN") == 1  # the run was not restarted


def test_derived_excluded() -> None:
    extra = "derived:\n  twice: {unit: degC, expr: cabin_temp_c * 2}\n"
    mdb = ld(files(parameters=files()["parameters"] + extra.encode()))
    cset = compile_set(mdb)
    vs, _ = fold(empty_state(cset), "NYX-TA1", frames(mdb, 0, 2), mdb, cset)
    assert vs.frozen.values is not None and len(vs.frozen.values) == len(mdb.raw_names)
