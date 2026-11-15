"""GET /api/events against a real uvicorn server: order, deltas, heartbeat, auth, shutdown."""

import signal
import time
from typing import Any

import httpx
import pytest

from gs.api import events
from tests.integration.conftest import LiveApi
from tests.support.env import until, vid
from tests.support.redisx import client
from tests.support.sse import Stream, publish_tm, seed_row, sse, validate


async def tm_of(s: Stream, v: str) -> dict[str, Any]:
    """The next tm of `v`: a running stack's simulator publishes other vehicles' tm too."""
    while (d := (await s.next_of("tm"))[1])["vehicle"] != v:
        pass
    return d


async def test_hello_snapshot_then_deltas(live_api: LiveApi) -> None:
    v = live_api.vehicle
    seed_row(v, seq=0)
    async with sse(live_api) as s:
        assert [e for e, _ in await s.take(2)] == ["hello", "snapshot"]
        assert s.last["vehicles"][v]["seq"] == 0 and s.last["vehicles"][v]["age_ms"] >= 0
        publish_tm(v, seq=1)
        d = await tm_of(s, v)
        assert (d["vehicle"], d["seq"]) == (v, 1) and d["age_ms"] is not None
        for kind, data in s.seen:
            validate(kind, data)


async def test_snapshot_is_latest_row_per_vehicle(live_api: LiveApi) -> None:
    v = live_api.vehicle
    for seq in (0, 1, 2):
        seed_row(v, seq)
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        assert s.last["vehicles"][v]["seq"] == 2 and len(s.last["vehicles"]) == 1


async def test_event_committed_during_snapshot_is_a_delta(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig = events.build_snapshot

    v = vid()

    async def racing(*a, **k):  # type: ignore[no-untyped-def]
        publish_tm(v, seq=7)
        return await orig(*a, **k)

    monkeypatch.setattr(events, "build_snapshot", racing)
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        assert (await tm_of(s, v))["seq"] == 7


async def test_first_boot_empty_stream_and_no_rows(live_api: LiveApi) -> None:
    v = live_api.vehicle
    client("admin").delete("ui:events")
    async with sse(live_api) as s:
        assert (await s.next_of("snapshot"))[1]["vehicles"] == {}
        publish_tm(v, seq=0)
        assert (await tm_of(s, v))["seq"] == 0


async def test_heartbeat_every_second(live_api: LiveApi) -> None:
    async with sse(live_api) as s:
        t0 = time.monotonic()
        await s.next_of("hb")
        t1 = time.monotonic()
        await s.next_of("hb")
        t2 = time.monotonic()
    assert t2 - t0 < 2.5 and 0.8 < t2 - t1 < 1.3


async def test_events_requires_a_session(live_api: LiveApi) -> None:
    async with httpx.AsyncClient(base_url=live_api.url) as c:
        r = await c.get("/api/events")
    assert r.status_code == 401 and r.json()["error"] == "UNAUTHENTICATED"


async def test_disconnect_releases_stream(live_api: LiveApi) -> None:
    for _ in range(10):
        async with sse(live_api) as s:
            await s.next_of("snapshot")
    await until(lambda: live_api.app.state.sse_open == 0, timeout=2)
    # no per-stream XREAD left holding a pooled redis connection: only the hub's one reader
    pool = live_api.app.state.redis.connection_pool
    await until(lambda: len(pool._in_use_connections) <= 1, timeout=2)
    assert not live_api.app.state.hub.clients


async def test_shutdown_hook_ends_streams(live_api: LiveApi) -> None:
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        t = time.monotonic()
        live_api.server.handle_exit(signal.SIGTERM, None)
        await s.until_closed(timeout=1.5)
        assert time.monotonic() - t < 0.6  # plain uvicorn waits the full graceful timeout (1 s)
