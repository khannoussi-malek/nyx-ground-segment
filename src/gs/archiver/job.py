"""Archive one vehicle-hour: write, read back, compare bytes and rows, only then record it."""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
import pyarrow as pa
from minio import Minio
from minio.error import S3Error

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
from gs.common.time import iso_z
from gs.ingest.events import GroundEvent, insert_events

log = logging.getLogger(__name__)

UPSERT = """
insert into archive_object (vehicle, hour, sha256, rows, t_min, t_max, version, verified_at)
values (%s, %s, %s, %s, %s, %s, %s, now())
on conflict (vehicle, hour) do update set version = excluded.version, sha256 = excluded.sha256,
  rows = excluded.rows, t_min = excluded.t_min, t_max = excluded.t_max,
  verified_at = excluded.verified_at, last_checked_at = null
  where archive_object.version < excluded.version and archive_object.hot_deleted_at is null
returning vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,
  last_checked_at
"""


def corrupt_key(vehicle: str, hour: datetime, version: int) -> str:
    """T4-d: version 0 keeps the design key, later versions get a /v<N> suffix."""
    base = f"ARCHIVE_CORRUPT/{vehicle}/{iso_z(hour)}"
    return base if version == 0 else f"{base}/v{version}"


async def flag_corrupt(
    conn: psycopg.AsyncConnection[Any], row: ArchiveRow, found: str | None
) -> None:
    """Put an ARCHIVE_CORRUPT on record (idempotent by key): expected = the row's sha256."""
    event = GroundEvent(
        corrupt_key(row.vehicle, row.hour, row.version), row.vehicle, "ARCHIVE_CORRUPT", row.hour,
        {"hour": row.hour, "version": row.version, "expected": row.sha256, "found": found},
    )  # fmt: skip
    await insert_events(conn, [event])


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
    if not await _may_replace_recorded(ctx, conn, vehicle, w.start, version, table):
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
    if row is None:  # the row moved on (newer version, or hot-deleted) since the pre-PUT check
        _fail(vehicle, w.start, f"{key} not recorded: the archive row changed during the archive")
        return None
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


async def _recorded_table(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], row: ArchiveRow
) -> pa.Table | None:
    """The recorded object, decoded, only if its bytes still hash to the recorded sha256. A missing
    or altered object is put on record (ARCHIVE_CORRUPT) without waiting for the scrub."""
    found: str | None = None
    try:
        data = await get(ctx.s3, ctx.bucket, object_key(row.vehicle, row.hour, row.version))
        found = hashlib.sha256(data).hexdigest()
    except S3Error as e:
        if e.code != "NoSuchKey":
            raise
    if found != row.sha256:
        _fail(row.vehicle, row.hour, f"recorded object is {found or 'missing'}, not {row.sha256}")
        await flag_corrupt(conn, row, found)
        return None
    return decode(data)


