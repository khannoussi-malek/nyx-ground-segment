"""Run N statistics: stdlib nearest-rank percentiles, warmup exclusion, CSV with iso_z times
(D-PERF-03)."""

import csv
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from gs.common.time import iso_z


def nearest_rank(xs: Sequence[float], p: float) -> float:
    """The value at rank ceil(p * n) of the sorted samples (rank at least 1)."""
    if not xs:
        raise ValueError("no samples")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in 0..1")
    return sorted(xs)[max(math.ceil(p * len(xs)), 1) - 1]


def p95(xs: Sequence[float]) -> float:
    return nearest_rank(xs, 0.95)


CI_MIN_N, Z95 = 120, 1.96  # D-PERF-10: a two-sided CI only from n = 120


def ci_ranks(n: int, p: float = 0.95) -> tuple[int, int] | None:
    """Le Boudec ranks floor(np - z sqrt(np(1-p))) .. ceil(1 + np + z sqrt(np(1-p))), 1-based;
    None when n < 120 or they fall outside 1..n."""
    half = Z95 * math.sqrt(n * p * (1 - p))
    lo, hi = math.floor(n * p - half), math.ceil(1 + n * p + half)
    return (lo, hi) if n >= CI_MIN_N and lo >= 1 and hi <= n else None


def p95_ci(xs: Sequence[float]) -> tuple[float, float] | None:
    r = ci_ranks(len(xs))
    if r is None:
        return None
    s = sorted(xs)
    return s[r[0] - 1], s[r[1] - 1]


def pooled(samples: Mapping[str, Sequence[float]]) -> list[float]:
    """Raw concatenation across vehicles and runs; never an average of per-series p95s.
    Pass only runs from the same configuration (rate, duration, faults)."""
    return [x for xs in samples.values() for x in xs]


class Verdict(NamedTuple):
    p95: float
    ci: tuple[float, float] | None
    limit: float
    passed: bool  # the point p95 is under the limit
    marginal: bool  # the limit lies inside the CI: the sample cannot settle it


def verdict_of(xs: Sequence[float], limit: float) -> Verdict:
    point, ci = p95(xs), p95_ci(xs)
    return Verdict(point, ci, limit, point < limit, ci is not None and ci[0] <= limit <= ci[1])


def ci_text(v: Verdict) -> str:
    return (
        "n too small for a two-sided CI"
        if v.ci is None
        else f"95% CI [{v.ci[0]:.1f}, {v.ci[1]:.1f}]"
    )


def after_warmup[T: tuple[datetime, float]](
    rows: Sequence[T], t0: datetime, warmup_s: float = 10.0
) -> list[T]:
    """Rows (time first) from t0 + warmup_s on; the caller reports len(rows) - len(result)."""
    cut = t0 + timedelta(seconds=warmup_s)
    return [r for r in rows if r[0] >= cut]


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    """None is an empty field, datetimes are iso_z."""
    with path.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for row in rows:
            w.writerow("" if v is None else iso_z(v) if isinstance(v, datetime) else v for v in row)
