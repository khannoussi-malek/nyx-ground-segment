"""Read a replay window: verified Parquet hours plus any hot rows, one table per UTC hour."""

import hashlib
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.compute as pc
from minio import Minio
from minio.error import S3Error

from gs.archiver.hours import HOUR, hour_bounds, hour_of, object_key
from gs.archiver.job import merged_rows
from gs.archiver.parquet import decode, hot_schema, hot_table, sort_rows
from gs.archiver.plan import ArchiveRow
from gs.archiver.s3 import get

BASE_COLUMNS = ("vehicle", "t_onboard", "seq", "t_received", "mode", "last_cmd_id")
ROW_SQL = (
    "select vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,"
    " last_checked_at from archive_object where vehicle = %s and hour = %s"
)
ROWS_SQL = ROW_SQL.replace("hour = %s", "hour = any(%s) order by hour")


class ArchiveCorrupt(Exception):
    """An archived hour's object does not hash to its archive_object row (or is gone)."""

    def __init__(self, vehicle: str, hour: datetime, expected: str, found: str | None) -> None:
        super().__init__("archive object failed hash check")
        self.vehicle, self.hour, self.expected, self.found = vehicle, hour, expected, found


def _hours(t_from: datetime, t_to: datetime) -> Iterator[datetime]:
    for t in (t_from, t_to):
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError(f"naive datetime {t!r}")
    h = hour_of(t_from)
    while h < t_to:
        yield h
        h += HOUR


async def _archived(s3: Minio, bucket: str, row: ArchiveRow) -> pa.Table:
    """The row's current-version object, only if it hashes to the row's sha256."""
    found: str | None = None
    try:
        data = await get(s3, bucket, object_key(row.vehicle, row.hour, row.version))
        found = hashlib.sha256(data).hexdigest()
    except S3Error as e:
        if e.code != "NoSuchKey":
            raise
    if found != row.sha256:
        raise ArchiveCorrupt(row.vehicle, row.hour, row.sha256, found)
    return decode(data)


def _project(t: pa.Table, params: Sequence[str]) -> pa.Table:
    """BASE_COLUMNS + params; a column the table lacks (added since it was written): typed NULLs."""
    cols = {}
    for name in (*BASE_COLUMNS, *params):
        cols[name] = t[name] if name in t.column_names else pa.nulls(t.num_rows, pa.float64())
    return pa.table(cols)


async def window_hours(
    conn: psycopg.AsyncConnection[Any],
    s3: Minio,
    bucket: str,
    vehicle: str,
    t_from: datetime,
    t_to: datetime,
    params: Sequence[str],
) -> AsyncIterator[pa.Table]:
    """One table per UTC hour touching [t_from, t_to), oldest first, sorted (t_onboard, seq), rows
    outside the window dropped, an empty hour skipped. An hour with an archive row reads its
    current object (ArchiveCorrupt when it fails the hash) plus the hot rows still or again there
    (late frames), the archived row winning a shared key (EX-29). The hot rows are read BEFORE the
    archive row: an archive-and-delete in between then leaves the rows in the object we find."""
    hours = list(_hours(t_from, t_to))  # rejects naive datetimes before any I/O
    schema = await hot_schema(conn)
    for h in hours:
        hot = await hot_table(conn, hour_bounds(vehicle, h), schema)
        cur = await conn.execute(ROW_SQL, (vehicle, h))
        found = await cur.fetchone()
        t = hot
        if found is not None:
            row = ArchiveRow(*found)
            t = (
                merged_rows(await _archived(s3, bucket, row), hot, vehicle, row.hour)
                if hot.num_rows
                else (await _archived(s3, bucket, row))
            )
        t = t.filter(
            pc.and_(pc.greater_equal(t["t_onboard"], t_from), pc.less(t["t_onboard"], t_to))
        )
        if t.num_rows:
            yield _project(sort_rows(t), params)


async def archive_rows(
    conn: psycopg.AsyncConnection[Any], vehicle: str, t_from: datetime, t_to: datetime
) -> list[ArchiveRow]:
    """The archive rows of the window's hours, oldest first: one query, so a caller can leave the
    connection before it reads the objects."""
    cur = await conn.execute(
        ROWS_SQL,
        (vehicle, list(_hours(t_from, t_to))),  # naive datetimes raise
    )
    return [ArchiveRow(*r) for r in await cur.fetchall()]


async def check_rows(s3: Minio, bucket: str, rows: Sequence[ArchiveRow]) -> None:
    """Hash-check each row's object, in order; ArchiveCorrupt on the first bad one."""
    for row in rows:
        await _archived(s3, bucket, row)


async def verify_window(
    conn: psycopg.AsyncConnection[Any], s3: Minio, bucket: str, vehicle: str, t_from: datetime,
    t_to: datetime,
) -> None:  # fmt: skip
    """Hash-check every archived hour of the window; ArchiveCorrupt on the first bad one."""
    await check_rows(s3, bucket, await archive_rows(conn, vehicle, t_from, t_to))


async def has_data(
    conn: psycopg.AsyncConnection[Any], vehicle: str, t_from: datetime, t_to: datetime
) -> bool:
    """A hot row or an archived row range overlapping [t_from, t_to)."""
    list(_hours(t_from, t_to))  # naive datetimes raise
    cur = await conn.execute(
        "select exists (select 1 from telemetry where vehicle = %(v)s and t_onboard >= %(a)s"
        " and t_onboard < %(b)s) or exists (select 1 from archive_object where vehicle = %(v)s"
        " and t_min < %(b)s and t_max >= %(a)s)",
        {"v": vehicle, "a": t_from, "b": t_to},
    )
    row = await cur.fetchone()
    assert row is not None
    return bool(row[0])
