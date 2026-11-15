import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from perf.stats import (
    after_warmup,
    ci_ranks,
    ci_text,
    nearest_rank,
    p95,
    p95_ci,
    pooled,
    verdict_of,
    write_csv,
)

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def test_nearest_rank_p95() -> None:
    assert p95(list(range(1, 101))) == 95 and nearest_rank([5.0], 0.95) == 5.0
    assert nearest_rank([3.0, 1.0, 2.0], 0.5) == 2.0  # rank ceil(0.5 * 3) = 2 of the sorted list
    assert nearest_rank([1.0, 2.0], 0.0) == 1.0  # rank is at least 1
    assert nearest_rank(list(range(1, 61)), 0.95) == 57  # ceil(0.95 * 60) = 57


def test_nearest_rank_rejects_empty_and_bad_p() -> None:
    with pytest.raises(ValueError, match="no samples"):
        p95([])
    with pytest.raises(ValueError, match="0..1"):
        nearest_rank([1.0], 1.5)


def test_warmup_excluded() -> None:
    rows = [(T0 + timedelta(seconds=s), float(s)) for s in (0, 9, 10, 11, 600)]
    kept = after_warmup(rows, T0)
    assert [v for _, v in kept] == [10.0, 11.0, 600.0]  # t0 + 10 s itself is kept
    assert len(rows) - len(kept) == 2  # the caller reports how many were excluded
    assert after_warmup(rows, T0, warmup_s=0.0) == rows


def test_csv_times_end_in_z(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    write_csv(path, ["t", "ms", "note"], [(T0, 1.5, None), (T0 + timedelta(seconds=1), 2, "a,b")])
    rows = list(csv.reader(path.open()))
    assert rows[0] == ["t", "ms", "note"]
    assert rows[1] == ["2026-11-15T12:00:00.000000Z", "1.5", ""]
    assert rows[2][0].endswith("Z") and rows[2][2] == "a,b"  # quoted by the csv module


def test_p95_ci_ranks() -> None:
    assert ci_ranks(120) == (109, 120) and ci_ranks(60) is None
    assert ci_ranks(119) is None  # the design value: a two-sided CI only from n = 120
    assert ci_ranks(1000) is not None


def test_p95_ci_values_are_the_ranked_samples() -> None:
    assert p95_ci([float(i) for i in range(1, 121)]) == (109.0, 120.0)
    assert p95_ci([1.0] * 60) is None


def test_pooled_not_averaged() -> None:
    s = {"NYX-TA1": [1.0] * 100 + [10.0] * 20, "NYX-TA2": [1.0] * 120}
    assert len(pooled(s)) == 240
    assert p95(pooled(s)) != (p95(s["NYX-TA1"]) + p95(s["NYX-TA2"])) / 2


def test_marginal_when_ci_straddles() -> None:
    xs = [float(i) for i in range(1, 121)]  # p95 = 114, CI = (109, 120)
    assert (v := verdict_of(xs, 115.0)).passed and v.marginal and v.ci == (109.0, 120.0)
    assert not verdict_of(xs, 200.0).marginal and verdict_of(xs, 200.0).passed
    assert not verdict_of(xs, 100.0).passed and not verdict_of(xs, 100.0).marginal


def test_limit_is_strict_and_small_n_is_never_marginal() -> None:
    assert not verdict_of([5.0] * 120, 5.0).passed
    v = verdict_of([1.0] * 60, 1.0)
    assert v.ci is None and not v.marginal
    assert ci_text(v) == "n too small for a two-sided CI"
    assert "109" in ci_text(verdict_of([float(i) for i in range(1, 121)], 115.0))
