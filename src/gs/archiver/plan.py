"""The archiver pass planner: what to do this loop from hot counts and archive rows (pure)."""

from collections.abc import Collection, Mapping
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
    corrupt: Collection[Key] = frozenset(),
) -> list[Action]:
    """Archive/MergeLate first (oldest hour first), then DeleteHot oldest first, then anomalies.
    `corrupt`: hours with an unresolved ARCHIVE_CORRUPT. One whose hot rows are all still here is
    rebuilt as the next version and never deleted; one that already lost its hot rows is left
    alone (nothing can rebuild it; the event stays open)."""
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
            if n > 0 and key not in corrupt:
                build.append(MergeLate(v, h, row.version + 1))
        elif n > row.rows:
            build.append(Archive(v, h, row.version + 1))
        elif n < row.rows:
            odd.append(Anomaly(v, h, f"{n} hot rows, archived {row.rows}"))
        elif key in corrupt:
            build.append(Archive(v, h, row.version + 1))
        elif delete_due(h, now, retention_h):
            delete.append(DeleteHot(v, h))
    return [*build, *delete, *odd]


def oldest_unarchived(
    now: datetime, counts: Mapping[Key, int], rows: Mapping[Key, ArchiveRow]
) -> datetime | None:
    """Oldest hour with hot rows the archive does not hold yet (drives /healthz): eligible with no
    row, or (G4-1) late rows pending: a row not hot-deleted with n > rows, or hot-deleted with
    n > 0, so a failing MergeLate shows as backlog."""
    pending = []
    for (v, h), n in counts.items():
        row = rows.get((v, h))
        if row is None:
            late = n > 0 and eligible(h, now)
        else:
            late = n > 0 if row.hot_deleted_at is not None else n > row.rows
        if late:
            pending.append(h)
    return min(pending, default=None)
