"""Table calibration, pure (D-EXT-04)."""

import math

import pytest

from gs.common.calibration import Table, apply, eng_range, side
from gs.common.missiondb import MissionDbError
from tests.support.mission import files, ld

SHIPPED_P = files()["parameters"]
SHIPPED_L = files()["limits"]

T = Table.from_pairs([[0, 0], [10, 100], [20, 150]])


@pytest.mark.parametrize(
    ("raw", "eng"),
    [
        (0, 0.0),
        (10, 100.0),
        (20, 150.0),
        (5, 50.0),
        (15, 125.0),
        (-1e-9, None),
        (20.000000000000004, None),
        (math.nan, None),
        (math.inf, None),
        (-math.inf, None),
        (None, None),
    ],
)
def test_boundary_table(raw: float | None, eng: float | None) -> None:
    assert apply(T, raw) == eng


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (0, None),
        (20, None),
        (-0.1, "LOW"),
        (20.1, "HIGH"),
        (math.nan, "LOW"),
        (-math.inf, "LOW"),
        (math.inf, "HIGH"),
        (None, None),
    ],
)
def test_side(raw: float | None, want: str | None) -> None:
    assert side(T, raw) == want


@pytest.mark.parametrize(
    "pairs",
    [
        [[0, 0]],
        [[0, 0], [0, 1]],
        [[1, 0], [0, 1]],
        [[0, 0], [1, math.nan]],
        [[0, 0], [math.inf, 1]],
    ],
)
def test_table_rejected(pairs: list[list[float]]) -> None:
    with pytest.raises(ValueError):
        Table.from_pairs(pairs)


def test_eng_range_decreasing_table() -> None:
    assert eng_range(Table.from_pairs([[0, 5], [1, 1]])) == (1.0, 5.0)


def test_table_row_with_fewer_than_two_values_is_value_error() -> None:
    with pytest.raises(ValueError, match="2 values"):
        Table.from_pairs([[0], [1, 2]])


TEMP = b"  cabin_temp_c: {unit: degC, type: float, description: Cabin temperature}\n"


def _cal(table: str) -> dict[str, bytes]:
    new = (
        b"  cabin_temp_c: {unit: degC, type: float, calibration: {table: "
        + table.encode()
        + b"}}\n"
    )
    return {"parameters": SHIPPED_P.replace(TEMP, new)}


def test_table_must_cover_red_limits_plus_deadband() -> None:  # D-EXT-04, red 15..30, deadband 0.45
    for bad in ("[[0, 15], [10, 30]]", "[[0, 14.8], [10, 30.2]]"):
        with pytest.raises(MissionDbError, match="cabin_temp_c.*cover"):
            ld(files(**_cal(bad)))
    db = ld(files(**_cal("[[0, 14], [10, 31]]")))
    assert db.raw_columns == ("cabin_temp_c_raw",)
    assert db.calibrations["cabin_temp_c"].points == ((0, 14), (10, 31))


def test_table_must_cover_mode_checks_too() -> None:
    lim = SHIPPED_L.replace(
        b"  cabin_temp_c: {checks: [",
        b"  cabin_temp_c: {checks_by_mode: {SAFE: [{type: limit, red_high: 40.0,"
        b" hysteresis_abs: 1}]},"
        b" checks: [",
    )
    with pytest.raises(MissionDbError, match="cover"):
        ld(files(limits=lim, **_cal("[[0, 14], [10, 31]]")))


def test_calibration_row_too_short_rejected_at_load() -> None:
    with pytest.raises(MissionDbError, match="2 values"):
        ld(files(**_cal("[[0], [10, 31]]")))


def test_overflowing_output_is_none_not_inf() -> None:
    t = Table.from_pairs([[0, 0], [1, 1.7e308], [2, -1.7e308]])
    assert apply(t, 1.5) is None  # e1 - e0 overflows to -inf, so the output would be -inf
