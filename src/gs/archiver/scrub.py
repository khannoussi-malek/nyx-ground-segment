"""Rolling scrub (D-ARC-11): re-hash the K least recently checked objects; detect, never repair."""

import hashlib
from typing import Any

import psycopg
from minio.error import S3Error

from gs.archiver.hours import object_key
from gs.archiver.job import ArchiverCtx, corrupt_key, flag_corrupt  # noqa: F401  (re-export)
from gs.archiver.plan import ArchiveRow, Key
from gs.archiver.s3 import get

SCRUB_K = 20
_PICK = (
    "select vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,"
    " last_checked_at from archive_object order by last_checked_at nulls first, hour limit %s"
)


async def _found(ctx: ArchiverCtx, vehicle: str, hour: Any, version: int) -> str | None:
    """sha256 of the stored object; None when it is gone. Other S3 trouble raises."""
    try:
        return hashlib.sha256(
            await get(ctx.s3, ctx.bucket, object_key(vehicle, hour, version))
        ).hexdigest()
    except S3Error as e:
        if e.code == "NoSuchKey":
            return None
        raise


async def scrub_pass(ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], k: int = SCRUB_K) -> int:
    """Check up to `k` objects, oldest `last_checked_at` first; returns how many were checked. S3
    trouble raises with that row's `last_checked_at` untouched."""
    cur = await conn.execute(_PICK, (k,))
    for r in await cur.fetchall():
        row = ArchiveRow(*r)
        found = await _found(ctx, row.vehicle, row.hour, row.version)
        if found != row.sha256:
            await flag_corrupt(conn, row, found)
        await conn.execute(
            "update archive_object set last_checked_at = clock_timestamp()"
            " where vehicle = %s and hour = %s", (row.vehicle, row.hour),
        )  # fmt: skip
    return cur.rowcount


_UNRESOLVED = (
    "from events e join archive_object a on a.vehicle = e.vehicle"
    " and a.hour = (e.details->>'hour')::timestamptz and a.sha256 = e.details->>'expected'"
    " and a.version = coalesce((e.details->>'version')::int, 0)"
    " where e.kind = 'ARCHIVE_CORRUPT'"
)


async def unresolved_corrupt(conn: psycopg.AsyncConnection[Any], vehicle: str | None = None) -> int:
    """ARCHIVE_CORRUPT events whose `expected` is still the row's current sha256 (T4-d) and
    version: a rebuilt version has the same bytes when only the object was damaged."""
    cur = await conn.execute(
        "select count(*) " + _UNRESOLVED + " and (%s::text is null or e.vehicle = %s)",
        (vehicle, vehicle),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def unresolved_corrupt_keys(conn: psycopg.AsyncConnection[Any]) -> frozenset[Key]:
    cur = await conn.execute("select distinct a.vehicle, a.hour " + _UNRESOLVED)
    return frozenset((v, h) for v, h in await cur.fetchall())
