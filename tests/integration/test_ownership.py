import asyncio
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from gs.ingest.errors import OperationalDbError
from gs.ingest.owner import LOCK_SQL, acquire, holder, waiting
from gs.ingest.worker import Timing
from tests.support.env import now, until, vid, wait_until
from tests.support.ingest import (
    lock_holder_pid,
    process_batch_once,
    start_ingest,
    terminate_backends,
    worker,
)
from tests.support.pg import (
    admin_dsn,
    app_dsn,
    gapless,
    lock_holder,
    out_of_order,
    seqs_by_t_received,
    seqs_since,
    stored,
)
from tests.support.redisx import client, frame, pending_of, produce, read_without_ack, xadd_raw

FAST = Timing(start_delay_s=0.3, handback_s=0.3, reclaim_s=0.5, tick_s=0.5)


def t0() -> Any:
    return now() - timedelta(minutes=2)  # frame() stamps t_onboard a minute in the past


async def produce_paced(vehicles: list[str], n: int, hz: float, start_seq: int = 0) -> None:
    for seq in range(start_seq, start_seq + n):
        for v in vehicles:
            xadd_raw(v, frame(v, seq))
        await asyncio.sleep(1 / hz)


async def test_two_workers_share_and_keep_order() -> None:  # ING-06
    v1, v2, since = vid(), vid(), t0()
    homes = {v1: "w1", v2: "w2"}
    async with worker([v1, v2], "w1", homes, FAST), worker([v1, v2], "w2", homes, FAST):
        await until(lambda: lock_holder(v1) == "w1" and lock_holder(v2) == "w2", 5)
        await produce_paced([v1, v2], n=200, hz=50)
        await until(lambda: stored(v1) == stored(v2) == 200, 15)
        assert lock_holder(v1) == "w1" and lock_holder(v2) == "w2"
    for v in (v1, v2):
        assert out_of_order(v, since) == 0 and gapless(seqs_since(v, since))


def read_n(v: str, consumer: str, n: int) -> list[tuple[str, dict[bytes, bytes]]]:
    r = client("ingest", decode=False)
    res: Any = r.xreadgroup("ingest", consumer, {f"tm:{v}": ">"}, count=n)
    return [(i.decode(), f) for i, f in res[0][1]]


async def test_order_check_catches_alternating_consumers() -> None:  # negative control
    v, since = vid(), t0()
    produce(v, 20)
    client("admin").xgroup_create(f"tm:{v}", "ingest", id="0")
    a, b = read_n(v, "ca", 10), read_n(v, "cb", 10)
    await process_batch_once(v, b, "cb")
    await process_batch_once(v, a, "ca")
    assert out_of_order(v, since) > 0


async def test_hand_back_when_home_worker_waits() -> None:
    v1, v2, since = vid(), vid(), t0()
    homes = {v1: "w1", v2: "w2"}
    async with worker([v1, v2], "w1", homes, FAST):
        await until(lambda: lock_holder(v2) == "w1", 5)  # home worker absent: w1 takes v2
        feed = asyncio.create_task(produce_paced([v2], n=100, hz=50))
        async with worker([v1, v2], "w2", homes, FAST):
            await until(lambda: lock_holder(v2) == "w2", 5)
            await feed
            await until(lambda: stored(v2) == 100, 5)
        assert lock_holder(v1) == "w1"
    assert gapless(seqs_since(v2, since)) and out_of_order(v2, since) == 0


async def test_takeover_drains_previous_owner_pel() -> None:  # D-ING-11
    v = vid()
    produce(v, 10)
    read_without_ack(v, "ghost")  # a dead owner's PEL
    produce(v, 10, start_seq=10)
    async with worker([v], "w1", {v: "w1"}, FAST):
        await until(lambda: stored(v) == 20, 5)
    assert seqs_by_t_received(v) == list(range(20)) and pending_of(v, "ghost") == 0


def vid_with_hashtext_sign(sign: int) -> str:
    with psycopg.connect(app_dsn("gs")) as c:
        while True:
            v = vid()
            row = c.execute("select hashtext(%s)", ("tm:" + v,)).fetchone()
            assert row is not None
            if (row[0] < 0) == (sign < 0):
                return v


