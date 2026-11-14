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
from gs.ingest.worker import BACKOFF_MAX_S, BLOCK_MS, GROUP, SOCKET_TIMEOUT_S, Ctx

log = logging.getLogger(__name__)

STALE_S = 10.0  # an owned loop that has not passed for this long makes the service down
# G23: a holder's worst own reconnect after an outage (max backoff + a socket timeout + two
# blocked reads); the hold-off after OUR reconnect is never shorter than that
HOLDOFF_MIN_MS = int((BACKOFF_MAX_S + SOCKET_TIMEOUT_S) * 1000) + 2 * BLOCK_MS


def should_steal(idle_ms: int, steal_idle_ms: int, since_reconnect_ms: int | None) -> bool:
    """R2-23/G23: no steal within max(STEAL_IDLE_MS, HOLDOFF_MIN_MS) of our own Redis reconnect,
    because the holder's `idle` kept counting through the same outage."""
    hold_off = max(steal_idle_ms, HOLDOFF_MIN_MS)
    return idle_ms > steal_idle_ms and (
        since_reconnect_ms is None or since_reconnect_ms >= hold_off
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
    not attempted a read for STEAL_IDLE_MS on two consecutive ticks with the same holder pid
    (G24), re-checked right before the terminate; our waiting task then takes over (D-ING-12)."""
    t = ctx.reconnected_at[0]
    since = None if t is None else int((time.monotonic() - t) * 1000)
    stolen: list[str] = []
    for v in ctx.vehicles:
        h = None if v in ctx.owned else await holder(conn, v)
        idle = None if h is None or h[1] == ctx.consumer else await _consumer_idle(ctx, v, h[1])
        if h is None or idle is None or not should_steal(idle, ctx.steal_idle_ms, since):
            ctx.suspects.pop(v, None)
            continue
        if ctx.suspects.get(v) != h[0]:
            ctx.suspects[v] = h[0]  # first sighting: steal on the next tick if still so
            continue
        ctx.suspects.pop(v)
        if await holder(conn, v) != h:  # the lock moved since the lookup
            continue
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


async def tick(
    ctx: Ctx, conn: psycopg.AsyncConnection, watchdog: bool, was_ok: bool = True
) -> bool:
    """One worker tick; returns whether Redis answered. Redis back after a failed tick starts the
    steal hold-off BEFORE this tick's steal check (the holder's idle grew through the outage)."""
    ok = await refresh_memory(ctx)
    if ok and not was_ok:
        ctx.reconnected_at[0] = time.monotonic()
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
    ok = True
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, application_name=f"{ctx.consumer}:tick"
        ) as conn:
            while not stop.is_set():
                ok = await tick(ctx, conn, watchdog, was_ok=ok)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), ctx.timing.tick_s)
    except psycopg.Error as e:
        raise OperationalDbError(f"tick: {e}") from e
    finally:
        if watchdog:
            faulthandler.cancel_dump_traceback_later()
