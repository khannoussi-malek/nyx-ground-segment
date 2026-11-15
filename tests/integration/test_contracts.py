import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jsonschema

from gs.api.app import create_app
from tests.support.env import REPO, until, vid
from tests.support.http import api_settings, authed
from tests.support.ingest import worker
from tests.support.redisx import (
    client,
    dlq_entries,
    drained,
    frame,
    produce,
    stream_entries,
    ui_tm,
    xadd_raw,
)
from tests.support.sim import post_fault, running_sim, send_tc, tcacks

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


def _vehicle_of(m: dict[str, Any]) -> str | None:
    """tm and resync carry the vehicle at the top level, the others in their payload."""
    inner = {"alarm": "alarm", "cmd": "command", "link": "event"}.get(m["kind"])
    value = (m[inner] if inner else m).get("vehicle")
    return value if isinstance(value, str) else None


def _last_id(stream: str) -> str:
    newest: Any = client("admin").xrevrange(stream, count=1)
    return str(newest[0][0]) if newest else "0-0"


async def _post_iso(c: httpx.AsyncClient, v: str) -> str:
    r = await c.post(
        "/api/commands", json={"vehicle": v, "name": "isolate_valve", "args": {"valve": "A"}}
    )
    assert r.status_code == 202, r.text
    return str(r.json()["cmd_id"])


async def test_tc_tcack_match_schema(users_file: Path) -> None:  # D-EXT-18
    v = vid()
    app = create_app(dataclasses.replace(api_settings(users_file), vehicles=(v,)))
    async with running_sim(v), authed(app) as c:
        await _post_iso(c, v)  # API-produced tc, acked OK
        await asyncio.to_thread(send_tc, v, "vent_cabin", {"duration_s": 11})  # INVALID_ARGS
        client("api").xadd(f"tc:{v}", {"data": json.dumps({"cmd_id": str(uuid4())})})  # SCHEMA
        await until(lambda: len(tcacks(v)) == 3, 3)
    tc_schema, ack_schema = _validator("tc.schema.json"), _validator("tcack.schema.json")
    tcs = [json.loads(f["data"]) for _, f in stream_entries(f"tc:{v}")]
    tc_schema.validate(tcs[0])  # the API's; the other two are hand-made inputs
    for a in tcacks(v):
        ack_schema.validate(a)
    assert sorted(a["code"] for a in tcacks(v)) == ["INVALID_ARGS", "OK", "SCHEMA"]


async def test_ui_events_match_schema(users_file: Path) -> None:  # D-EXT-18
    v = vid()
    start = _last_id("ui:events")
    app = create_app(dataclasses.replace(api_settings(users_file), vehicles=(v,)))

    def mine() -> list[dict[str, Any]]:
        got: Any = client("admin").xrange("ui:events", min=f"({start}")
        msgs = [json.loads(f["data"]) for _, f in got]
        return [m for m in msgs if _vehicle_of(m) == v]

    def kinds() -> set[str]:
        return {m["kind"] for m in mine()}

    async with running_sim(v) as port, worker([v]), authed(app) as c:
        await until(lambda: {"resync", "tm"} <= kinds(), 5)
        step = {"param": "cabin_temp_c", "value": 31.0, "duration_s": 2}
        assert await asyncio.to_thread(post_fault, v, "step", port, **step) == 204
        await until(lambda: "alarm" in kinds(), 5)
        assert await asyncio.to_thread(post_fault, v, "seq_jump", port) == 204
        await until(lambda: "link" in kinds(), 5)
        cmd_id = await _post_iso(c, v)
        await until(
            lambda: any(m["kind"] == "cmd" and m["command"]["cmd_id"] == cmd_id for m in mine()), 5
        )
    msgs = mine()
    assert {m["kind"] for m in msgs} == {"tm", "alarm", "cmd", "link", "resync"}
    validator = _validator("ui-event.schema.json")
    for m in msgs:
        validator.validate(m)
