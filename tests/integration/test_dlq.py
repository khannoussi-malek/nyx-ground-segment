import asyncio
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
import redis.asyncio
import redis.exceptions
from psycopg import sql

from gs.common.interfaces import DlqRecord, FrameHeader
from gs.common.migrate import add_columns
from gs.common.missiondb import load_dir
from gs.ingest import worker as worker_mod
from gs.ingest.errors import OperationalDbError
from gs.ingest.events import poison_key
from gs.ingest.fold import VehicleState, fold
from gs.ingest.store import insert_frames
from tests.support.env import until, vid
from tests.support.ingest import claim, ensure_set, process_batch_once, times_delivered, worker
from tests.support.mission import MISSION_DIR
from tests.support.pg import admin_dsn, app_dsn, events_for, owner_dsn, stored, throwaway_db
from tests.support.redisx import (
    GROUP,
    client,
    dlq_entries,
    dlq_entry_for,
    drained,
    frame,
    pending,
    produce,
    read_without_ack,
    xadd_raw,
)


async def test_malformed_to_dlq_and_continue() -> None:  # ING-02
    v = vid()
    produce(v, 1)
    eid = xadd_raw(v, b"not json")
    produce(v, 1, start_seq=1)
    async with worker([v]):
        await until(lambda: stored(v) == 2 and drained(v), timeout=5)
    assert pending(v) == 0
    rec = DlqRecord.model_validate_json(dlq_entry_for(eid))
    got = (rec.reason, rec.stream, rec.entry_id, rec.raw, rec.deliveries)
    assert got == ("json_invalid", f"tm:{v}", eid, "not json", 1)


async def test_dlq_deliveries_come_from_xpending() -> None:  # R8
    v = vid()
    eid = xadd_raw(v, b"{broken")
    entries = read_without_ack(v, consumer="c1")
    client("admin").xclaim(f"tm:{v}", GROUP, "c2", min_idle_time=0, message_ids=[eid])
    await process_batch_once(v, entries, consumer="c2")
    rec = DlqRecord.model_validate_json(dlq_entry_for(eid))
    assert rec.deliveries == 2 and pending(v) == 0


async def test_each_reason_classified_in_batch() -> None:
    v = vid()
    xadd_raw(v, json.dumps({"vehicle": v, "seq": "x"}))
    xadd_raw(v, frame("NYX-OTHER", 0))
    produce(v, 1)
    async with worker([v]):
        await until(lambda: drained(v), timeout=5)
    reasons = sorted(json.loads(d)["reason"] for d in dlq_entries(v))
    assert reasons == ["schema", "vehicle_mismatch"] and stored(v) == 1


async def test_poison_after_3() -> None:  # ING-08: DLQ on the 4th delivery
    v = vid()
    eid = xadd_raw(v, frame(v, 0))
    read_without_ack(v, consumer="g1")
    claim(v, eid, "g2")
    claim(v, eid, "ingest-test")  # 3 deliveries, never acked; now in the worker's own PEL
    async with worker([v]):
        await until(lambda: drained(v), 5)
    rec = DlqRecord.model_validate_json(dlq_entry_for(eid))
    assert (rec.reason, rec.deliveries, rec.detail) == ("poison", 4, "delivered 4 times")
    assert stored(v) == 0 and json.loads(rec.raw)["seq"] == 0
    (ev,) = events_for(v, "POISON")
    assert ev["key"] == poison_key(f"tm:{v}", eid)
    assert ev["details"] == {"stream": f"tm:{v}", "entry_id": eid, "deliveries": 4}


class Crash(BaseException):
    """Stands in for os._exit / OOM: never reaches the operational-error path."""


async def test_poison_neighbours_not_dlqd(monkeypatch: pytest.MonkeyPatch) -> None:  # RF 2
    v = vid()
    produce(v, 20)
    bad = [i for i, _ in read_all(v)][7]

    def crashing_fold(
        vs: VehicleState, vehicle: str, frames: Sequence[FrameHeader], params: Sequence[str]
    ) -> object:
        if any(f.seq == 7 for f in frames):
            raise Crash
        return fold(vs, vehicle, frames, params)

    monkeypatch.setattr(worker_mod, "fold", crashing_fold)
    for _ in range(3):  # crash every time the bad frame is read: deliveries 1, 2, 3
        with pytest.raises(Crash):
            async with worker([v]) as w:
                await asyncio.wait_for(w.tasks[0], 10)  # a missing crash fails, not hangs
    async with worker([v]):  # 4th read of the bad frame: DLQ'd before processing
        await until(lambda: drained(v), 5)
    assert stored(v) == 19 and [json.loads(d)["entry_id"] for d in dlq_entries(v)] == [bad]


def read_all(v: str) -> list[tuple[str, Any]]:
    rows: Any = client("admin").xrange(f"tm:{v}")
    return [(str(i), f) for i, f in rows]


@dataclass(frozen=True)
class Trigger:
    vehicle: str
    seq: int


@pytest.fixture
def owner_trigger() -> Iterator[Trigger]:
    """BEFORE INSERT trigger on telemetry: SQLSTATE 22023 for (vehicle, seq 5) only."""
    t = Trigger(vid(), 5)
    name = "reject_" + t.vehicle.lower().replace("-", "_")
    fn, trg = sql.Identifier(name), sql.Identifier(name + "_trg")
    with psycopg.connect(owner_dsn("gs"), autocommit=True) as c:
        c.execute(
            sql.SQL(
                "CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN"
                " IF NEW.vehicle = {} AND NEW.seq = {} THEN"
                " RAISE EXCEPTION 'test reject' USING ERRCODE = '22023'; END IF;"
                " RETURN NEW; END $$"
            ).format(fn, sql.Literal(t.vehicle), sql.Literal(t.seq))
        )
        c.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON telemetry FOR EACH ROW EXECUTE FUNCTION {}()"
            ).format(trg, fn)
        )
    try:
        yield t
    finally:
        with psycopg.connect(owner_dsn("gs"), autocommit=True) as c:
            c.execute(sql.SQL("DROP TRIGGER {} ON telemetry").format(trg))
            c.execute(sql.SQL("DROP FUNCTION {}()").format(fn))


