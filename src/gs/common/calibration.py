"""Table calibration, pure (D-EXT-04). Outside the table is out of range, never extrapolated."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Table:
    points: tuple[tuple[float, float], ...]

    @staticmethod
    def from_pairs(pairs: Sequence[Sequence[float]]) -> Table:
        if any(len(p) != 2 for p in pairs):
            raise ValueError("every calibration row needs exactly 2 values [raw, eng]")
        pts = tuple((float(p[0]), float(p[1])) for p in pairs)
        if len(pts) < 2:
            raise ValueError("a calibration table needs at least 2 points")
        if not all(math.isfinite(x) for p in pts for x in p):
            raise ValueError("calibration points must be finite")
        if any(a[0] >= b[0] for a, b in zip(pts, pts[1:], strict=False)):
            raise ValueError("calibration raw values must be strictly increasing")
        return Table(pts)


def side(t: Table, raw: float | None) -> Literal["LOW", "HIGH"] | None:
    """LOW below / HIGH above the table; None inside (ends included) or for None."""
    if raw is None:
        return None
    if math.isnan(raw) or raw < t.points[0][0]:
        return "LOW"
    return "HIGH" if raw > t.points[-1][0] else None


def apply(t: Table, raw: float | None) -> float | None:
    """Engineering value; None for a None, non-finite or out-of-table raw, or an overflow."""
    if raw is None or not math.isfinite(raw) or side(t, raw) is not None:
        return None
    pts = t.points
    i = bisect_right(pts, (raw, math.inf)) - 1  # last point with raw0 <= raw
    r0, e0 = pts[i]
    if raw == r0:
        return e0
    r1, e1 = pts[i + 1]
    eng = e0 + (raw - r0) * (e1 - e0) / (r1 - r0)
    return eng if math.isfinite(eng) else None  # an overflow is a NULL, never a stored inf


def eng_range(t: Table) -> tuple[float, float]:
    es = [e for _, e in t.points]
    return min(es), max(es)
