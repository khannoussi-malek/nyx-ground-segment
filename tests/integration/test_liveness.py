import asyncio
import faulthandler
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import pytest
import redis.asyncio
import redis.exceptions

from gs.ingest import liveness
from gs.ingest.owner import LOCK_SQL
from gs.ingest.worker import Timing
from tests.support.env import until, vid
from tests.support.ingest import make_ctx, worker
from tests.support.pg import app_dsn, lock_holder, stored
from tests.support.redisx import GROUP, client, frame, pending_of, produce, read_without_ack

FAST = Timing(start_delay_s=0.0, handback_s=0.3, reclaim_s=0.5, tick_s=0.5)


def ghost_read_new(v: str, n: int) -> None:
    """XADD n frames and read them as `ghost` in one MULTI: a running owner's blocked `>` read
    is served after the transaction, so the entries are only ever in ghost's PEL."""
    pipe = client("admin").pipeline(transaction=True)
    for seq in range(n):
        pipe.xadd(f"tm:{v}", {"data": frame(v, seq)})
    pipe.xreadgroup(GROUP, "ghost", {f"tm:{v}": ">"}, count=n)
    pipe.execute()


async def test_reclaim_idle_30s() -> None:  # ING-08: 1 s threshold here, 3 s in CI, 30 s default
    v = vid()
    async with worker([v], "w1", {v: "w1"}, FAST, reclaim_idle_ms=1000):
        await until(lambda: lock_holder(v) == "w1", 5)
        await asyncio.sleep(0.3)  # takeover drain done, the loop reads `>`
        ghost_read_new(v, 5)
        assert pending_of(v, "ghost") == 5
        await until(lambda: stored(v) == 5 and pending_of(v, "ghost") == 0, 6)


async def test_reclaim_waits_for_the_idle_threshold() -> None:
    v = vid()
    async with worker([v], "w1", {v: "w1"}, FAST, reclaim_idle_ms=3000):
        await until(lambda: lock_holder(v) == "w1", 5)
        await asyncio.sleep(0.3)
        ghost_read_new(v, 3)
        await asyncio.sleep(1.5)  # three reclaim passes, all below the threshold
        assert pending_of(v, "ghost") == 3 and stored(v) == 0
        await until(lambda: stored(v) == 3, 5)


def hb_ttl(v: str) -> int:
    return int(client("admin").ttl(f"hb:veh:{v}"))


async def test_heartbeat_from_loop() -> None:  # D-ING-25
    v = vid()
    async with worker([v], "w1", {v: "w1"}, FAST):
        await until(lambda: hb_ttl(v) in (1, 2, 3), 3)
        assert client("admin").get(f"hb:veh:{v}") == "1"
    await until(lambda: hb_ttl(v) == -2, 4)  # gone ≤ 3 s after the loop stops


async def test_no_heartbeat_from_a_standby_task() -> None:
    v = vid()
    async with ghost_owner(v, "ghost-owner"), worker([v], "w1", {v: "w1"}, FAST):
        await asyncio.sleep(1.5)
        assert hb_ttl(v) == -2


@asynccontextmanager
async def ghost_owner(
    v: str, app: str, read: bool = True
) -> AsyncIterator[psycopg.AsyncConnection]:
    """A session holding the vehicle's lock whose consumer read once and then stopped."""
    async with await psycopg.AsyncConnection.connect(
        app_dsn("gs"), autocommit=True, application_name=app
    ) as c:
        await c.execute(LOCK_SQL, ("tm:" + v,))
        if read:
            read_without_ack(v, app)
        yield c


async def test_steal_terminates_idle_owner() -> None:  # D-ING-12
    v = vid()
    produce(v, 5)
    async with ghost_owner(v, "ghost-owner") as ghost:
        fast = Timing(tick_s=0.5, start_delay_s=0)
        async with worker([v], "w1", {v: "other"}, fast, steal_idle_ms=1000):
            await until(lambda: lock_holder(v) == "w1" and stored(v) == 5, 8)
        with pytest.raises(psycopg.OperationalError):  # its session was terminated
            await ghost.execute("SELECT 1")
    assert pending_of(v, "ghost-owner") == 0  # its PEL was drained by the takeover


