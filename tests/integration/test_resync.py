"""Snapshots: the subscribe/snapshot race, resync entries, and what a snapshot carries."""

import json
import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from gs.api import events
from gs.api import hub as hub_mod
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
    assert len(snap["commands"]) <= 50 and len(snap["events"]) <= hub_mod.SNAPSHOT_EVENTS + 1
    kinds = {e["kind"] for e in snap["events"] if e["vehicle"] == v}
    assert {"FROZEN", "LIMITS_APPLIED"} <= kinds
    tm = snap["vehicles"][v]
    assert (tm["seq"], tm["frozen"]) == (29, True)


async def test_snapshot_carries_each_vehicles_open_los_past_the_newest_events() -> None:
    """An old open LOS (it carries its last frame's time) falls out of the newest SNAPSHOT_EVENTS
    after enough other events; the snapshot still carries each vehicle's newest LOS/AOS event, so
    a console never hides or un-tones a loss the server keeps open (item E)."""
    v, w, t = vid(), vid(), now() - timedelta(minutes=10)  # past times: other tests' newest win
    los = GroundEvent(f"LOS/{v}/old", v, "LOS", t - timedelta(hours=1), {})
    newer = [
        GroundEvent(f"GAP/{w}/{i}", w, "GAP", t + timedelta(seconds=i), {})
        for i in range(hub_mod.SNAPSHOT_EVENTS)
    ]
    aos_w = GroundEvent(f"AOS/{w}/new", w, "AOS", t + timedelta(minutes=1), {})
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as ac:
        await insert_events(ac, [los, *newer, aos_w])
        snap = await hub_mod.build_snapshot(ac, [v, w], [TEMP], now())
    keys = [e.key for e in snap.events]
    assert los.key not in keys[: hub_mod.SNAPSHOT_EVENTS] and keys.count(los.key) == 1
    assert keys.count(aos_w.key) == 1  # once, whether among the newest or carried
    assert keys[-1] == los.key  # older than every newest event: newest first is kept
