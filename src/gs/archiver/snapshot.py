"""Daily snapshot of every non-telemetry table into the archive bucket (D-ARC-12, R3-01)."""

import asyncio
import hashlib
import io
import logging
import tarfile
from datetime import datetime
from typing import Any

import psycopg
from minio.error import S3Error

from gs.archiver import s3
from gs.archiver.job import ArchiverCtx

# every table but telemetry, parents before children (the restore COPYs in this order)
ORDER_BY = {
    "limits_versions": "set_sha256",
    "test_session": "id",
    "commands": "cmd_id",
    "alarms": "alarm_id",
    "alarm_events": "id",
    "alarm_ack_request": "request_id",
    "events": "id",
    "seq_state": "vehicle",
    "engine_state": "vehicle",
    "session_report": "session_id",
    "archive_object": "vehicle, hour",
    "audit_log": "id",
}
TABLES = tuple(ORDER_BY)
log = logging.getLogger(__name__)


def snapshot_key(t: datetime) -> str:
    return t.strftime("state/%Y/%m/%d/%H%M.tar.gz")


async def _copy_table(conn: psycopg.AsyncConnection[Any], table: str) -> bytes:
    out = io.BytesIO()
    async with conn.cursor() as cur:
        async with cur.copy(
            f"COPY (SELECT * FROM {table} ORDER BY {ORDER_BY[table]}) TO STDOUT"
            " (FORMAT csv, HEADER)"
        ) as cp:
            async for block in cp:
                out.write(block)
    return out.getvalue()


def _compress(tables: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for t, data in tables.items():
            info = tarfile.TarInfo(f"{t}.csv")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def _tarball(conn: psycopg.AsyncConnection[Any]) -> bytes:
    """One REPEATABLE READ read-only transaction: every table is read from the same view.
    The gzip runs in a thread, after the transaction: it must not stall the health server."""
    await conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    try:
        tables = {t: await _copy_table(conn, t) for t in TABLES}
    finally:
        await conn.execute("ROLLBACK")
    return await asyncio.to_thread(_compress, tables)


async def _exists(ctx: ArchiverCtx, key: str) -> bool:
    try:
        await asyncio.to_thread(ctx.s3.stat_object, ctx.bucket, key)
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchObject"):
            return False
        raise
    return True


async def count_orphans(ctx: ArchiverCtx) -> int:
    """Tarballs under state/ with no `.sha256` sibling (one listing). The archiver cannot delete
    them (D-ARC-09), so they stay counted until a person removes them."""

    def listing() -> list[str]:
        return [o.object_name for o in ctx.s3.list_objects(ctx.bucket, "state/", recursive=True)]

    keys = set(await asyncio.to_thread(listing))
    return sum(1 for k in keys if k.endswith(".tar.gz") and f"{k}.sha256" not in keys)


async def take_snapshot(ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], now: datetime) -> str:
    """PUT the tarball, read it back and compare, and only then PUT `<key>.sha256` (the sidecar
    is the commit mark: no sidecar, no snapshot). Returns the key."""
    key = snapshot_key(now)
    if await _exists(ctx, key):  # D-ARC-09: no overwrite
        log.error("state snapshot %s already exists: refusing to overwrite it", key)
        raise FileExistsError(key)
    data = await _tarball(conn)
    sent = await s3.put(ctx.s3, ctx.bucket, key, data)
    try:
        if hashlib.sha256(await s3.get(ctx.s3, ctx.bucket, key)).hexdigest() != sent:
            raise OSError(f"snapshot {key} differs from what was sent")
        await s3.put(ctx.s3, ctx.bucket, f"{key}.sha256", sent.encode())
    except BaseException:
        # no delete in the archiver policy (D-ARC-09): /healthz counts it from the bucket
        log.error("state snapshot %s has no sidecar and cannot be deleted", key)
        raise
    return key
