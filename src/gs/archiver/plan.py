"""The archiver pass planner: what to do this loop from hot counts and archive rows (pure)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from gs.archiver.hours import delete_due, eligible

Key = tuple[str, datetime]


@dataclass(frozen=True)
class ArchiveRow:
    vehicle: str
    hour: datetime
    sha256: str
    rows: int
    t_min: datetime
    t_max: datetime
    version: int
    verified_at: datetime
    hot_deleted_at: datetime | None
    last_checked_at: datetime | None


@dataclass(frozen=True)
class Archive:
    vehicle: str
    hour: datetime
    version: int


@dataclass(frozen=True)
class MergeLate:
    vehicle: str
    hour: datetime
    version: int


@dataclass(frozen=True)
class DeleteHot:
    vehicle: str
    hour: datetime


@dataclass(frozen=True)
class Anomaly:
    vehicle: str
    hour: datetime
    detail: str


Action = Archive | MergeLate | DeleteHot | Anomaly


def _order(k: Key) -> tuple[datetime, str]:
    return k[1], k[0]


def plan_pass(
    now: datetime,
    counts: Mapping[Key, int],
    rows: Mapping[Key, ArchiveRow],
    retention_h: float,
) -> list[Action]:
    """Archive/MergeLate first (oldest hour first), then DeleteHot oldest first, then anomalies."""
    build: list[Action] = []
    delete: list[Action] = []
    odd: list[Action] = []
    for key in sorted(set(counts) | set(rows), key=_order):
        v, h = key
        n = counts.get(key, 0)
        row = rows.get(key)
        if row is None:
            if n > 0 and eligible(h, now):
                build.append(Archive(v, h, 0))
        elif row.hot_deleted_at is not None:
            if n > 0:
                build.append(MergeLate(v, h, row.version + 1))
        elif n > row.rows:
            build.append(Archive(v, h, row.version + 1))
        elif n < row.rows:
            odd.append(Anomaly(v, h, f"{n} hot rows, archived {row.rows}"))
        elif delete_due(h, now, retention_h):
            delete.append(DeleteHot(v, h))
    return [*build, *delete, *odd]


def oldest_unarchived(
    now: datetime, counts: Mapping[Key, int], rows: Mapping[Key, ArchiveRow]
) -> datetime | None:
    """Oldest eligible hour that has hot rows but no archive row (drives /healthz)."""
    hours = [h for (v, h), n in counts.items() if n > 0 and (v, h) not in rows and eligible(h, now)]
    return min(hours, default=None)
