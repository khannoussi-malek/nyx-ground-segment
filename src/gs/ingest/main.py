"""gs-ingest: read tm:<vehicle>, store idempotently, publish to ui:events, then ack."""

import asyncio
import logging
import signal
import sys
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.config import require, require_int, vehicle_list
from gs.common.health import serve_health
from gs.common.hooks import Hooks
from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.errors import OperationalDbError
from gs.ingest.events import insert_set
from gs.ingest.liveness import ingest_status, refresh_memory, run_ticks
from gs.ingest.owner import parse_homes
from gs.ingest.validate import FieldStats
from gs.ingest.worker import SOCKET_TIMEOUT_S, Ctx, run_vehicle


@dataclass(frozen=True)
class IngestSettings:
    redis_url: str
    database_url: str
    consumer_name: str
    vehicles: tuple[str, ...]
    mission_db: str
    bind_host: str
    health_port: int
    homes: Mapping[str, str] = field(default_factory=dict)  # vehicle -> home consumer
    steal_idle_ms: int = 30_000
    reclaim_idle_ms: int = 30_000

    @classmethod
    def from_env(cls) -> IngestSettings:
        vehicles = vehicle_list(require("VEHICLES"))
        return cls(
            redis_url=require("REDIS_URL"),
            database_url=require("DATABASE_URL"),
            consumer_name=require("CONSUMER_NAME"),
            vehicles=vehicles,
            mission_db=require("MISSION_DB"),
            bind_host=require("BIND_HOST"),
            health_port=require_int("HEALTH_PORT"),
            homes=parse_homes(require("INGEST_HOMES"), vehicles),
            steal_idle_ms=require_int("STEAL_IDLE_MS"),
            reclaim_idle_ms=require_int("RECLAIM_IDLE_MS"),
        )


async def run(s: IngestSettings, stop: asyncio.Event) -> None:
    mdb = load_dir(Path(s.mission_db))
    async with await psycopg.AsyncConnection.connect(s.database_url, autocommit=True) as conn:
        await insert_set(conn, mdb)  # engine_state.set_sha256 references it
    # redis-py 8.1: Redis(...) defaults to 10 retries, from_url(...) to 0; keep it 0 explicitly
    # no client-side retry: every connection error must reach run_vehicle's backoff loop, which
    # re-reads our PEL and never re-sends a non-idempotent XADD behind our back (D-ING-03/08)
    r = redis.asyncio.Redis.from_url(
        s.redis_url, socket_timeout=SOCKET_TIMEOUT_S, retry=Retry(NoBackoff(), 0)
    )
    seen: dict[str, float] = {}  # a standby task (lock not granted) is not a stale loop
    ctx = Ctx(
        s.consumer_name,
        build_frame_model(mdb.param_names),
        mdb.param_names,
        r,
        FieldStats(),
        seen,
        mdb.set_sha256,
        Counter(),
        s.homes,
        vehicles=s.vehicles,
        steal_idle_ms=s.steal_idle_ms,
        reclaim_idle_ms=s.reclaim_idle_ms,
        hooks=Hooks.from_env(),
    )
    await refresh_memory(ctx)  # health knows Redis from the first request
    server = await serve_health(
        s.bind_host,
        s.health_port,
        lambda: ingest_status(seen, ctx.owned, time.monotonic(), ctx.used_memory[0]),
    )
    try:
        async with asyncio.TaskGroup() as tg:  # any task error cancels the rest and exits 1
            for v in s.vehicles:
                tg.create_task(run_vehicle(ctx, v, s.database_url, stop))
            tg.create_task(run_ticks(ctx, s.database_url, stop, watchdog=True))
    finally:
        server.close()
        await server.wait_closed()
        await r.aclose()


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(IngestSettings.from_env(), stop)


log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_main())
    except* OperationalDbError as eg:  # D-ING-24: exit; locks free, restart re-reads our PEL
        log.error("operational database error, exiting: %s", eg.exceptions[0])
        sys.exit(1)