@asynccontextmanager
async def session(app: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(
        app_dsn("gs"), autocommit=True, application_name=app
    ) as c:
        yield c


async def test_lock_match_negative_hashtext() -> None:  # Review Focus 3
    for v in (vid_with_hashtext_sign(-1), vid_with_hashtext_sign(+1)):
        async with session("home-x") as h, session("home-y") as w, session("probe") as c:
            await h.execute(LOCK_SQL, ("tm:" + v,))
            pid_row = await (await h.execute("select pg_backend_pid()")).fetchone()
            assert pid_row is not None
            blocked = asyncio.ensure_future(w.execute(LOCK_SQL, ("tm:" + v,)))
            try:
                deadline = time.monotonic() + 5
                while not await waiting(c, v, "home-y"):
                    assert time.monotonic() < deadline and not blocked.done()
                    await asyncio.sleep(0.05)
                assert await holder(c, v) == (pid_row[0], "home-x")
                assert not await waiting(c, v, "home-x")  # granted, not waiting
                assert lock_holder(v) == "home-x"
            finally:
                await w.cancel_safe()
                with pytest.raises(psycopg.errors.QueryCanceled):
                    await blocked
        async with session("probe") as c:
            assert await holder(c, v) is None


async def test_fenced_owner_exits_before_read() -> None:  # D-ING-12, R2-06
    v = vid()
    calm = Timing(los_s=60.0)  # keep the LOS write out of it: only the fence may notice
    with pytest.raises(OperationalDbError, match="fence"):
        async with worker([v], "w1", {v: "w1"}, calm) as w:
            await until(lambda: lock_holder(v) == "w1", 5)
            with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
                a.execute(
                    "select pg_terminate_backend(pid) from pg_stat_activity"
                    " where application_name = 'w1'"
                )
            await asyncio.wait_for(w.tasks[0], 3)  # ≤ one BLOCK, then the fence fails
    produce(v, 5)
    assert pending_of(v, "w1") == 0  # nothing entered the fenced owner's PEL


def test_sigterm_while_waiting_on_lock() -> None:  # Review Focus 4
    v = vid()
    with psycopg.connect(app_dsn("gs"), autocommit=True, application_name="other") as c:
        c.execute(LOCK_SQL, ("tm:" + v,))
        p, _ = start_ingest(VEHICLES=v, INGEST_HOMES=f"ingest-main-test={v}")
        try:
            wait_until(lambda: asyncio.run(_waiting(v)), 10)  # it is blocked on the lock
            p.send_signal(signal.SIGTERM)
            assert p.wait(timeout=5) == 0
        finally:
            if p.poll() is None:
                p.kill()


async def _waiting(v: str) -> bool:
    async with session("probe") as c:
        return await waiting(c, v, "ingest-main-test")


async def test_cancelled_acquire_ends_the_server_wait() -> None:
    v = vid()  # a TaskGroup cancels a standby task when a sibling vehicle fails
    async with asyncio.timeout(15), session("other") as h, session("w1") as c:
        await h.execute(LOCK_SQL, ("tm:" + v,))
        task = asyncio.create_task(acquire(c, v, asyncio.Event()))
        async with session("probe") as p:
            await until_async(lambda: waiting(p, v, "w1"), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert not await waiting(p, v, "w1")
        await asyncio.wait_for(c.execute("SELECT 1"), 3)  # the connection is usable again


async def until_async(cond: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not await cond():
        assert time.monotonic() < deadline, "condition not met"
        await asyncio.sleep(0.05)


def test_owner_db_error_exits_while_sibling_waits() -> None:  # TaskGroup + blocked standby
    v1, v2 = vid(), vid()
    with psycopg.connect(app_dsn("gs"), autocommit=True, application_name="other") as c:
        c.execute(LOCK_SQL, ("tm:" + v2,))
        homes = f"ingest-main-test={v1},ingest-main-test={v2}"
        p, _ = start_ingest(VEHICLES=f"{v1},{v2}", INGEST_HOMES=homes)
        try:
            wait_until(lambda: lock_holder(v1) == "ingest-main-test", 10)
            wait_until(lambda: asyncio.run(_waiting(v2)), 10)
            owner = lock_holder_pid(v1)
            assert owner is not None
            terminate_backends([owner[0]])
            assert p.wait(timeout=5) == 1
        finally:
            if p.poll() is None:
                p.kill()


async def test_home_worker_wins_at_startup() -> None:  # D-ING-10: non-home starts 2 s late
    v1, v2 = vid(), vid()
    homes, slow_handback = {v1: "w1", v2: "w2"}, Timing(start_delay_s=1.0, handback_s=60.0)
    async with worker([v1, v2], "w1", homes, slow_handback):
        await asyncio.sleep(0.1)  # w1 is up first; only the delay keeps it off v2
        async with worker([v1, v2], "w2", homes, slow_handback):
            await until(lambda: lock_holder(v1) == "w1" and lock_holder(v2) == "w2", 3)
