"""The API's one ui:events reader (Task 11): rejection, slow clients, broker state, heartbeats."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
import redis.exceptions

from gs.api.hub import Hub
from gs.common.interfaces import UiEvent
from gs.common.interfaces.render import schema_sha12
from gs.ingest.events import mismatch_key
from tests.integration.conftest import LiveApi
from tests.support.env import now, until
from tests.support.ingest import worker
from tests.support.pg import events_for, open_alarm
from tests.support.redisx import client, dlq_since, drained, produce_values, xadd_data
from tests.support.sse import publish_tm, sse

TEMP = "cabin_temp_c"


def hub(live: LiveApi) -> Hub:
    h: Hub = live.app.state.hub
    return h


async def test_invalid_ui_event_dlq(live_api: LiveApi) -> None:  # D-ING-27, requirements name
    t0 = now()
    async with sse(live_api) as s1, sse(live_api) as s2:
        await s1.next_of("snapshot")
        await s2.next_of("snapshot")
        xadd_data("ui:events", '{"kind":"tm","vehicle":"NYX-TA1"}')
        publish_tm(live_api.vehicle, 1)
        assert (await s1.next_of("tm"))[1]["seq"] == 1 and (await s2.next_of("tm"))[1]["seq"] == 1
    (rec,) = dlq_since("ui:events", t0)
    assert '"reason":"schema"' in rec and '"stream":"ui:events"' in rec
    key = mismatch_key("ui-event", schema_sha12(UiEvent))  # once per schema version, ever
    (ev,) = [e for e in events_for(None, "INTERFACE_MISMATCH") if e["key"] == key]
    assert (ev["details"]["stream"], ev["details"]["consumer"]) == ("ui:events", "api")
    async with httpx.AsyncClient(base_url=live_api.url) as c:
        health = (await c.get("/healthz")).json()
    assert health["status"] == "degraded" and health["checks"]["ui_events"] == "degraded"


async def test_slow_client_overflow_closes_then_snapshot(live_api: LiveApi) -> None:
    v, base = live_api.vehicle, now() - timedelta(minutes=1)  # Review Focus 5
    produce_values(v, {TEMP: [22.0] * 5 + [31.0] * 5}, base=base)
    async with worker([v]):
        await until(lambda: drained(v) and open_alarm(v, TEMP) is not None, 5)
    h = hub(live_api)
    async with sse(live_api) as fast:
        await fast.next_of("snapshot")
        h.queue_size = 5
        slow = h.subscribe()  # a client whose SSE loop never takes from its queue
        for seq in range(20):
            publish_tm(v, 100 + seq)
        for seq in range(20):
            assert (await fast.next_of("tm"))[1]["seq"] == 100 + seq  # the others keep going
    assert slow.closed.is_set() and slow not in h.clients and slow.queue.qsize() == 5
    async with sse(live_api) as again:  # the closed client's watchdog reconnects
        _, snap = await again.next_of("snapshot")
    assert [(a["param"], a["state"]) for a in snap["alarms"]] == [(TEMP, "UNACK")]


async def test_closed_client_ends_its_stream(live_api: LiveApi) -> None:
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        (c,) = hub(live_api).clients
        c.closed.set()  # what an overflow does
        await s.until_closed(timeout=1.5)
    await until(lambda: not hub(live_api).clients, 2)


async def test_broker_down_ground_then_up_and_snapshot(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = live_api.app.state.redis
    real, fails = r.xread, [3]

    async def flaky(*a: Any, **k: Any) -> Any:
        if fails[0]:
            fails[0] -= 1
            raise redis.exceptions.ConnectionError("test: broker down")
        return await real(*a, **k)

    async with sse(live_api) as s:
        await s.next_of("snapshot")
        monkeypatch.setattr(r, "xread", flaky)
        assert (await s.next_of("ground", timeout=4))[1]["broker"] == "down"
        assert not hub(live_api).broker_up
        await s.next_of("hb", timeout=2)  # the stream itself stays alive
        assert (await s.next_of("ground", timeout=6))[1]["broker"] == "up"
        await s.next_of("snapshot")
    assert hub(live_api).broker_up and fails == [0]


async def test_down_broker_on_connect_sends_ground_down(live_api: LiveApi) -> None:
    hub(live_api).broker_up = False  # as after a failed XREAD
    async with sse(live_api) as s:
        kinds = [k for k, _ in await s.take(3)]
    assert kinds == ["hello", "snapshot", "ground"] and s.last["broker"] == "down"


async def test_hb_carries_processing(live_api: LiveApi) -> None:  # D-UI-15, D-ING-25
    v = live_api.vehicle
    client("ingest").set(f"hb:veh:{v}", 1, ex=2)
    async with sse(live_api) as s:

        async def hb_until(want: bool) -> None:
            async with asyncio.timeout(5):
                while (await s.next_of("hb", timeout=5))[1]["processing"].get(v) is not want:
                    pass

        await hb_until(True)
        await hb_until(False)  # expired: no healthy owner
