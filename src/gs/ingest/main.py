"""gs-ingest: read tm:<vehicle>, store idempotently, publish to ui:events, then ack."""

import asyncio
import logging
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import psycopg
import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.config import require, require_int, vehicle_list
from gs.common.health import State, serve_health
from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.errors import OperationalDbError
from gs.ingest.events import insert_set
from gs.ingest.validate import FieldStats
from gs.ingest.worker import Ctx, run_vehicle

STALE_S = 10.0  # a vehicle loop that has not passed for this long makes the service down
SOCKET_TIMEOUT_S = 5  # must exceed the 1 s XREADGROUP block


@dataclass(frozen=True)
class IngestSettings:
    redis_url: str
    database_url: str
    consumer_name: str
    vehicles: tuple[str, ...]
    mission_db: str
    bind_host: str
    health_port: int

    @classmethod
    def from_env(cls) -> IngestSettings:
        return cls(
            redis_url=require("REDIS_URL"),
            database_url=require("DATABASE_URL"),
            consumer_name=require("CONSUMER_NAME"),
            vehicles=vehicle_list(require("VEHICLES")),
            mission_db=require("MISSION_DB"),
            bind_host=require("BIND_HOST"),
            health_port=require_int("HEALTH_PORT"),
        )


def ingest_status(
    seen: dict[str, float], vehicles: tuple[str, ...], now: float
) -> tuple[State, dict[str, object]]:
    ages = {v: round(now - seen[v], 1) if v in seen else None for v in vehicles}
    ok = all(a is not None and a < STALE_S for a in ages.values())
    return ("ok" if ok else "down"), {"loop_age_s": ages}


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
    seen = {v: time.monotonic() for v in s.vehicles}  # startup grace
    ctx = Ctx(
        s.consumer_name,
        build_frame_model(mdb.param_names),
        mdb.param_names,
        r,
        FieldStats(),
        seen,
        mdb.set_sha256,
        Counter(),
    )
    server = await serve_health(
        s.bind_host, s.health_port, lambda: ingest_status(seen, s.vehicles, time.monotonic())
    )
    try:
        async with asyncio.TaskGroup() as tg:  # any task error cancels the rest and exits 1
            for v in s.vehicles:
                tg.create_task(run_vehicle(ctx, v, s.database_url, stop))
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
