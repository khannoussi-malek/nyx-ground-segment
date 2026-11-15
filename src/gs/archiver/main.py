"""gs-archiver: each loop archives due hours, deletes hot rows past retention, scrubs."""

import asyncio
import logging
import math
import os
import signal
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import psycopg
import redis.asyncio
import redis.exceptions
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.archiver.hours import hour_bounds
from gs.archiver.job import ArchiverCtx, archive_hour, delete_hot, merge_late
from gs.archiver.plan import (
    Anomaly,
    Archive,
    ArchiveRow,
    DeleteHot,
    MergeLate,
    oldest_unarchived,
    plan_pass,
)
from gs.archiver.s3 import S3_ERRORS, S3Settings, client
from gs.archiver.scrub import scrub_pass, unresolved_corrupt
from gs.archiver.status import archiver_status
from gs.common.config import ConfigError, require, require_int
from gs.common.health import State, serve_health
from gs.common.hooks import Hooks
from gs.common.logs import setup_logging

log = logging.getLogger(__name__)

HEALTH_REFRESH_S = 5.0
REDIS_TIMEOUT_S = 2.0
COUNTS = (
    "select vehicle, date_bin('1 hour', t_onboard, TIMESTAMPTZ 'epoch') as hour, count(*)"
    " from telemetry group by 1, 2"
)  # ponytail: full-hot-table count per pass, upgrade to per-chunk counts if the hot table
# outgrows 24 h x 2 x 100 Hz
ROWS = (
    "select vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,"
    " last_checked_at from archive_object"
)


def _float(name: str, default: float, *, minimum: float, strict: bool = False) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        raise ConfigError(f"environment variable {name} must be a number, got {raw!r}") from None
    if not math.isfinite(v) or v < minimum or (strict and v == minimum):
        raise ConfigError(f"environment variable {name}={raw!r} is out of range")
    return v


@dataclass(frozen=True)
class ArchiverSettings:
    database_url: str
    redis_url: str
    s3: S3Settings
    bind_host: str
    health_port: int
    retention_h: float = 25.0
    loop_s: float = 300.0

    @classmethod
    def from_env(cls) -> ArchiverSettings:
        return cls(
            database_url=require("DATABASE_URL"),
            redis_url=require("REDIS_URL"),
            s3=S3Settings.from_env(),
            bind_host=require("BIND_HOST"),
            health_port=require_int("HEALTH_PORT"),
            retention_h=_float("HOT_RETENTION_H", 25.0, minimum=0.0),
            loop_s=_float("ARCHIVE_LOOP_S", 300.0, minimum=0.0, strict=True),
        )


@dataclass(frozen=True)
class PassResult:
    archived: int = 0
    merged: int = 0
    deleted: int = 0
    anomalies: int = 0
    scrubbed: int = 0
    failed: bool = False  # S3 trouble: the pass stopped, nothing was deleted after it
    oldest_unarchived: datetime | None = None


async def _connect(url: str) -> psycopg.AsyncConnection[Any]:
    conn = await psycopg.AsyncConnection.connect(url, autocommit=True)
    await conn.execute("SET TIME ZONE 'UTC'")  # hour bounds are UTC instants either way
    return conn


