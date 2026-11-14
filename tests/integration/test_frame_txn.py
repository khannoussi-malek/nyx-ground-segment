import asyncio
import random
import time
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest
import redis.exceptions
from psycopg.rows import dict_row

from gs.ingest import store
from gs.ingest.events import reset_key
from gs.ingest.fold import state_version
from gs.simulator.frames import make_frame
from tests.support.env import now, until, vid
from tests.support.ingest import process_batch_once, worker
from tests.support.pg import admin_dsn, app_dsn, events_for, stored
from tests.support.redisx import client, drained, pending, produce, read_without_ack, xadd_raw

SEQ_KINDS = {"GAP", "SEQ_JUMP", "SEQ_RESET", "SEQ_ANOMALY"}


def put(v: str, seqs: range, base: datetime, same_values: bool = False) -> None:
    """Frames at base + 100 ms per seq; same_values → every analog value identical."""
    for q in seqs:
        rng = random.Random(0 if same_values else q)
        xadd_raw(v, make_frame(v, q, base + timedelta(milliseconds=100 * q), rng))


def row(table: str, v: str) -> dict[str, Any] | None:
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        return c.execute(f"select * from {table} where vehicle=%s", (v,)).fetchone()  # noqa: S608


def kinds(v: str) -> list[str]:
    return [e["kind"] for e in events_for(v)]


def redeliver_last(v: str, n: int) -> None:
    """XADD byte-identical copies of the last n entries (a producer resend)."""
    r = client("admin", decode=False)
    last: Any = r.xrevrange(f"tm:{v}", count=n)
    for _, f in reversed(last):
        r.xadd(f"tm:{v}", {"data": f[b"data"]})


async def test_gap_event_once_across_redelivery() -> None:  # ING-04 + R5 T4
    v, base = vid(), now() - timedelta(minutes=1)
    # explicit vehicle time: produce() stamps the wall clock per XADD, which skews the period
    put(v, range(50), base)
    put(v, range(150, 200), base)  # t_onboard keeps running: 10 s of silence
    async with worker([v]):
        await until(lambda: stored(v) == 100 and drained(v), 10)
    redeliver_last(v, 6)
    async with worker([v]) as w:
        await until(lambda: drained(v), 10)
    assert w.ctx.dup_or_late[v] == 6 and stored(v) == 100
    (gap,) = events_for(v, "GAP")
    assert (gap["details"]["lost"], gap["details"]["from_seq"]) == (100, 50)
    assert abs(gap["details"]["lost_by_time"] - 100) <= 1
    assert [k for k in kinds(v) if k in ("LOS", "AOS")] == ["LOS", "AOS"]


async def test_seq_state_in_same_txn() -> None:  # D-ING-15
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(5), base)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    s = row("seq_state", v)
    assert s is not None and (s["last_seq"], s["epoch"]) == (4, base)
    e = row("engine_state", v)
    assert e is not None and (e["last_seq"], e["epoch"], e["schema_version"]) == (
        4,
        base,
        state_version(),
    )


async def test_failed_write_rolls_back_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    v = vid()
    produce(v, 3)
    entries = read_without_ack(v, consumer="c1")

    async def boom(*_: object) -> int:
        raise RuntimeError("events insert failed")

    monkeypatch.setattr(store, "insert_events", boom)
    with pytest.raises(RuntimeError):
        await process_batch_once(v, entries)
    assert stored(v) == 0 and row("seq_state", v) is None and pending(v) == 3


async def test_restart_continues_tracker_no_false_gap() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    put(v, range(10, 20), base)
    async with worker([v]):
        await until(lambda: stored(v) == 20 and drained(v), 5)
    assert not SEQ_KINDS & set(kinds(v))
    s = row("seq_state", v)
    assert s is not None and (s["last_seq"], s["epoch"]) == (19, base)


