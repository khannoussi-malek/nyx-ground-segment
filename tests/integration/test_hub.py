"""The API's one ui:events reader (Task 11): rejection, slow clients, broker state, heartbeats."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest
import redis.exceptions

from gs.api.hub import Hub
from gs.common.interfaces import UiEvent
from gs.common.interfaces.render import schema_sha12
from gs.ingest.events import mismatch_key
from tests.integration.conftest import LiveApi
from tests.support.compose import compose, wait_healthy
from tests.support.env import now, until
from tests.support.ingest import worker
from tests.support.pg import admin_dsn, events_for, open_alarm
from tests.support.redisx import client, dlq_since, drained, produce_values, xadd_data
from tests.support.sse import Stream, publish_tm, sse

TEMP = "cabin_temp_c"


async def tm_of(s: Stream, v: str) -> dict[str, Any]:
    """The next tm of `v`: a running stack's simulator publishes other vehicles' tm too."""
    while (d := (await s.next_of("tm"))[1])["vehicle"] != v:
        pass
    return d


async def frames_until_tm(s: Stream, v: str) -> list[tuple[str, dict[str, Any]]]:
    """Every frame up to and including the next tm of `v`."""
    got: list[tuple[str, dict[str, Any]]] = []
    async with asyncio.timeout(4):
        while not ((f := await s._next())[0] == "tm" and f[1]["vehicle"] == v):  # noqa: SLF001
            got.append(f)
    return [*got, f]


def hub(live: LiveApi) -> Hub:
    h: Hub = live.app.state.hub
    return h


@pytest.fixture
def compose_api_stopped() -> Iterator[None]:
    """The compose api reads ui:events too: it would DLQ the same bad entry and could win the
    once-per-schema INTERFACE_MISMATCH insert. Stopped for the test (this project's api only),
    started again after; the result no longer depends on whether an earlier test stopped it."""
    compose("stop", "api")
    try:
        yield
    finally:
        compose("start", "api")
        wait_healthy("api")


@pytest.mark.usefixtures("compose_api_stopped")
async def test_invalid_ui_event_dlq(live_api: LiveApi) -> None:  # D-ING-27, requirements name
    t0 = now()
    async with sse(live_api) as s1, sse(live_api) as s2:
        await s1.next_of("snapshot")
        await s2.next_of("snapshot")
        xadd_data("ui:events", '{"kind":"tm","vehicle":"NYX-TA1"}')
        publish_tm(live_api.vehicle, 1)
        v = live_api.vehicle
        assert (await tm_of(s1, v))["seq"] == 1 and (await tm_of(s2, v))["seq"] == 1
    (rec,) = dlq_since("ui:events", t0)
    assert '"reason":"schema"' in rec and '"stream":"ui:events"' in rec
    key = mismatch_key("ui-event", schema_sha12(UiEvent))  # once per schema version, ever
    (ev,) = [e for e in events_for(None, "INTERFACE_MISMATCH") if e["key"] == key]
    assert (ev["details"]["stream"], ev["details"]["consumer"]) == ("ui:events", "api")
    async with httpx.AsyncClient(base_url=live_api.url) as c:
        health = (await c.get("/healthz")).json()
    assert health["status"] == "degraded" and health["checks"]["ui_events"] == "degraded"


@pytest.mark.usefixtures("compose_api_stopped")
async def test_rejected_entry_gets_its_dlq_record_after_a_redis_failure(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # assumptions.md Known minors, API (b): the entry is re-read, not skipped
    r = live_api.app.state.redis
    real, left = r.xadd, [1]

    async def flaky(name: str, *a: Any, **k: Any) -> Any:
        if name == "tm:dlq" and left[0]:
            left[0] -= 1
            raise redis.exceptions.ConnectionError("test: injected")
        return await real(name, *a, **k)

    monkeypatch.setattr(r, "xadd", flaky)
    t0 = now()
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        bad = xadd_data("ui:events", '{"kind":"no-such-kind","marker":"dlq-retry"}')
        await until(lambda: left == [0], 4)
        publish_tm(live_api.vehicle, 1)
        assert (await tm_of(s, live_api.vehicle))["seq"] == 1  # the next entry still arrives
    recs = [x for x in dlq_since("ui:events", t0) if f'"entry_id":"{bad}"' in x]
    assert len(recs) == 1


async def test_non_redis_error_in_handle_does_not_pin_the_stream(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # D-ING-27: the stream carries on; only a Redis failure holds the reader
    h = hub(live_api)
    real, left = h._handle, [0]  # noqa: SLF001

    async def flaky(entry_id: str, data: str) -> None:
        if "poison" in data:  # deterministic: a retry would raise again
            left[0] += 1
            raise RuntimeError("test: handler bug")
        await real(entry_id, data)

    monkeypatch.setattr(h, "_handle", flaky)
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        xadd_data("ui:events", '{"kind":"poison"}')
        await until(lambda: left[0] >= 1, 4)
        publish_tm(live_api.vehicle, 1)
        frames = await frames_until_tm(s, live_api.vehicle)
    assert frames[-1][1]["seq"] == 1
    assert not [f for f in frames if f[0] == "ground" and f[1]["broker"] == "down"]  # no flash


@pytest.mark.usefixtures("compose_api_stopped")
async def test_non_transient_redis_error_on_the_dlq_write_does_not_pin_the_stream(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # only a connection error or timeout holds the reader; OOM / NOPERM skip the entry
    r = live_api.app.state.redis
    real, hits = r.xadd, [0]

    async def oom(name: str, *a: Any, **k: Any) -> Any:
        if name == "tm:dlq":
            hits[0] += 1
            raise redis.exceptions.ResponseError("OOM command not allowed (test)")
        return await real(name, *a, **k)

    monkeypatch.setattr(r, "xadd", oom)
    async with sse(live_api) as s:
        await s.next_of("snapshot")
        xadd_data("ui:events", '{"kind":"no-such-kind","marker":"oom"}')
        await until(lambda: hits[0] >= 1, 4)
        publish_tm(live_api.vehicle, 1)
        frames = await frames_until_tm(s, live_api.vehicle)
    assert frames[-1][1]["seq"] == 1 and hits[0] == 1  # skipped once, not re-read
    assert not [f for f in frames if f[0] == "ground" and f[1]["broker"] == "down"]


async def test_resync_with_a_failed_snapshot_build_is_retried(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # the reload is not lost: the HB-paced _resnap retry delivers it
    h = hub(live_api)
    real, left = h.snapshot, [1]

    async def flaky() -> Any:
        if left[0]:
            left[0] -= 1
            raise psycopg.OperationalError("test: database down")
        return await real()

    async with sse(live_api) as s:
        await s.next_of("ground")
        monkeypatch.setattr(h, "snapshot", flaky)
        xadd_data("ui:events", f'{{"kind":"resync","vehicle":"{live_api.vehicle}"}}')
        await s.next_of("snapshot", timeout=4)
    assert left == [0]


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
            # the worker's own tm (seq 0..9) can still be in the hub's read when `fast` connects
            while (got := await tm_of(fast, v))["seq"] < 100:
                pass
            assert got["seq"] == 100 + seq  # the others keep going
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
        assert (await s.next_of("ground"))[1]["broker"] == "up"  # sent on every connect
        monkeypatch.setattr(r, "xread", flaky)
        assert (await s.next_of("ground", timeout=4))[1]["broker"] == "down"
        assert not hub(live_api).broker_up
        await s.next_of("hb", timeout=2)  # the stream itself stays alive
        assert (await s.next_of("ground", timeout=6))[1]["broker"] == "up"
        await s.next_of("snapshot")
    assert hub(live_api).broker_up and fails == [0]


def failing_xread(
    live: LiveApi, monkeypatch: pytest.MonkeyPatch, times: int, exc: type[Exception]
) -> list[int]:
    """The hub's next `times` XREADs raise `exc`; returns the countdown."""
    r = live.app.state.redis
    real, left = r.xread, [times]

    async def flaky(*a: Any, **k: Any) -> Any:
        if left[0]:
            left[0] -= 1
            raise exc("test: injected")
        return await real(*a, **k)

    monkeypatch.setattr(r, "xread", flaky)
    return left


async def test_down_broker_on_connect_sends_ground_down(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing_xread(live_api, monkeypatch, 1000, redis.exceptions.ConnectionError)
    await until(lambda: not hub(live_api).broker_up, 3)
    async with sse(live_api) as s:
        kinds = [k for k, _ in await s.take(3)]
    assert kinds == ["hello", "snapshot", "ground"] and s.last["broker"] == "down"


async def test_connect_always_sends_the_broker_state(live_api: LiveApi) -> None:
    async with sse(live_api) as s:  # a stale BROKER banner must clear on reconnect
        kinds = [k for k, _ in await s.take(3)]
    assert kinds == ["hello", "snapshot", "ground"] and s.last["broker"] == "up"


async def test_reader_exception_is_ground_down_then_up(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # Task 11 review: never a silent reader
    async with sse(live_api) as s:
        await s.next_of("ground")
        left = failing_xread(live_api, monkeypatch, 2, RuntimeError)
        assert (await s.next_of("ground", timeout=4))[1]["broker"] == "down"
        assert (await s.next_of("ground", timeout=6))[1]["broker"] == "up"
        await s.next_of("snapshot")
    assert left == [0] and hub(live_api).up()


async def test_stalled_reader_is_ground_down_and_degraded(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = live_api.app.state.redis

    async def hang(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(30)  # a reader that never returns

    async with sse(live_api) as s:
        await s.next_of("ground")
        monkeypatch.setattr(r, "xread", hang)
        assert (await s.next_of("ground", timeout=5))[1]["broker"] == "down"  # STALE_S + 1 hb
    async with httpx.AsyncClient(base_url=live_api.url) as c:
        health = (await c.get("/healthz")).json()
    assert (health["status"], health["checks"]["ui_events"]) == ("degraded", "stalled")


@pytest.mark.usefixtures("compose_api_stopped")
async def test_mismatch_event_reaches_the_console(live_api: LiveApi) -> None:
    key = mismatch_key("ui-event", schema_sha12(UiEvent))  # written once per schema version:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:  # clear an earlier run's row
        c.execute("delete from events where key = %s", (key,))
    async with sse(live_api) as s:
        await s.next_of("ground")
        xadd_data("ui:events", '{"kind":"no-such-kind"}')  # no helper selects this kind
        _, link = await s.next_of("link")
        await s.next_of("snapshot")  # restores what the dropped entry may have carried
    assert (link["event"]["key"], link["event"]["kind"]) == (key, "INTERFACE_MISMATCH")


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


async def test_every_rejected_entry_restores_with_a_snapshot(live_api: LiveApi) -> None:
    async with sse(live_api) as s:  # fix round 3, item 2: the mismatch row exists after one
        await s.next_of("ground")
        xadd_data("ui:events", '{"kind":"no-such-kind"}')
        await s.next_of("snapshot", timeout=4)
        n = len(s.seen)
        for _ in range(2):  # a lasting mismatch: each dropped entry may have been an alarm
            xadd_data("ui:events", '{"kind":"no-such-kind"}')
        await s.next_of("snapshot", timeout=4)
        later = [k for k, _ in s.seen[n:]]
    assert "link" not in later  # the event row is written once per schema version


async def test_rejected_entry_snapshot_retried_after_a_db_error(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # 0d24090 re-review, item 1: the flag survives an unreachable database
    h = hub(live_api)
    real, left = h.snapshot, [1]

    async def flaky() -> Any:
        if left[0]:
            left[0] -= 1
            raise psycopg.OperationalError("test: database down")
        return await real()

    async with sse(live_api) as s:
        await s.next_of("ground")
        monkeypatch.setattr(h, "snapshot", flaky)
        xadd_data("ui:events", '{"kind":"no-such-kind"}')
        await s.next_of("snapshot", timeout=5)
    assert left == [0]