async def run_pass(ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any]) -> PassResult:
    """One loop: plan from hot counts and archive rows, act oldest first, then scrub. An S3 error
    ends the pass at once, so no delete follows it (deletes are planned after every archive)."""
    now = datetime.now(UTC)
    cur = await conn.execute(COUNTS)
    counts = {(v, h): int(n) for v, h, n in await cur.fetchall()}
    cur = await conn.execute(ROWS)
    rows = {(r[0], r[1]): ArchiveRow(*r) for r in await cur.fetchall()}
    res = PassResult()
    done: set[tuple[str, datetime]] = set()
    try:
        for a in plan_pass(now, counts, rows, ctx.retention_h):
            match a:
                case Archive():
                    ok = await archive_hour(ctx, conn, a.vehicle, a.hour, a.version) is not None
                    res = replace(res, archived=res.archived + ok)
                    if ok:
                        done.add((a.vehicle, a.hour))
                case MergeLate():
                    ok = await merge_late(ctx, conn, rows[(a.vehicle, a.hour)]) is not None
                    res = replace(res, merged=res.merged + ok)
                    if ok:
                        done.add((a.vehicle, a.hour))
                case DeleteHot():
                    row = rows[(a.vehicle, a.hour)]
                    ok = await delete_hot(ctx, conn, row, hour_bounds(a.vehicle, a.hour))
                    res = replace(res, deleted=res.deleted + ok)
                case Anomaly():
                    log.error("archive anomaly: %s", a.detail, extra={"vehicle": a.vehicle})
                    res = replace(res, anomalies=res.anomalies + 1)
        res = replace(res, scrubbed=await scrub_pass(ctx, conn))
    except S3_ERRORS as e:
        log.warning("archiver pass stopped, MinIO trouble: %s", e)
        res = replace(res, failed=True)
    pending = {k: n for k, n in counts.items() if k not in done}
    return replace(res, oldest_unarchived=oldest_unarchived(now, pending, rows))


@dataclass(frozen=True)
class Snapshot:
    db_ok: bool = False
    redis_ok: bool = False
    s3_ok: bool = False
    corrupt: int = 0
    oldest_unarchived: datetime | None = None
    last_pass: datetime | None = None


async def _probe(s: ArchiverSettings, ctx: ArchiverCtx, r: redis.asyncio.Redis) -> Snapshot:
    """Cheap health facts only; the backlog and last_pass come from the pass."""
    try:
        r_ok = bool(await r.ping())
    except redis.exceptions.RedisError, OSError:
        r_ok = False

    def listing() -> None:
        list(ctx.s3.list_objects(ctx.bucket, prefix="healthz/"))

    try:
        await asyncio.to_thread(listing)
        s_ok = True
    except S3_ERRORS:
        s_ok = False
    try:
        async with await _connect(s.database_url) as conn:
            return Snapshot(True, r_ok, s_ok, await unresolved_corrupt(conn))
    except psycopg.Error:
        return Snapshot(False, r_ok, s_ok)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass


async def run(s: ArchiverSettings, stop: asyncio.Event) -> None:
    ctx = ArchiverCtx(client(s.s3), s.s3.bucket, Hooks.from_env(), s.retention_h)
    r = redis.asyncio.Redis.from_url(
        s.redis_url, socket_timeout=REDIS_TIMEOUT_S, retry=Retry(NoBackoff(), 0)
    )
    snap = await _probe(s, ctx, r)

    async def refresh() -> None:
        nonlocal snap
        while not stop.is_set():
            await _sleep(stop, HEALTH_REFRESH_S)
            p = await _probe(s, ctx, r)  # keep the pass's backlog and last_pass
            snap = replace(
                snap, db_ok=p.db_ok, redis_ok=p.redis_ok, s3_ok=p.s3_ok, corrupt=p.corrupt
            )

    def status() -> tuple[State, dict[str, object]]:
        return archiver_status(
            datetime.now(UTC), snap.db_ok, snap.redis_ok, snap.s3_ok, snap.oldest_unarchived,
            snap.corrupt, len(ctx.refused), snap.last_pass,
        )  # fmt: skip

    server = await serve_health(s.bind_host, s.health_port, status)
    refresher = asyncio.create_task(refresh())
    delay = 1.0
    try:
        while not stop.is_set():
            try:
                async with await _connect(s.database_url) as conn:
                    res = await run_pass(ctx, conn)
                failed = res.failed
                snap = replace(
                    snap, oldest_unarchived=res.oldest_unarchived, last_pass=datetime.now(UTC)
                )
            except psycopg.Error as e:
                log.warning("archiver pass failed, database trouble: %s", e)
                failed = True
            wait = min(delay, s.loop_s) if failed else s.loop_s  # 1, 2, 4 ... capped at loop_s
            delay = delay * 2 if failed else 1.0
            await _sleep(stop, wait)
    finally:
        refresher.cancel()
        server.close()
        await server.wait_closed()
        await r.aclose()


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(ArchiverSettings.from_env(), stop)


def main() -> None:
    setup_logging("gs-archiver")
    asyncio.run(_main())
