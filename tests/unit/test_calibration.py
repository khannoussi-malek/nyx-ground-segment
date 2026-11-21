"""Table calibration, pure (D-EXT-04)."""

import math

import pytest

from gs.common.calibration import Table, apply, eng_range, side

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
