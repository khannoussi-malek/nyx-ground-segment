"""Run N statistics: stdlib nearest-rank percentiles, warmup exclusion, CSV with iso_z times
(D-PERF-03)."""

import csv
import math
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

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
