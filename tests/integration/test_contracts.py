import json

import jsonschema

from tests.support.env import REPO, until, vid
from tests.support.ingest import worker
from tests.support.redisx import dlq_entries, drained, frame, produce, ui_tm, xadd_raw

SCHEMAS = REPO / "interfaces" / "schemas"


def _validator(rel: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads((SCHEMAS / rel).read_text()))


async def test_dlq_records_match_schema() -> None:
    v = vid()
    xadd_raw(v, b"not json")
    xadd_raw(v, json.dumps({"vehicle": v}))
    xadd_raw(v, frame("NYX-OTHER", 0))
    async with worker([v]):
        await until(lambda: drained(v), timeout=5)
    records = dlq_entries(v)
    assert len(records) == 3
    validator = _validator("dlq.schema.json")
    for r in records:
        validator.validate(json.loads(r))


async def test_ui_events_tm_match_schema() -> None:
    v = vid()
    produce(v, 5)
    async with worker([v]):
        await until(lambda: drained(v), timeout=5)
    events = [json.loads(d) for d in ui_tm(v)]
    assert len(events) == 5
    validator = _validator("sse/tm.schema.json")
    for e in events:
        validator.validate(e)
        assert e.get("age_ms") is None