async def _may_replace_recorded(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], vehicle: str, hour: datetime,
    version: int, table: pa.Table,
) -> bool:  # fmt: skip
    """Checked BEFORE the PUT. Without a row anything goes. With one: only a strictly newer version,
    and never once the hot rows are deleted (the object is then the only copy; late rows go
    through merge_late). The new version must contain every row of the recorded one (hot rows are
    a superset only if nothing was lost since), which also implies min <= old t_min, max >= old
    t_max and rows >= old rows."""
    old = await _recorded(conn, vehicle, hour)
    if old is None:
        return True
    if old.hot_deleted_at is not None or version <= old.version:
        _fail(
            vehicle,
            hour,
            f"a row exists (version {old.version}, hot deleted: "
            f"{old.hot_deleted_at is not None}); refusing to overwrite with version {version}",
        )
        return False
    prev = await _recorded_table(ctx, conn, old)
    if prev is None:
        # corrupt or missing, and the hot rows are still here: they are the truth, a new version
        # heals it (the ARCHIVE_CORRUPT resolves when the row's sha256 changes). The object cannot
        # prove the new version drops nothing, the row can: same count and range at least.
        t_min, t_max = table["t_onboard"][0].as_py(), table["t_onboard"][-1].as_py()
        if table.num_rows < old.rows or t_min > old.t_min or t_max < old.t_max:
            _fail(vehicle, hour, "the hot rows no longer cover the corrupt version's rows or range")
            return False
        return True
    if not set(keys(prev)) <= set(keys(table)):
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
    assert conn.autocommit, "delete_hot needs an autocommit connection (top-level transaction)"
    key = (row.vehicle, row.hour)
    if (w.vehicle, w.start) != key:
        return _refuse(ctx, *key, f"window {w.vehicle} {w.start} is not the archived hour")
    archived = await _recorded_table(ctx, conn, row)  # S3 trouble raises here, before any delete
    if archived is None:
        return _refuse(ctx, *key, "the archived object is not the recorded one")
    if archived.num_rows != row.rows:
        return _refuse(ctx, *key, f"object has {archived.num_rows} rows, row says {row.rows}")
    want = (row.rows, row.t_min, row.t_max, fingerprint(archived))
    why: str | None = None
    async with conn.transaction():
        got = await _delete_window(conn, w)
        if got != want:
            why = f"deleted rows {got[:3]} / {got[3][:12]} != archive {want[:3]}"
        else:
            cur = await conn.execute(
                "update archive_object set hot_deleted_at = clock_timestamp()"
                " where vehicle = %s and hour = %s and version = %s and sha256 = %s",
                (*key, row.version, row.sha256),
            )
            if cur.rowcount != 1:
                why = "the archive row changed (version or sha256) during the delete"
        if why is not None:
            raise psycopg.Rollback()  # ends the transaction: the delete is undone
    if why is not None:
        return _refuse(ctx, *key, why)
    ctx.refused.discard(key)
    return True


def merged_rows(prev: pa.Table, late: pa.Table, vehicle: str) -> pa.Table:
    """The archived rows plus the late rows whose (t_onboard, seq) is not archived yet; on a shared
    key the archived row wins (a differing late copy is logged)."""
    have = {
        k: i
        for i, k in enumerate(
            zip(prev["t_onboard"].to_pylist(), prev["seq"].to_pylist(), strict=True)
        )
    }
    common = [c for c in prev.column_names if c in late.column_names]
    fresh, differing = [], 0
    for j, k in enumerate(zip(late["t_onboard"].to_pylist(), late["seq"].to_pylist(), strict=True)):
        if k not in have:
            fresh.append(j)
        elif repr(late.select(common).slice(j, 1).to_pylist()) != repr(
            prev.select(common).slice(have[k], 1).to_pylist()
        ):
            differing += 1
    if differing:
        log.warning(
            "%d late row(s) share a key with an archived row but differ; the archived row is kept",
            differing, extra={"vehicle": vehicle},
        )  # fmt: skip
    return sort_rows(
        pa.concat_tables(
            [prev, late.take(pa.array(fresh, type=pa.int64()))], promote_options="default"
        )
    )


async def merge_late(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], row: ArchiveRow
) -> ArchiveRow | None:
    """Late frames of an hour whose hot rows are gone (D-ARC-06 v1+): the recorded object plus the
    late rows become version + 1; the row update and the delete of the late rows commit together,
    and only if exactly those late rows go. Any doubt: None, the late rows stay hot, and a later
    pass retries."""
    assert conn.autocommit, "merge_late needs an autocommit connection (top-level transaction)"
    prev = await _recorded_table(ctx, conn, row)
    if prev is None:
        return None
    w = hour_bounds(row.vehicle, row.hour)
    late = await hot_table(conn, w, await hot_schema(conn))  # columns added since v0 included
    if late.num_rows == 0:
        return None
    expected = merged_rows(prev, late, row.vehicle)
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
    why = ""
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
        if updated is None:
            why = "the archive row changed (version or sha256) before the merge committed"
            raise psycopg.Rollback()
        if got != want:
            why = f"late rows changed during the merge: {got}"
            raise psycopg.Rollback()  # both the row update and the delete are undone
        merged = ArchiveRow(*updated)
    if merged is None:
        _refuse(ctx, row.vehicle, row.hour, why)
        return None
    ctx.refused.discard((row.vehicle, row.hour))
    return merged
