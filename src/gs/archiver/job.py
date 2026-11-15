"""Archive one vehicle-hour: write, read back, compare bytes and rows, only then record it."""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from minio import Minio

from gs.archiver.hours import hour_bounds, object_key, sidecar
from gs.archiver.parquet import decode, encode, hot_schema, hot_table, same_rows
from gs.archiver.plan import ArchiveRow
from gs.archiver.s3 import get, put
from gs.common.hooks import Hooks

log = logging.getLogger(__name__)

UPSERT = """
insert into archive_object (vehicle, hour, sha256, rows, t_min, t_max, version, verified_at)
values (%s, %s, %s, %s, %s, %s, %s, now())
on conflict (vehicle, hour) do update set version = excluded.version, sha256 = excluded.sha256,
  rows = excluded.rows, t_min = excluded.t_min, t_max = excluded.t_max,
  verified_at = excluded.verified_at, last_checked_at = null
returning vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,
  last_checked_at
"""


@dataclass
class ArchiverCtx:
    s3: Minio
    bucket: str
    hooks: Hooks
    retention_h: float
    refused: set[tuple[str, datetime]] = field(
        default_factory=set
    )  # hours whose hot delete was refused


def _fail(vehicle: str, hour: datetime, why: str) -> None:
    log.error("archive of %s %s not verified: %s", vehicle, hour, why, extra={"vehicle": vehicle})


async def archive_hour(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], vehicle: str, hour: datetime,
    version: int = 0,
) -> ArchiveRow | None:  # fmt: skip
    """The archive_object row is written only after the stored bytes hash to what was sent AND the
    decoded rows equal a second read of the hot rows. Anything else: log, no row, None. S3 errors
    propagate (the pass treats them as MinIO trouble and deletes nothing)."""
    w = hour_bounds(vehicle, hour)
    schema = await hot_schema(conn)
    table = await hot_table(conn, w, schema)
    if table.num_rows == 0:
        return None
    key = object_key(vehicle, w.start, version)
    sha = await put(ctx.s3, ctx.bucket, key, encode(table))
    ctx.hooks.fire("archive:after_put")

    stored = await get(ctx.s3, ctx.bucket, key)
    if hashlib.sha256(stored).hexdigest() != sha:
        _fail(vehicle, w.start, f"{key} re-read does not hash to {sha}")
        return None
    if not same_rows(decode(stored), await hot_table(conn, w, schema)):
        _fail(vehicle, w.start, f"{key} rows differ from the hot rows")
        return None
    ctx.hooks.fire("archive:after_reread")

    name, body = sidecar(key, sha)
    await put(ctx.s3, ctx.bucket, name, body)
    t_min, t_max = table["t_onboard"][0].as_py(), table["t_onboard"][-1].as_py()  # sorted
    cur = await conn.execute(UPSERT, (vehicle, w.start, sha, table.num_rows, t_min, t_max, version))
    row = await cur.fetchone()
    assert row is not None
    return ArchiveRow(*row)