async def test_silence_writes_los_then_aos() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: stored(v) == 10, 5)
        await until(lambda: len(events_for(v, "LOS")) == 1, 5)  # ~2 s of no new frame
        assert kinds(v) == ["LOS"]
        put(v, range(10, 20), base)  # vehicle time continues: not a data gap
        await until(lambda: len(events_for(v, "AOS")) == 1, 5)
    (los,), (aos,) = events_for(v, "LOS"), events_for(v, "AOS")
    assert los["key"][4:] == aos["key"][4:] and los["key"].endswith("/9")
    assert los["t_onboard"] == base + timedelta(milliseconds=900)
    assert aos["details"]["lost_frames"] == 0 and kinds(v) == ["LOS", "AOS"]


async def test_aos_after_restart_during_los() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: len(events_for(v, "LOS")) == 1, 6)
    e = row("engine_state", v)
    assert e is not None and e["state"]["link"]["los_open"] is True
    put(v, range(10, 20), base)
    async with worker([v]):  # state from engine_state: AOS, not a second LOS
        await until(lambda: len(events_for(v, "AOS")) == 1, 5)
    assert kinds(v) == ["LOS", "AOS"]
    (los,), (aos,) = events_for(v, "LOS"), events_for(v, "AOS")
    assert los["key"][4:] == aos["key"][4:]


async def test_frozen_event_on_identical_values() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(25), base, same_values=True)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    (f,) = events_for(v, "FROZEN")
    assert f["details"]["first_seq"] == 0 and f["t_onboard"] == base + timedelta(seconds=2)


async def test_frozen_run_survives_restart() -> None:  # FrozenState round-trips through jsonb
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(15), base, same_values=True)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    assert events_for(v, "FROZEN") == []
    put(v, range(15, 25), base, same_values=True)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    (f,) = events_for(v, "FROZEN")
    assert f["details"]["first_seq"] == 0


async def test_engine_state_version_mismatch_reset() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    with psycopg.connect(admin_dsn("gs")) as c:
        c.execute("update engine_state set schema_version = -1 where vehicle=%s", (v,))
    put(v, range(10, 20), base)
    async with worker([v]):
        await until(lambda: stored(v) == 20 and drained(v), 5)
    (r,) = events_for(v, "ENGINE_STATE_RESET")
    assert r["key"] == reset_key(v, base, 9) and r["details"]["found_version"] == -1
    e = row("engine_state", v)
    assert e is not None and (e["schema_version"], e["last_seq"]) == (state_version(), 19)
    assert not SEQ_KINDS & set(kinds(v))  # seq_state still continues the tracker


async def test_silence_threshold_lower_bound() -> None:  # LOS at > 2 s, not earlier
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: stored(v) == 10, 5)
        await asyncio.sleep(1.5)
        assert events_for(v, "LOS") == []
        await until(lambda: len(events_for(v, "LOS")) == 1, 2.0)


async def test_redis_outage_is_not_los(monkeypatch: pytest.MonkeyPatch) -> None:  # D-UI-04/15
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]) as w:
        await until(lambda: stored(v) == 10, 5)
        real = w.ctx.redis.xreadgroup
        down_until = time.monotonic() + 3.0
        recovered: list[float] = []

        async def broker_down(*a: Any, **k: Any) -> Any:
            if time.monotonic() < down_until:
                raise redis.exceptions.ConnectionError("broker down")
            if not recovered:
                recovered.append(time.monotonic())
            return await real(*a, **k)

        monkeypatch.setattr(w.ctx.redis, "xreadgroup", broker_down)
        await until(lambda: bool(recovered), 8)
        await asyncio.sleep(1.5 - (time.monotonic() - recovered[0]))
        assert events_for(v, "LOS") == []


async def test_reset_event_written_while_silent() -> None:  # extra rides with the LOS
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    with psycopg.connect(admin_dsn("gs")) as c:
        c.execute("update engine_state set schema_version = -1 where vehicle=%s", (v,))
    async with worker([v]):  # no new frame: only the silence path writes
        await until(lambda: len(events_for(v, "LOS")) == 1, 5)
    assert len(events_for(v, "ENGINE_STATE_RESET")) == 1
    e = row("engine_state", v)
    assert e is not None and e["schema_version"] == state_version()