async def test_no_steal_of_a_reading_owner() -> None:
    v = vid()
    async with worker([v], "w0", {v: "w0"}, FAST):
        await until(lambda: lock_holder(v) == "w0", 5)
        async with worker([v], "w1", {v: "w0"}, FAST, steal_idle_ms=3000) as w1:
            await asyncio.sleep(4.0)  # eight steal ticks: w0 reads at least once per BLOCK
            assert lock_holder(v) == "w0" and w1.ctx.owned == set()


async def test_no_steal_right_after_own_reconnect() -> None:  # R2-23
    v = vid()
    async with ghost_owner(v, "ghost-owner"):
        await asyncio.sleep(1.2)  # the ghost consumer is idle > 1 s
        async with worker([v], "w1", {v: "other"}, Timing(tick_s=0.3), steal_idle_ms=1000) as w:
            w.ctx.reconnected_at[0] = time.monotonic()  # as if Redis just came back
            await asyncio.sleep(0.8)
            assert lock_holder(v) == "ghost-owner"


async def test_tick_rearms_watchdog_only_for_live_owned_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    armed: list[tuple[float, dict[str, Any]]] = []
    monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda t, **k: armed.append((t, k)))
    ctx = make_ctx("w1", {}, Timing(watchdog_s=7.5))
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
            ctx.owned.add("NYX-A")
            ctx.loop_seen["NYX-A"] = time.monotonic()
            await liveness.tick(ctx, c, watchdog=True)
            assert armed == [(7.5, {"exit": True})] and (ctx.used_memory[0] or 0) > 0
            ctx.loop_seen["NYX-A"] = time.monotonic() - 8  # a hung owned loop
            await liveness.tick(ctx, c, watchdog=True)
            assert len(armed) == 1  # not re-armed: the pending dump fires and exits
    finally:
        await ctx.redis.aclose()


async def test_tick_without_redis_reports_unknown_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(*_: Any, **__: Any) -> Any:
        raise redis.exceptions.ConnectionError("redis down")

    monkeypatch.setattr(redis.asyncio.Redis, "info", down)
    ctx = make_ctx("w1", {})
    ctx.used_memory[0] = 5
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
            assert await liveness.tick(ctx, c, watchdog=False) is False
        assert ctx.used_memory[0] is None
    finally:
        await ctx.redis.aclose()


async def test_no_steal_without_evidence() -> None:
    v_nostream, v_noread = vid(), vid()
    client("admin").xgroup_create(f"tm:{v_noread}", GROUP, id="0", mkstream=True)
    ctx = make_ctx("w1", {}, FAST, (v_nostream, v_noread), steal_idle_ms=0)
    try:
        async with (
            await psycopg.AsyncConnection.connect(
                app_dsn("gs"), autocommit=True, application_name="holder-x"
            ) as h,
            await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c,
        ):
            for v in (v_nostream, v_noread):
                await h.execute(LOCK_SQL, ("tm:" + v,))
            # no stream / no group, and a holder whose consumer never read: nothing to judge
            assert await liveness.steal_tick(ctx, c) == []
            assert lock_holder(v_nostream) == lock_holder(v_noread) == "holder-x"
    finally:
        await ctx.redis.aclose()


async def test_tick_survives_redis_failing_mid_steal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_: Any, **__: Any) -> Any:
        raise redis.exceptions.ConnectionError("redis gone")

    v = vid()
    monkeypatch.setattr(redis.asyncio.Redis, "xinfo_consumers", down)
    ctx = make_ctx("w1", {}, FAST, (v,))
    try:
        async with (
            await psycopg.AsyncConnection.connect(
                app_dsn("gs"), autocommit=True, application_name="holder-x"
            ) as h,
            await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c,
        ):
            await h.execute(LOCK_SQL, ("tm:" + v,))
            assert await liveness.tick(ctx, c, watchdog=False) is False
            assert lock_holder(v) == "holder-x"
    finally:
        await ctx.redis.aclose()


