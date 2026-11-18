"""The archiver's /healthz verdict (pure): D-ARC-10, T4-q."""

from datetime import datetime, timedelta

from gs.common.health import State
from gs.common.time import iso_z

BACKLOG_H = 72


def _ok(up: bool) -> str:
    return "ok" if up else "down"


def archiver_status(
    now: datetime,
    db_ok: bool,
    redis_ok: bool,
    s3_ok: bool,
    oldest_unarchived: datetime | None,
    corrupt: int,
    refused: int,
    last_pass: datetime | None,
    backlog_h: float = BACKLOG_H,
    last_snapshot: datetime | None = None,
    snapshot_every_h: float = 24,
) -> tuple[State, dict[str, object]]:
    """down: the database is unreachable; degraded: Redis or MinIO unreachable, a backlog older
    than `backlog_h`, an unresolved ARCHIVE_CORRUPT, a refused hot delete or a state snapshot
    older than twice `snapshot_every_h` (D-ARC-12)."""
    checks: dict[str, object] = {
        "oldest_unarchived_hour": None if oldest_unarchived is None else iso_z(oldest_unarchived),
        "scrub": "ok" if corrupt == 0 else f"{corrupt} corrupt",
        "delete_refused": refused,
        "db": _ok(db_ok),
        "redis": _ok(redis_ok),
        "minio": _ok(s3_ok),
        "last_pass": None if last_pass is None else iso_z(last_pass),
        "last_state_snapshot": None if last_snapshot is None else iso_z(last_snapshot),
    }
    if not db_ok:
        return "down", checks
    behind = oldest_unarchived is not None and now - oldest_unarchived > timedelta(hours=backlog_h)
    # no snapshot: fine until the first pass has run (it takes one at once), a fault after it
    stale = (last_pass is not None and last_snapshot is None) or (
        last_snapshot is not None and now - last_snapshot > timedelta(hours=2 * snapshot_every_h)
    )
    bad = not redis_ok or not s3_ok or behind or corrupt > 0 or refused > 0 or stale
    return ("degraded" if bad else "ok"), checks
