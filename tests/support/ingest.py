"""In-process ingest harness: run_vehicle tasks / single batches against the compose redis + db."""

import asyncio
import subprocess
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.events import insert_set
from gs.ingest.fold import empty_state
from gs.ingest.validate import FieldStats
from gs.ingest.worker import Ctx, process_batch, run_vehicle
from tests.support.compose import compose, compose_ps, restart_count
from tests.support.env import wait_until
from tests.support.mission import MISSION_DIR
from tests.support.pg import admin_dsn, app_dsn
from tests.support.redisx import GROUP, aclient, client, drained


async def ensure_set(dsn: str | None = None) -> None:
    """The start set row engine_state references, as main.run inserts it."""
    async with await psycopg.AsyncConnection.connect(dsn or app_dsn("gs"), autocommit=True) as c:
        await insert_set(c, load_dir(MISSION_DIR))


def make_ctx(consumer: str) -> Ctx:
    mdb = load_dir(MISSION_DIR)
    return Ctx(
        consumer,
        build_frame_model(mdb.param_names),
        mdb.param_names,
        aclient("ingest", decode=False, retry=Retry(NoBackoff(), 0)),
        FieldStats(),
        {},
        mdb.set_sha256,
        Counter(),
    )


@dataclass
class Running:
    ctx: Ctx
    tasks: list[asyncio.Task[None]]

    def done(self) -> bool:
        return any(t.done() for t in self.tasks)


@asynccontextmanager
async def worker(
    vehicles: list[str], consumer: str = "ingest-test", dsn: str | None = None
) -> AsyncIterator[Running]:
    """run_vehicle tasks; a custom `dsn` must already hold the start set (see ensure_set)."""
    if dsn is None:
        await ensure_set()
    ctx, stop = make_ctx(consumer), asyncio.Event()
    tasks = [asyncio.create_task(run_vehicle(ctx, v, dsn or app_dsn("gs"), stop)) for v in vehicles]
    try:
        yield Running(ctx, tasks)
    finally:
        stop.set()
        try:
            await asyncio.gather(*tasks)
        finally:
            await ctx.redis.aclose()


async def process_batch_once(
    v: str, entries: list[tuple[str, dict[bytes, bytes]]], consumer: str = "c1"
) -> int:
    ctx = make_ctx(consumer)
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
            await ensure_set()
            _, n = await process_batch(ctx, conn, f"tm:{v}", entries, empty_state(ctx.set_sha256))
            return n
    finally:
        await ctx.redis.aclose()


def claim(v: str, eid: str, consumer: str) -> None:
    """XCLAIM one pending entry to `consumer` (times_delivered + 1)."""
    client("admin").xclaim(f"tm:{v}", GROUP, consumer, min_idle_time=0, message_ids=[eid])


def times_delivered(v: str) -> list[int]:
    """times_delivered of every pending entry of tm:<v>, in id order."""
    rows: Any = client("admin").xpending_range(f"tm:{v}", GROUP, "-", "+", 1000)
    return [int(r["times_delivered"]) for r in rows]


INGEST = ("ingest-1", "ingest-2")
LIVE = ("NYX-TA1", "NYX-TA2")


def running_ingest() -> list[str]:
    """Ingest services that run (ingest-2 is opt-in until the ownership lock, G1)."""
    ps = compose_ps()
    return [s for s in INGEST if ps.get(s, {}).get("State") in ("running", "restarting")]


def restarts() -> dict[str, int]:
    return {s: restart_count(s) for s in running_ingest()}


def ingest_backends(service: str) -> list[int]:
    """pids of the service's Postgres sessions (application_name = consumer name)."""
    with psycopg.connect(admin_dsn("gs")) as c:
        rows = c.execute(
            "select pid from pg_stat_activity where application_name = %s", (service,)
        ).fetchall()
    return [r[0] for r in rows]


def terminate_backends(pids: list[int]) -> None:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        for pid in pids:
            c.execute("select pg_terminate_backend(%s)", (pid,))


def set_read_only(on: bool) -> None:
    """A-R15-3: the whole cluster read-only via ALTER SYSTEM + reload (existing sessions too)."""
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        if on:
            c.execute("ALTER SYSTEM SET default_transaction_read_only = on")
        else:
            c.execute("ALTER SYSTEM RESET default_transaction_read_only")
        c.execute("SELECT pg_reload_conf()")


def wait_drained(vehicles: tuple[str, ...], timeout: float) -> None:
    wait_until(lambda: all(drained(v) for v in vehicles), timeout)


def uptime_s(service: str) -> float:
    cid = compose("ps", "-q", service).stdout.strip()
    started = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", cid],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()


def wait_backoff_reset(services: list[str]) -> None:
    """Docker resets its restart delay (100 ms, doubling) after 10 s of uptime; restart counts
    in a fixed window only mean something from the 100 ms start."""
    wait_until(lambda: all(uptime_s(s) > 11 for s in services), 30)
