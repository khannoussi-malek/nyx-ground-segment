"""gs-archiver: each loop archives due hours, deletes hot rows past retention, scrubs."""

import asyncio
import logging
import math
import os
import signal
from collections.abc import Awaitable, Callable, Mapping
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
    Action,
    Anomaly,
    Archive,
    ArchiveRow,
    DeleteHot,
    Key,
    MergeLate,
    oldest_unarchived,
    plan_pass,
)
from gs.archiver.s3 import S3_ERRORS, S3Settings, client
from gs.archiver.scrub import scrub_pass, unresolved_corrupt, unresolved_corrupt_keys
from gs.archiver.status import archiver_status
from gs.common.config import ConfigError, require, require_int
from gs.common.health import State, serve_health
from gs.common.hooks import Hooks
from gs.common.logs import setup_logging
from gs.common.time import iso_z

log = logging.getLogger(__name__)

HEALTH_REFRESH_S = 5.0
PROBE_TIMEOUT_S = 5.0
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


async def _act(
    ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any], a: Action, rows: Mapping[Key, ArchiveRow]
) -> bool:
    """Run one planned action; True when it did its work. Any error is logged with its vehicle
    and hour, then raised."""
    try:
        match a:
            case Archive():
                return await archive_hour(ctx, conn, a.vehicle, a.hour, a.version) is not None
            case MergeLate():
                return await merge_late(ctx, conn, rows[(a.vehicle, a.hour)]) is not None
            case DeleteHot():
                row = rows[(a.vehicle, a.hour)]
                return await delete_hot(ctx, conn, row, hour_bounds(a.vehicle, a.hour))
            case Anomaly():
                log.error("archive anomaly: %s", a.detail, extra={"vehicle": a.vehicle})
                return False
    except Exception:
        log.exception(
            "archiver action %s failed", type(a).__name__,
            extra={"vehicle": a.vehicle, "hour": iso_z(a.hour)},
        )  # fmt: skip
        raise


async def run_pass(ctx: ArchiverCtx, conn: psycopg.AsyncConnection[Any]) -> PassResult:
    """One loop: scrub first, then plan from hot counts, archive rows and the unresolved corrupt
    hours, then act: builds oldest first, deletes after them. An S3 error ends the pass at once
    (the scrub runs before any delete, so no delete follows an S3 error: T4-q)."""
    now = datetime.now(UTC)
    cur = await conn.execute(COUNTS)
    counts = {(v, h): int(n) for v, h, n in await cur.fetchall()}
    cur = await conn.execute(ROWS)
    rows = {(r[0], r[1]): ArchiveRow(*r) for r in await cur.fetchall()}
    res = PassResult()
    done: set[Key] = set()
    try:
        res = replace(res, scrubbed=await scrub_pass(ctx, conn))
        corrupt = await unresolved_corrupt_keys(conn)
        for a in plan_pass(now, counts, rows, ctx.retention_h, corrupt):
            ok = await _act(ctx, conn, a, rows)
            match a:
                case Archive():
                    res = replace(res, archived=res.archived + ok)
                case MergeLate():
                    res = replace(res, merged=res.merged + ok)
                case DeleteHot():
                    res = replace(res, deleted=res.deleted + ok)
                case Anomaly():
                    res = replace(res, anomalies=res.anomalies + 1)
            if ok and isinstance(a, Archive | MergeLate):
                done.add((a.vehicle, a.hour))
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


@dataclass
class Box:
    """The health snapshot, replaced wholesale by whoever has fresher facts."""

    snap: Snapshot


_PROBE_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError, redis.exceptions.RedisError, psycopg.Error, *S3_ERRORS,
)  # fmt: skip


async def _bounded(awaitable: Awaitable[Any]) -> Any:
    """A probe that hangs counts as down; no probe may hold up the others."""
    try:
        return await asyncio.wait_for(awaitable, PROBE_TIMEOUT_S)
    except _PROBE_ERRORS:
        return None


async def _db_facts(s: ArchiverSettings) -> int | None:
    async with await _connect(s.database_url) as conn:
        return await unresolved_corrupt(conn)


async def _probe(s: ArchiverSettings, ctx: ArchiverCtx, r: redis.asyncio.Redis) -> Snapshot:
    """Cheap health facts only (the backlog and last_pass come from the pass); the three probes
    run concurrently, each bounded."""

    def listing() -> bool:
        list(ctx.s3.list_objects(ctx.bucket, prefix="healthz/"))
        return True

    pinged, listed, corrupt = await asyncio.gather(
        _bounded(r.ping()), _bounded(asyncio.to_thread(listing)), _bounded(_db_facts(s))
    )
    return Snapshot(corrupt is not None, bool(pinged), bool(listed), corrupt or 0)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass


async def _refresh(
    box: Box, probe: Callable[[], Awaitable[Snapshot]], stop: asyncio.Event, first: bool = False
) -> None:
    """Keep the probe facts fresh; a failing probe is logged and the next round tries again."""
    while not stop.is_set():
        if not first:
            await _sleep(stop, HEALTH_REFRESH_S)
        first = False
        try:
            p = await probe()
        except Exception:
            log.exception("health probe failed")
            continue
        box.snap = replace(
            box.snap, db_ok=p.db_ok, redis_ok=p.redis_ok, s3_ok=p.s3_ok, corrupt=p.corrupt
        )  # the pass's backlog and last_pass stay


async def run(s: ArchiverSettings, stop: asyncio.Event) -> None:
    ctx = ArchiverCtx(client(s.s3), s.s3.bucket, Hooks.from_env(), s.retention_h)
    r = redis.asyncio.Redis.from_url(
        s.redis_url,
        socket_timeout=REDIS_TIMEOUT_S,
        socket_connect_timeout=REDIS_TIMEOUT_S,
        retry=Retry(NoBackoff(), 0),
    )
    box = Box(Snapshot())

    async def probe() -> Snapshot:
        return await _probe(s, ctx, r)

    async def first_probe() -> None:
        try:
            box.snap = await probe()
        except Exception:
            log.exception("first health probe failed")

    await first_probe()

    def status() -> tuple[State, dict[str, object]]:
        snap = box.snap
        return archiver_status(
            datetime.now(UTC), snap.db_ok, snap.redis_ok, snap.s3_ok, snap.oldest_unarchived,
            snap.corrupt, len(ctx.refused), snap.last_pass,
        )  # fmt: skip

    server = await serve_health(s.bind_host, s.health_port, status)
    refresher = asyncio.create_task(_refresh(box, probe, stop))
    delay = 1.0
    try:
        while not stop.is_set():
            try:
                async with await _connect(s.database_url) as conn:
                    res = await run_pass(ctx, conn)
                failed = res.failed
                box.snap = replace(
                    box.snap, oldest_unarchived=res.oldest_unarchived, last_pass=datetime.now(UTC)
                )
            except Exception:  # a bug or a database error: this pass failed, the next one retries
                log.exception("archiver pass failed")
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
