"""Snapshots: the subscribe/snapshot race, resync entries, and what a snapshot carries."""

import json
import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from gs.api import events
from gs.ingest.events import GroundEvent, insert_events
from tests.integration.conftest import LiveApi
from tests.support.env import now, until, vid
from tests.support.ingest import worker
from tests.support.pg import app_dsn, events_for, open_alarm
from tests.support.redisx import alarm_msgs, client, drained, produce_values
from tests.support.sse import sse, validate

TEMP = "cabin_temp_c"
RED_STEP = {TEMP: [22.0] * 5 + [31.0] * 5}


def alarm_keys(seen: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Transition keys a console would see: every snapshot's alarms and every alarm delta."""
    keys = [a["last"]["key"] for k, d in seen if k == "snapshot" for a in d["alarms"]]
    return keys + [d["alarm"]["last"]["key"] for k, d in seen if k == "alarm"]


async def test_snapshot_race_no_missed_alarm(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # R2-08: committed and published between subscribe() and the snapshot query
    v = live_api.vehicle
    produce_values(v, RED_STEP)
    orig = events.build_snapshot

    async def racing(*a: Any, **k: Any) -> Any:
        async with worker([v]):
            await until(lambda: bool(alarm_msgs(v)), 5)
        return await orig(*a, **k)

    monkeypatch.setattr(events, "build_snapshot", racing)
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        await s.next_of("alarm")
        seen = s.seen
    keys = alarm_keys(seen)
    (raise_key,) = set(keys)  # after dedupe by transition key: exactly once
    assert raise_key.endswith("/RAISE") and len(keys) >= 2  # in the snapshot and as a delta


async def test_resync_entry_sends_snapshot(live_api: LiveApi) -> None:  # D-UI-18
    v = live_api.vehicle
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        client("ingest").xadd("ui:events", {"data": json.dumps({"kind": "resync", "vehicle": v})})
        _, snap = await s.next_of("snapshot")
    validate("snapshot", snap)
    assert not [k for k, _ in s.seen if k == "resync"]  # a resync is never forwarded as such


async def test_snapshot_has_alarms_commands_events_frozen(live_api: LiveApi) -> None:  # R-e, R-r
    v, base = live_api.vehicle, now() - timedelta(minutes=1)
    produce_values(v, {TEMP: [22.0] * 5 + [31.0] * 25}, base=base)  # RED, then 2.5 s frozen
    async with worker([v]):
        await until(lambda: drained(v) and bool(events_for(v, "FROZEN")), 5)
    w = vid()  # a second vehicle: commands and events are the newest overall (R-r, as written)
    cmds = [uuid.uuid4(), uuid.uuid4()]
    with psycopg.connect(app_dsn("gs")) as c:
        for cmd_id, veh in zip(cmds, (v, w), strict=True):  # w's is the newer
            c.execute(
                "insert into commands (cmd_id, vehicle, name, args, args_hash, hazardous,"
                " issued_by, state, queued_at) values (%s, %s, 'vent_cabin',"
                " '{\"duration_s\": 2}', 'h', true, 'fc1', 'QUEUED', clock_timestamp())",
                (cmd_id, veh),
            )
    keys = [f"TEST/{v}/older", f"TEST/{w}/newer"]
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as ac:
        for key, veh in zip(keys, (v, w), strict=True):
            await insert_events(ac, [GroundEvent(key, veh, "LOS", None, {})])
    async with sse(live_api) as s:
        _, snap = await s.next_of("snapshot")
    validate("snapshot", snap)
    a = open_alarm(v, TEMP)
    assert a is not None and [x["alarm_id"] for x in snap["alarms"]] == [str(a["alarm_id"])]
    assert [c["cmd_id"] for c in snap["commands"][:2]] == [str(cmds[1]), str(cmds[0])]
    assert [e["key"] for e in snap["events"][:2]] == [keys[1], keys[0]]  # newest first
    assert len(snap["commands"]) <= 50 and len(snap["events"]) <= 50
    kinds = {e["kind"] for e in snap["events"] if e["vehicle"] == v}
    assert {"FROZEN", "LIMITS_APPLIED"} <= kinds
    tm = snap["vehicles"][v]
    assert (tm["seq"], tm["frozen"]) == (29, True)
