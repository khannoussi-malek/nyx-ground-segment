import json

from gs.common.interfaces import DlqRecord
from tests.support.env import until, vid
from tests.support.ingest import process_batch_once, worker
from tests.support.pg import stored
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
