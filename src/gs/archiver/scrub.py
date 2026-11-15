"""Rolling scrub (D-ARC-11): re-hash the K least recently checked objects; detect, never repair."""

import hashlib
from typing import Any

import psycopg
from minio.error import S3Error

from gs.archiver.hours import object_key
from gs.archiver.job import ArchiverCtx
from gs.archiver.s3 import get
from gs.common.time import iso_z
from gs.ingest.events import GroundEvent, insert_events

SCRUB_K = 20
_PICK = (
    "select vehicle, hour, sha256, version from archive_object"
    " order by last_checked_at nulls first, hour limit %s"
)


def corrupt_key(vehicle: str, hour: Any, version: int) -> str:
    """T4-d: version 0 keeps the design key, later versions get a /v<N> suffix."""
    base = f"ARCHIVE_CORRUPT/{vehicle}/{iso_z(hour)}"
    return base if version == 0 else f"{base}/v{version}"


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
    for vehicle, hour, expected, version in await cur.fetchall():
        found = await _found(ctx, vehicle, hour, version)
        if found != expected:
            await insert_events(conn, [GroundEvent(
                corrupt_key(vehicle, hour, version), vehicle, "ARCHIVE_CORRUPT", hour,
                {"hour": hour, "version": version, "expected": expected, "found": found},
            )])  # fmt: skip
        await conn.execute(
            "update archive_object set last_checked_at = clock_timestamp()"
            " where vehicle = %s and hour = %s", (vehicle, hour),
        )  # fmt: skip
    return cur.rowcount


async def unresolved_corrupt(conn: psycopg.AsyncConnection[Any], vehicle: str | None = None) -> int:
    """ARCHIVE_CORRUPT events whose `expected` is still the row's current sha256 (T4-d)."""
    cur = await conn.execute(
        "select count(*) from events e join archive_object a on a.vehicle = e.vehicle"
        " and a.hour = (e.details->>'hour')::timestamptz"
        " and a.sha256 = e.details->>'expected'"
        " where e.kind = 'ARCHIVE_CORRUPT' and (%s::text is null or e.vehicle = %s)",
        (vehicle, vehicle),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])