async def test_data_error_isolates_frame(owner_trigger: Trigger) -> None:  # ING-08, D-ING-04
    v = owner_trigger.vehicle
    produce(v, 10)
    eid = [i for i, _ in read_all(v)][5]
    async with worker([v]) as w:
        await until(lambda: drained(v), 5)
        assert not w.done()  # no exit
    (raw,) = dlq_entries(v)
    rec = DlqRecord.model_validate_json(raw)
    assert stored(v) == 9 and rec.reason == "poison" and rec.detail.startswith("22023")
    assert (rec.entry_id, rec.deliveries) == (eid, 1) and "test reject" in rec.detail
    assert [e["key"] for e in events_for(v, "POISON")] == [poison_key(f"tm:{v}", eid)]
    (gap,) = events_for(v, "GAP")  # the DLQ'd frame never reached the tracker
    assert (gap["details"]["from_seq"], gap["details"]["lost"]) == (5, 1)


async def test_frame_committed_on_last_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    v = vid()
    produce(v, 10)
    calls = 0

    async def flaky(conn: object, frames: list[FrameHeader], params: object) -> object:
        nonlocal calls
        if any(f.seq == 5 for f in frames):
            calls += 1
            if calls <= 3:  # the batch + 2 per-frame attempts fail (a processing exception)
                raise ValueError("flaky")
        return await insert_frames(conn, frames, params)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_mod, "insert_frames", flaky)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    assert stored(v) == 10 and dlq_entries(v) == [] and calls == 4


@pytest.fixture
def readonly_db() -> Iterator[str]:
    with throwaway_db() as db:
        with psycopg.connect(owner_dsn(db), autocommit=True) as c:
            add_columns(c, load_dir(MISSION_DIR))  # as the migrate service does
        asyncio.run(ensure_set(app_dsn(db)))
        with psycopg.connect(admin_dsn("postgres"), autocommit=True) as c:
            c.execute(
                sql.SQL("ALTER DATABASE {} SET default_transaction_read_only = on").format(
                    sql.Identifier(db)
                )
            )
        yield app_dsn(db)


async def test_operational_exit_resets_delivery_count(readonly_db: str) -> None:  # R15 E1
    v = vid()
    produce(v, 5)
    with pytest.raises(OperationalDbError, match="25006"):
        async with worker([v], dsn=readonly_db) as w:
            await asyncio.wait_for(w.tasks[0], 10)
    assert times_delivered(v) == [0] * 5 and pending(v) == 5 and dlq_entries(v) == []


async def test_cancelled_batch_resets_delivery_count(monkeypatch: pytest.MonkeyPatch) -> None:
    v = vid()  # a sibling vehicle's exit cancels this batch mid-transaction
    produce(v, 3)
    entries = read_without_ack(v, consumer="c1")
    entered = asyncio.Event()

    async def hang(*_: object) -> list[FrameHeader]:
        entered.set()
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(worker_mod, "insert_frames", hang)
    task = asyncio.create_task(process_batch_once(v, entries))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert times_delivered(v) == [0] * 3 and pending(v) == 3


async def test_operational_error_not_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    v = vid()  # an operational error is never retried per frame and never DLQs a frame
    produce(v, 5)
    entries = read_without_ack(v, consumer="c1")
    calls = 0

    async def shutdown(*_: object) -> list[FrameHeader]:
        nonlocal calls
        calls += 1
        raise psycopg.errors.AdminShutdown("terminating connection")

    monkeypatch.setattr(worker_mod, "insert_frames", shutdown)
    with pytest.raises(OperationalDbError, match="57P01"):
        await process_batch_once(v, entries)
    assert calls == 1 and dlq_entries(v) == [] and times_delivered(v) == [0] * 5


async def test_operational_error_during_isolation_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    v = vid()
    produce(v, 3)
    entries = read_without_ack(v, consumer="c1")
    errors: list[Exception] = [ValueError("bad frame"), psycopg.errors.AdminShutdown("bye")]

    async def failing(*_: object) -> list[FrameHeader]:
        raise errors.pop(0)

    monkeypatch.setattr(worker_mod, "insert_frames", failing)
    with pytest.raises(OperationalDbError, match="57P01"):
        await process_batch_once(v, entries)
    assert stored(v) == 0 and dlq_entries(v) == [] and times_delivered(v) == [0] * 3


async def test_reset_skipped_when_redis_fails_too(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    v = vid()
    produce(v, 2)
    entries = read_without_ack(v, consumer="c1")

    async def shutdown(*_: object) -> list[FrameHeader]:
        raise psycopg.errors.AdminShutdown("bye")

    async def redis_down(*_: object, **__: object) -> None:
        raise redis.exceptions.ConnectionError("redis down")

    monkeypatch.setattr(worker_mod, "insert_frames", shutdown)
    monkeypatch.setattr(redis.asyncio.Redis, "xclaim", redis_down)
    with pytest.raises(OperationalDbError):
        await process_batch_once(v, entries)
    assert "could not reset delivery counts" in caplog.text and times_delivered(v) == [1, 1]


async def test_connect_error_is_operational() -> None:
    with pytest.raises(OperationalDbError, match="no_such_db"):
        async with worker([vid()], dsn=app_dsn("no_such_db")) as w:
            await asyncio.wait_for(w.tasks[0], 10)
