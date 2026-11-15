"""Archive one vehicle-hour: write, read back, compare bytes and rows, only then record it."""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
import pyarrow as pa
from minio import Minio

from gs.archiver.hours import HourWindow, hour_bounds, object_key, sidecar
from gs.archiver.parquet import (
    FINGERPRINT_SQL,
    decode,
    encode,
    fingerprint,
    hot_schema,
    hot_table,
    keys,
    same_rows,
    sort_rows,
)
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
    if version >= 1 and not await _supersedes_recorded(ctx, conn, vehicle, w.start, table):
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


async def _recorded(
    conn: psycopg.AsyncConnection[Any], vehicle: str, hour: datetime
) -> ArchiveRow | None:
    cur = await conn.execute(
        "select vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,"
        " last_checked_at from archive_object where vehicle = %s and hour = %s",
        (vehicle, hour),
    )
    row = await cur.fetchone()
    return ArchiveRow(*row) if row else None


async def _recorded_table(ctx: ArchiverCtx, row: ArchiveRow) -> pa.Table | None:
    """The recorded object, decoded, only if its bytes still hash to the recorded sha256."""
    data = await get(ctx.s3, ctx.bucket, object_key(row.vehicle, row.hour, row.version))
    if hashlib.sha256(data).hexdigest() != row.sha256:
        _fail(row.vehicle, row.hour, f"recorded object does not hash to {row.sha256}")
        return None
    return decode(data)


async def _supersedes_recorded(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], vehicle: str, hour: datetime,
    table: pa.Table,
) -> bool:  # fmt: skip
    """A new version must contain every row of the recorded one (hot rows are a superset only if
    nothing was lost since). Containing the old key set implies min <= old t_min, max >= old t_max
    and rows >= old rows."""
    old = await _recorded(conn, vehicle, hour)
    if old is None:
        return True
    prev = await _recorded_table(ctx, old)
    if prev is None or not set(keys(prev)) <= set(keys(table)):
        _fail(vehicle, hour, "the new version would drop rows of the recorded version")
        return False
    return True


def _refuse(ctx: ArchiverCtx, vehicle: str, hour: datetime, why: str) -> bool:
    ctx.refused.add((vehicle, hour))
    log.error("hot delete of %s %s refused: %s", vehicle, hour, why, extra={"vehicle": vehicle})
    return False


async def _delete_window(
    conn: psycopg.AsyncConnection[Any], w: HourWindow
) -> tuple[int, datetime | None, datetime | None, str]:
    """DELETE the window's rows; what was deleted: count, min, max, fingerprint."""
    cur = await conn.execute(
        "with d as (delete from telemetry where " + HourWindow.SQL + " returning t_onboard, seq)"
        " select count(*), min(t_onboard), max(t_onboard), " + FINGERPRINT_SQL + " from d",
        w.params(),
    )
    got = await cur.fetchone()
    assert got is not None
    return int(got[0]), got[1], got[2], str(got[3])


async def delete_hot(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], row: ArchiveRow, w: HourWindow
) -> bool:
    """Delete the hour's hot rows only if exactly the archived rows go: the recorded object must
    still hash right, and inside the delete transaction the deleted rows' count, min, max AND
    (t_onboard, seq) fingerprint must equal the archive row and the archived object. Else roll
    back, log, refuse."""
    key = (row.vehicle, row.hour)
    if (w.vehicle, w.start) != key:
        return _refuse(ctx, *key, f"window {w.vehicle} {w.start} is not the archived hour")
    archived = await _recorded_table(ctx, row)  # S3 trouble raises here, before any delete
    if archived is None:
        return _refuse(ctx, *key, "the archived object is not the recorded one")
    if archived.num_rows != row.rows:
        return _refuse(ctx, *key, f"object has {archived.num_rows} rows, row says {row.rows}")
    want = (row.rows, row.t_min, row.t_max, fingerprint(archived))
    got: tuple[int, datetime | None, datetime | None, str] | None = None
    async with conn.transaction():
        got = await _delete_window(conn, w)
        if got != want:
            raise psycopg.Rollback()  # ends the transaction: the delete is undone
        await conn.execute(
            "update archive_object set hot_deleted_at = clock_timestamp()"
            " where vehicle = %s and hour = %s", key,
        )  # fmt: skip
        got = None  # committed
    if got is not None:
        return _refuse(ctx, *key, f"deleted rows {got[:3]} / {got[3][:12]} != archive {want[:3]}")
    ctx.refused.discard(key)
    return True


def _dedupe_sorted(t: pa.Table) -> pa.Table:
    s = sort_rows(t)
    seen: set[tuple[Any, Any, Any]] = set()
    keep = []
    for i, k in enumerate(
        zip(s["vehicle"].to_pylist(), s["t_onboard"].to_pylist(), s["seq"].to_pylist(), strict=True)
    ):
        if k not in seen:
            seen.add(k)
            keep.append(i)
    return s.take(keep)


async def merge_late(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], row: ArchiveRow
) -> ArchiveRow | None:
    """Late frames of an hour whose hot rows are gone (D-ARC-06 v1+): the recorded object plus the
    late rows become version + 1; the row update and the delete of the late rows commit together,
    and only if exactly those late rows go. Any doubt: None, the late rows stay hot, and a later
    pass retries."""
    prev = await _recorded_table(ctx, row)
    if prev is None:
        return None
    w = hour_bounds(row.vehicle, row.hour)
    late = await hot_table(conn, w, prev.schema)
    if late.num_rows == 0:
        return None
    expected = _dedupe_sorted(pa.concat_tables([prev, late], promote_options="default"))
    version = row.version + 1
    key = object_key(row.vehicle, row.hour, version)
    sha = await put(ctx.s3, ctx.bucket, key, encode(expected))
    ctx.hooks.fire("archive:after_put")
    stored = await get(ctx.s3, ctx.bucket, key)
    if hashlib.sha256(stored).hexdigest() != sha or not same_rows(decode(stored), expected):
        _fail(row.vehicle, row.hour, f"{key} does not match the merged rows")
        return None
    ctx.hooks.fire("archive:after_reread")
    name, body = sidecar(key, sha)
    await put(ctx.s3, ctx.bucket, name, body)

    t_min, t_max = expected["t_onboard"][0].as_py(), expected["t_onboard"][-1].as_py()
    want = (
        late.num_rows,
        late["t_onboard"][0].as_py(),
        late["t_onboard"][-1].as_py(),
        fingerprint(late),
    )
    merged: ArchiveRow | None = None
    got: tuple[int, datetime | None, datetime | None, str] | None = None
    async with conn.transaction():
        cur = await conn.execute(
            "update archive_object set version = %s, sha256 = %s, rows = %s, t_min = %s,"
            " t_max = %s, verified_at = now(), hot_deleted_at = clock_timestamp(),"
            " last_checked_at = null"
            " where vehicle = %s and hour = %s and version = %s and sha256 = %s"
            " returning vehicle, hour, sha256, rows, t_min, t_max, version, verified_at,"
            " hot_deleted_at, last_checked_at",
            (
                version,
                sha,
                expected.num_rows,
                t_min,
                t_max,
                row.vehicle,
                row.hour,
                row.version,
                row.sha256,
            ),
        )
        updated = await cur.fetchone()
        got = await _delete_window(conn, w)
        if updated is None or got != want:
            raise psycopg.Rollback()  # both the row update and the delete are undone
        merged = ArchiveRow(*updated)
    if merged is None:
        _refuse(ctx, row.vehicle, row.hour, f"late rows changed during the merge: {got}")
        return None
    ctx.refused.discard((row.vehicle, row.hour))
    return merged
