"""Worker-level liveness: steal a hung owner, Redis memory, watchdog, health (D-ING-12, 25, 26).
Reclaim and heartbeat run inside each vehicle's read loop (worker._own), so a hung loop stops
them; only the steal decision, the memory probe and the watchdog re-arm live in this tick."""

import asyncio
import contextlib
import faulthandler
import logging
import time
from collections.abc import Collection, Mapping
from typing import Any

import psycopg
import redis.exceptions

from gs.common.health import State
from gs.ingest.errors import OperationalDbError
from gs.ingest.owner import holder
from gs.ingest.worker import GROUP, Ctx

log = logging.getLogger(__name__)

STALE_S = 10.0  # an owned loop that has not passed for this long makes the service down


def should_steal(idle_ms: int, steal_idle_ms: int, since_reconnect_ms: int | None) -> bool:
    """R2-23: no steal within steal_idle_ms of our own Redis reconnect (idle counts through it)."""
    return idle_ms > steal_idle_ms and (
        since_reconnect_ms is None or since_reconnect_ms >= steal_idle_ms
    )


def watchdog_ok(
    seen: Mapping[str, float], owned: Collection[str], now: float, limit_s: float
) -> bool:
    return all(v in seen and now - seen[v] < limit_s for v in owned)


def ingest_status(
    seen: Mapping[str, float], owned: Collection[str], now: float, used_memory: int | None
) -> tuple[State, dict[str, object]]:
    ages = {v: round(now - seen[v], 1) if v in seen else None for v in sorted(owned)}
    checks: dict[str, object] = {
        "owned": sorted(owned),
        "loop_age_s": ages,
        "redis_used_memory": used_memory,
    }
    if any(a is None or a >= STALE_S for a in ages.values()):
        return "down", checks
    return ("degraded" if used_memory is None else "ok"), checks


async def _consumer_idle(ctx: Ctx, vehicle: str, consumer: str) -> int | None:
    try:
        rows: Any = await ctx.redis.xinfo_consumers(f"tm:{vehicle}", GROUP)
    except redis.exceptions.ResponseError:  # no stream / no group yet: nothing to judge
        return None
    for r in rows:
        name = r["name"].decode() if isinstance(r["name"], bytes) else r["name"]
        if name == consumer:
            return int(r["idle"])
    return None  # the holder never read: its own watchdog covers a hang before the first read


async def steal_tick(ctx: Ctx, conn: psycopg.AsyncConnection) -> list[str]:
    """For each vehicle we don't own: terminate the holder's lock session when its consumer has
    not attempted a read for STEAL_IDLE_MS; our waiting task then takes over (D-ING-12)."""
    t = ctx.reconnected_at[0]
    since = None if t is None else int((time.monotonic() - t) * 1000)
    stolen: list[str] = []
    for v in ctx.vehicles:
        h = None if v in ctx.owned else await holder(conn, v)
        if h is None or h[1] == ctx.consumer:
            continue
        idle = await _consumer_idle(ctx, v, h[1])
        if idle is not None and should_steal(idle, ctx.steal_idle_ms, since):
            log.warning("stealing %s from %s (consumer idle %d ms)", v, h[1], idle)
            await conn.execute("SELECT pg_terminate_backend(%s)", (h[0],))
            stolen.append(v)
    return stolen


async def refresh_memory(ctx: Ctx) -> bool:
    """INFO memory → ctx.used_memory[0]; None (health degraded) when Redis does not answer."""
    try:
        info: Any = await ctx.redis.info("memory")
    except redis.exceptions.RedisError as e:
        log.warning("redis INFO failed (%s)", e)
        ctx.used_memory[0] = None
        return False
    ctx.used_memory[0] = int(info["used_memory"])
    return True


async def tick(ctx: Ctx, conn: psycopg.AsyncConnection, watchdog: bool) -> bool:
    """One worker tick; returns whether Redis answered."""
    ok = await refresh_memory(ctx)
    if ok:
        try:
            await steal_tick(ctx, conn)
        except redis.exceptions.RedisError as e:
            log.warning("steal check skipped (%s)", e)
            ok = False
    if watchdog and watchdog_ok(ctx.loop_seen, ctx.owned, time.monotonic(), ctx.timing.watchdog_s):
        # not re-armed while an owned loop hangs: the pending dump fires and exits (D-ING-26)
        faulthandler.dump_traceback_later(ctx.timing.watchdog_s, exit=True)
    return ok


async def run_ticks(ctx: Ctx, dsn: str, stop: asyncio.Event, watchdog: bool) -> None:
    redis_ok = True
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, application_name=f"{ctx.consumer}:tick"
        ) as conn:
            while not stop.is_set():
                ok = await tick(ctx, conn, watchdog)
                if ok and not redis_ok:  # back after an outage: start the steal hold-off
                    ctx.reconnected_at[0] = time.monotonic()
                redis_ok = ok
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), ctx.timing.tick_s)
    except psycopg.Error as e:
        raise OperationalDbError(f"tick: {e}") from e
    finally:
        if watchdog:
            faulthandler.cancel_dump_traceback_later()
