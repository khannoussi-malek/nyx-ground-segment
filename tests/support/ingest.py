"""In-process ingest harness: run_vehicle tasks / single batches against the compose redis + db."""

import asyncio
import dataclasses
import os
import socket
import subprocess
import sys
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.hooks import Hooks
from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.events import insert_set
from gs.ingest.fold import empty_state
from gs.ingest.liveness import run_ticks
from gs.ingest.owner import MATCH
from gs.ingest.validate import FieldStats
from gs.ingest.worker import Ctx, Timing, process_batch, run_vehicle
from tests.support.compose import compose, compose_ps, restart_count
from tests.support.env import REPO, dotenv, vid, wait_until
from tests.support.mission import MISSION_DIR
from tests.support.pg import admin_dsn, app_dsn
from tests.support.redisx import GROUP, aclient, client, drained

DEFAULT_TIMING = Timing()
NO_HOOKS = Hooks()
# what the fast_ingest fixture runs both compose workers with (CI thresholds, hooks on)
FAST_ENV = {"GS_TEST_HOOKS": "1", "STEAL_IDLE_MS": "3000", "RECLAIM_IDLE_MS": "3000"}


async def ensure_set(dsn: str | None = None) -> None:
    """The start set row engine_state references, as main.run inserts it."""
    async with await psycopg.AsyncConnection.connect(dsn or app_dsn("gs"), autocommit=True) as c:
        await insert_set(c, load_dir(MISSION_DIR))


def make_ctx(
    consumer: str,
    homes: Mapping[str, str] | None = None,
    timing: Timing = DEFAULT_TIMING,
    vehicles: tuple[str, ...] = (),
    steal_idle_ms: int = 30_000,
    reclaim_idle_ms: int = 30_000,
) -> Ctx:
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
        homes or {},
        timing,
        vehicles,
        steal_idle_ms,
        reclaim_idle_ms,
    )


@dataclass
class Running:
    ctx: Ctx
    tasks: list[asyncio.Task[None]]

    def done(self) -> bool:
        return any(t.done() for t in self.tasks)


@asynccontextmanager
async def worker(
    vehicles: list[str],
    consumer: str = "ingest-test",
    homes: Mapping[str, str] | None = None,
    timing: Timing = DEFAULT_TIMING,
    dsn: str | None = None,
    steal_idle_ms: int = 30_000,
    reclaim_idle_ms: int = 30_000,
    hooks: Hooks = NO_HOOKS,
) -> AsyncIterator[Running]:
    """run_vehicle tasks; `homes=None` makes every vehicle home (no start delay, no hand-back).
    A custom `dsn` must already hold the start set (see ensure_set)."""
    if dsn is None:
        await ensure_set()
    homes = {v: consumer for v in vehicles} if homes is None else homes
    ctx = make_ctx(consumer, homes, timing, tuple(vehicles), steal_idle_ms, reclaim_idle_ms)
    ctx = dataclasses.replace(ctx, hooks=hooks)
    stop, db = asyncio.Event(), dsn or app_dsn("gs")
    tasks = [asyncio.create_task(run_vehicle(ctx, v, db, stop)) for v in vehicles]
    tasks.append(asyncio.create_task(run_ticks(ctx, db, stop, watchdog=False)))
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
    """Ingest services that run (both, since the ownership lock; G1/G5)."""
    ps = compose_ps()
    return [s for s in INGEST if ps.get(s, {}).get("State") in ("running", "restarting")]


def restarts() -> dict[str, int]:
    return {s: restart_count(s) for s in running_ingest()}


def lock_holder_pid(vehicle: str) -> tuple[int, str] | None:
    """(pid, application_name) of the session holding the vehicle's advisory lock."""
    with psycopg.connect(admin_dsn("gs")) as c:
        row = c.execute(
            "select l.pid, a.application_name from pg_locks l join pg_stat_activity a using (pid)"
            f" where {MATCH} and l.granted",
            ("tm:" + vehicle,),
        ).fetchone()
    return None if row is None else (int(row[0]), str(row[1]))


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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def start_ingest(
    db_password: str | None = None, **over: str
) -> tuple[subprocess.Popen[bytes], int]:
    """gs-ingest main() in a subprocess against the compose redis + db; `over` overrides env."""
    env, port, v = dotenv(), _free_port(), vid()
    proc_env = {
        **os.environ,
        "REDIS_URL": f"redis://ingest:{env['REDIS_INGEST_PW']}@127.0.0.1:{env['REDIS_HOST_PORT']}/0",
        "DATABASE_URL": f"postgresql://gs_app:{db_password or env['GS_APP_PASSWORD']}"
        f"@127.0.0.1:{env['DB_HOST_PORT']}/gs",
        "CONSUMER_NAME": "ingest-main-test",
        "VEHICLES": v,
        "INGEST_HOMES": f"ingest-main-test={v}",
        "STEAL_IDLE_MS": "30000",
        "RECLAIM_IDLE_MS": "30000",
        "MISSION_DB": str(REPO / "mission"),
        "BIND_HOST": "127.0.0.1",
        "HEALTH_PORT": str(port),
        **over,
    }
    code = "from gs.ingest.main import main; main()"
    return subprocess.Popen([sys.executable, "-c", code], cwd=REPO, env=proc_env), port


def latest_seq(vehicle: str) -> int:
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select seq from telemetry where vehicle=%s order by t_onboard desc limit 1", (vehicle,)
        ).fetchone()
    assert row is not None, f"no telemetry for {vehicle}"
    return int(row[0])