async def test_no_steal_on_the_tick_that_finds_redis_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # the holder's idle kept counting through the outage: not a hang
    v = vid()
    calls = 0
    real_info = redis.asyncio.Redis.info

    async def info_after_outage(self: redis.asyncio.Redis, *a: Any, **k: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls in (2, 3):  # tick 1 sees the idle holder once; ticks 2-3: outage
            raise redis.exceptions.ConnectionError("redis down")
        return await real_info(self, *a, **k)

    async with ghost_owner(v, "ghost-owner"):
        await asyncio.sleep(1.2)  # the holder's consumer is idle > 1 s
        monkeypatch.setattr(redis.asyncio.Redis, "info", info_after_outage)
        ctx = make_ctx("w1", {}, Timing(tick_s=0.05), (v,), steal_idle_ms=1000)
        stop = asyncio.Event()
        try:
            ticks = asyncio.create_task(liveness.run_ticks(ctx, app_dsn("gs"), stop, False))
            await until(lambda: calls >= 9, 3)  # 1 good, 2 down, then 6 good ticks
            assert ctx.reconnected_at[0] is not None and lock_holder(v) == "ghost-owner"
            stop.set()
            await asyncio.wait_for(ticks, 3)
        finally:
            await ctx.redis.aclose()


async def test_steal_needs_the_same_holder_twice() -> None:  # G24
    v = vid()
    ctx = make_ctx("w1", {}, FAST, (v,), steal_idle_ms=1000)
    try:
        async with (
            ghost_owner(v, "ghost-owner") as ghost,
            await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c,
        ):
            await asyncio.sleep(1.2)
            assert await liveness.steal_tick(ctx, c) == []  # first sighting over threshold
            assert await liveness.steal_tick(ctx, c) == [v]  # same pid on the next tick
            with pytest.raises(psycopg.OperationalError):
                await ghost.execute("SELECT 1")
    finally:
        await ctx.redis.aclose()


async def test_no_steal_when_the_holder_changed_between_ticks() -> None:  # G24
    v = vid()
    ctx = make_ctx("w1", {}, FAST, (v,), steal_idle_ms=1000)
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
            async with ghost_owner(v, "ghost-owner"):
                await asyncio.sleep(1.2)
                assert await liveness.steal_tick(ctx, c) == []
            async with ghost_owner(v, "ghost-owner", read=False):  # new pid, same idle consumer
                assert await liveness.steal_tick(ctx, c) == []
                assert await liveness.steal_tick(ctx, c) == [v]  # now twice in a row
    finally:
        await ctx.redis.aclose()


async def test_steal_never_terminates_a_session_that_does_not_hold_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # check and terminate are one statement: a stale pid is never killed
    v = vid()
    ctx = make_ctx("w1", {}, FAST, (v,), steal_idle_ms=1000)
    try:
        async with (
            ghost_owner(v, "ghost-owner") as ghost,
            await psycopg.AsyncConnection.connect(
                app_dsn("gs"), autocommit=True, application_name="bystander"
            ) as bystander,
            await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c,
        ):
            row = await (await bystander.execute("select pg_backend_pid()")).fetchone()
            assert row is not None

            async def stale_holder(*_: Any) -> tuple[int, str]:
                return (int(row[0]), "ghost-owner")  # the lock moved: this pid lost it

            monkeypatch.setattr(liveness, "holder", stale_holder)
            await asyncio.sleep(1.2)
            assert await liveness.steal_tick(ctx, c) == []
            assert await liveness.steal_tick(ctx, c) == []  # same pid twice, but not the holder
            await bystander.execute("SELECT 1")  # still alive
            await ghost.execute("SELECT 1")
    finally:
        await ctx.redis.aclose()
