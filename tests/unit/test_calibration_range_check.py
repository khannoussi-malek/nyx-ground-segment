"""The built-in calibration_range check (D-EXT-04): RED when the raw value is off its table."""

from collections.abc import Sequence

import pytest

from gs.alarms.contract import Level, Transition
from gs.alarms.engine import compile_set, initial_state, step
from gs.common.calibration import apply
from gs.common.missiondb import MissionDb, MissionDbError
from tests.support.alarms import EPOCH, PERIOD_S, V, at
from tests.support.mission import calibrated, files, ld

P = "bus_current_a"
TABLE = "[[0, 0], [20, 20]]"  # identity on 0..20; limits yellow_high 8, red_high 10 (persistence 3)


def feed(mdb: MissionDb, raws: Sequence[float]) -> list[Transition]:
    cset = compile_set(mdb)
    state, out = initial_state(cset), list[Transition]()
    for i, raw in enumerate(raws):
        values = {P: apply(mdb.calibrations[P], raw)}  # engineering, as compute() gives it
        state, trs = step(
            state, cset, V, EPOCH, i, at(i * PERIOD_S), PERIOD_S, values, False, {P: raw}
        )
        out += trs
    return out


def test_red_after_n_frames_and_clear_after_n() -> None:
    trs = feed(calibrated(TABLE), [5, -1, -1, -1, 5, 5, 5])
    assert [(t.kind, t.severity, t.level, t.check_kind, t.trigger_seq) for t in trs] == [
        ("RAISE", Level.RED, "RED_LOW", "calibration_range", 3),
        ("RTN", Level.NOMINAL, None, None, 6),
    ]
    assert len({t.alarm_id for t in trs}) == 1


def test_two_off_table_samples_do_not_alarm() -> None:
    assert feed(calibrated(TABLE), [5, 25, 25, 5, 25, 25]) == []


def test_shares_the_one_alarm_with_a_limit_check() -> None:
    trs = feed(calibrated(TABLE), [9, 9, 9, 25, 25, 25])  # limit YELLOW, then raw above the table
    assert [(t.kind, t.severity, t.level, t.check_kind) for t in trs] == [
        ("RAISE", Level.YELLOW, "YELLOW_HIGH", "limit"),
        ("ESCALATE", Level.RED, "RED_HIGH", "calibration_range"),
    ]
    assert len({t.alarm_id for t in trs}) == 1


def test_no_limits_still_alarms() -> None:
    trs = feed(calibrated(TABLE, no_limits=True), [25, 25, 25])
    assert [(t.kind, t.level) for t in trs] == [("RAISE", "RED_HIGH")]


def test_raw_missing_is_a_null_not_classified() -> None:
    mdb = calibrated(TABLE)
    cset = compile_set(mdb)
    s = initial_state(cset)
    for i in range(6):
        s, trs = step(s, cset, V, EPOCH, i, at(i * PERIOD_S), PERIOD_S, {P: None}, False, {P: None})
        assert trs == ()


def test_limits_yaml_cannot_name_the_built_in_check() -> None:
    lim = files()["limits"].decode()
    row = next(x for x in lim.splitlines() if x.startswith("  bus_current_a:"))
    bad = row.replace(
        "{type: limit,", "{type: calibration_range, table: [[0, 0], [1, 1]]}, {type: limit,"
    )
    with pytest.raises(MissionDbError, match="unknown check type 'calibration_range'"):
        ld(files(limits=lim.replace(row, bad).encode()))
