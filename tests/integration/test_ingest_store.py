import asyncio
import json
from typing import Any

import pytest

from tests.support.env import until, vid
from tests.support.ingest import process_batch_once, worker
from tests.support.pg import seqs_by_t_received, stored, stored_row
from tests.support.redisx import (
    GROUP,
    client,
    drained,
    frame,
    pending,
    produce,
    read_without_ack,
    ui_tm,
    ui_tm_count,
    xadd_raw,
)


async def test_two_vehicles_no_loss() -> None:  # ING-01 slice
    v1, v2 = vid(), vid()
    produce(v1, 50)
    produce(v2, 50)
    async with worker([v1, v2]):
        await until(lambda: stored(v1) == 50 and stored(v2) == 50, timeout=5)
        await until(lambda: drained(v1) and drained(v2), timeout=5)
    assert pending(v1) == pending(v2) == 0
    assert ui_tm_count(v1) == 50 and ui_tm_count(v2) == 50


async def test_duplicate_stored_once() -> None:  # ING-03 slice, D-ING-13
    v = vid()
    raw = frame(v, 0)
    xadd_raw(v, raw)
    xadd_raw(v, raw)
    async with worker([v]):
        await until(lambda: drained(v), timeout=5)
    assert stored(v) == 1 and ui_tm_count(v) == 1


async def test_redelivery_after_commit_publishes_nothing_new() -> None:
    v = vid()  # crash between COMMIT and XACK
    produce(v, 5)
    entries = read_without_ack(v, consumer="c1")
    assert len(entries) == 5
    assert await process_batch_once(v, entries) == 5  # stores + publishes + acks
    assert await process_batch_once(v, entries) == 0  # same entries again: nothing newly stored
    assert stored(v) == 5 and ui_tm_count(v) == 5


async def test_own_pel_read_first_after_restart() -> None:  # D-ING-03
    v = vid()
    produce(v, 5)
    read_without_ack(v, consumer=f"c-{v}")
    produce(v, 5, start_seq=5)
    async with worker([v], consumer=f"c-{v}"):
        await until(lambda: stored(v) == 10, timeout=5)
        await until(lambda: drained(v), timeout=5)
    assert seqs_by_t_received(v) == list(range(10))


async def test_stream_missing_at_start() -> None:
    v = vid()
    async with worker([v]) as w:
        await asyncio.sleep(1.2)
        assert not w.done()
        produce(v, 3)
        await until(lambda: stored(v) == 3, timeout=2)


async def test_survives_redis_connection_kill() -> None:
    v = vid()
    produce(v, 10)
    async with worker([v]) as w:
        await until(lambda: stored(v) == 10, timeout=5)
        client("admin").execute_command("CLIENT", "KILL", "USER", "ingest")  # type: ignore[no-untyped-call]
        produce(v, 10, start_seq=10)
        await until(lambda: stored(v) == 20, timeout=7)
        assert not w.done()
    assert ui_tm_count(v) == 20


async def test_unknown_and_missing_fields_counted_and_stored_as_null() -> None:  # R7
    v = vid()
    f = json.loads(frame(v, 0))
    del f["rate_z_dps"]
    f["bogus_x"] = 1.0
    xadd_raw(v, json.dumps(f))
    async with worker([v]) as w:
        await until(lambda: drained(v), timeout=5)
    assert w.ctx.stats.unknown["bogus_x"] == 1 and w.ctx.stats.missing["rate_z_dps"] == 1
    row = stored_row(v, 0)
    assert row["rate_z_dps"] is None and row["bus_voltage_v"] is not None
    assert json.loads(ui_tm(v)[0])["values"]["rate_z_dps"] is None


async def test_reconnect_rereads_own_pel(caplog: pytest.LogCaptureFixture) -> None:  # D-ING-03/08
    v = vid()
    produce(v, 5)
    ids: list[Any] = [
        i for i, _ in read_without_ack(v, consumer="other")
    ]  # pending elsewhere: a `>` read can never return them
    async with worker([v]) as w:
        await until(lambda: v in w.ctx.loop_seen, timeout=2)
        await asyncio.sleep(0.3)  # worker is now in its `>` loop
        client("admin").xclaim(
            f"tm:{v}",
            GROUP,
            "ingest-test",
            min_idle_time=0,
            message_ids=ids,
        )
        client("admin").execute_command("CLIENT", "KILL", "USER", "ingest")  # type: ignore[no-untyped-call]
        await until(lambda: stored(v) == 5, timeout=7)
    assert "redis unavailable" in caplog.text  # the error reached the backoff loop
