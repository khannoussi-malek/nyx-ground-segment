"""POST/GET/DELETE /api/replays against the in-process live server, and the live-stream vehicle
filter (Track RP Task 13). Rows are seeded hot (or archived) for a vid() vehicle."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg_pool import PoolTimeout

from gs.api import replays
from gs.archiver.hours import HOUR, hour_of, object_key
from gs.archiver.s3 import get as real_get
from tests.integration.conftest import LiveApi
from tests.integration.test_window_reader import archive_and_delete, flip
from tests.support.archive import (  # noqa: F401  (archiver_paused is the module fixture)
    app_conn,
    archiver_paused,
    hot_hour,
)
from tests.support.env import until, vid
from tests.support.http import CLIENT_HEADERS, FC2_PW, login
from tests.support.pg import admin_dsn
from tests.support.s3 import s3_client

pytestmark = pytest.mark.usefixtures("archiver_paused")
H = hour_of(datetime.now(UTC)) - timedelta(hours=40)
SPAN = timedelta(seconds=30)
_made: list[str] = []


def vehicle() -> str:
    _made.append(v := vid())
    return v


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    root = s3_client("root")
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        for v in _made:
            for t in ("archive_object", "telemetry"):
                c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
            for o in root.list_objects("archive", prefix=f"{v}/", recursive=True):
                root.remove_object("archive", o.object_name)
    _made.clear()


def body(
    v: str, t_from: datetime = H, t_to: datetime = H + SPAN, speed: int = 10
) -> dict[str, Any]:
    return {"vehicle": v, "from": t_from.isoformat(), "to": t_to.isoformat(), "speed": speed}


@asynccontextmanager
async def client(live: LiveApi, cookie: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=live.url, cookies={"session": cookie or live.cookie}, headers=CLIENT_HEADERS
    ) as c:
        yield c


async def create(c: httpx.AsyncClient, payload: dict[str, Any]) -> str:
    r = await c.post("/api/replays", json=payload)
    assert r.status_code == 201, r.text
    return str(r.json()["replay_id"])


async def frames(r: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    kind = ""
    async for line in r.aiter_lines():
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            yield kind, json.loads(line[6:])


def registry(live: LiveApi) -> dict[Any, Any]:
    reg: dict[Any, Any] = live.app.state.replays
    return reg


async def test_create_and_stream_hot_window(live_api: LiveApi) -> None:
    v = vehicle()
    hot_hour(v, H, n=40, rate_hz=1.0)  # 10 s of it lie outside the window
    async with client(live_api) as c:
        rid = await create(c, body(v))
        async with c.stream("GET", f"/api/replays/{rid}/events", timeout=20) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            got = [f async for f in frames(r)]
    kinds = [k for k, _ in got]
    assert kinds[0] == "replay_start" and kinds[-1] == "replay_end"
    assert got[-1][1]["reason"] == "done"
    assert [d["seq"] for k, d in got if k == "tm"] == list(range(30))
    assert all(d["replay"] is True for _, d in got)
    assert registry(live_api) == {}


async def test_no_data_404(live_api: LiveApi) -> None:
    async with client(live_api) as c:
        r = await c.post("/api/replays", json=body(vehicle()))
    assert r.status_code == 404
    assert r.json() == {"error": "NO_DATA", "message": "no data in window"}


async def test_corrupt_hour_409(live_api: LiveApi) -> None:
    v = vehicle()
    hot_hour(v, H, n=50, rate_hz=1.0)
    async with app_conn() as conn:
        await archive_and_delete(conn, v, H)
    flip(object_key(v, H))
    async with client(live_api) as c:
        r = await c.post("/api/replays", json=body(v))
    assert r.status_code == 409
    assert r.json() == {"error": "ARCHIVE_CORRUPT", "message": "archive object failed hash check"}
    assert registry(live_api) == {}


async def test_window_rules_400(live_api: LiveApi) -> None:
    v = vehicle()
    async with client(live_api) as c:
        same = await c.post("/api/replays", json=body(v, H, H))
        back = await c.post("/api/replays", json=body(v, H + SPAN, H))
        big = await c.post("/api/replays", json=body(v, H, H + timedelta(hours=24, seconds=1)))
    assert [r.status_code for r in (same, back, big)] == [400, 400, 400]
    assert [r.json()["error"] for r in (same, back, big)] == [
        "INVALID_WINDOW", "INVALID_WINDOW", "WINDOW_TOO_LARGE",
    ]  # fmt: skip


async def test_other_user_404_and_second_get_404(live_api: LiveApi) -> None:
    v = vehicle()
    hot_hour(v, H, n=30, rate_hz=1.0)
    async with client(live_api) as c, httpx.AsyncClient(base_url=live_api.url) as other:
        assert (await login(other, "fc2", FC2_PW)).status_code == 204
        o = httpx.AsyncClient(base_url=live_api.url, cookies=other.cookies, headers=CLIENT_HEADERS)
        try:
            rid = await create(c, body(v, speed=1))
            url = f"/api/replays/{rid}/events"
            assert (await o.get(url)).status_code == 404
            assert (await o.delete(f"/api/replays/{rid}")).status_code == 404
            async with c.stream("GET", url, timeout=20) as first:
                assert first.status_code == 200
                assert (await c.get(url)).status_code == 404  # taken by the first GET
                assert (await c.get(f"/api/replays/{uuid_zero()}/events")).status_code == 404
            # the disconnect cancels the stream at once; 10 s for a slow runner, far below the 60 s
            # no-reader timeout that would also empty it
            await until(lambda: registry(live_api) == {}, 10)
        finally:
            await o.aclose()


async def test_third_open_replay_is_refused(live_api: LiveApi) -> None:  # Task 13 M4
    v = vehicle()
    hot_hour(v, H, n=30, rate_hz=1.0)
    async with client(live_api) as c:
        first, second = await create(c, body(v)), await create(c, body(v))
        r = await c.post("/api/replays", json=body(v))
        assert r.status_code == 429
        assert r.json() == {
            "error": "TOO_MANY_REPLAYS",
            "message": "at most 2 replays at once; stop one first",
        }
        assert (await c.delete(f"/api/replays/{first}")).status_code == 204
        await create(c, body(v))  # stopping one frees a slot
        assert (await c.delete(f"/api/replays/{second}")).status_code == 204


def uuid_zero() -> str:
    return "00000000-0000-0000-0000-000000000000"


async def test_delete_stops_replay(live_api: LiveApi) -> None:
    v = vehicle()
    hot_hour(v, H, n=30, rate_hz=1.0)
    async with client(live_api) as c:
        rid = await create(c, body(v, speed=1))
        async with c.stream("GET", f"/api/replays/{rid}/events", timeout=20) as r:
            it = frames(r)
            assert (await anext(it))[0] == "replay_start"
            assert (await c.delete(f"/api/replays/{rid}")).status_code == 204
            rest = [f async for f in it]
        assert rest[-1][0] == "replay_end" and rest[-1][1]["reason"] == "stopped"
        assert registry(live_api) == {}
        assert (await c.delete(f"/api/replays/{rid}")).status_code == 404


async def test_disconnect_stops_replay(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    v = vehicle()
    for h in (H, H + HOUR):  # one hour's gap after the first: the stream sleeps between objects
        hot_hour(v, h, n=20, rate_hz=1.0)
    async with app_conn() as conn:
        for h in (H, H + HOUR):
            await archive_and_delete(conn, v, h)
    gets: list[str] = []
    async with client(live_api) as c:
        rid = await create(c, body(v, H, H + HOUR + SPAN, speed=10))

        async def counting(*a: Any) -> bytes:
            gets.append(a[2])
            return await real_get(*a)

        monkeypatch.setattr("gs.archiver.window.get", counting)
        async with c.stream("GET", f"/api/replays/{rid}/events", timeout=20) as r:
            async for k, _ in frames(r):
                if k == "tm":
                    break
        seen = len(gets)
        assert seen == 1 and len(registry(live_api)) <= 1
        await until(lambda: registry(live_api) == {}, 1)
        await asyncio.sleep(1.5)
        assert len(gets) == seen  # the generator was closed: the second hour was never read


async def test_idle_replay_expires(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    v = vehicle()
    hot_hour(v, H, n=30, rate_hz=1.0)
    monkeypatch.setattr(replays, "REPLAY_IDLE_S", 0.3)
    async with client(live_api) as c:
        rid = await create(c, body(v))
        assert len(registry(live_api)) == 1
        await until(lambda: registry(live_api) == {}, 2)
        assert (await c.get(f"/api/replays/{rid}/events")).status_code == 404


def _live_msg(kind: str, v: str | None) -> dict[str, Any]:
    t = H.isoformat()
    last: dict[str, Any] = {
        "key": "k",
        "kind": "RAISE",
        "t": t,
        "alert": True,
        "by_user": "system",
        "note": None,
        "check_kind": None,
        "level": None,
        "value": None,
    }
    alarm: dict[str, Any] = {
        "alarm_id": str(uuid4()),
        "vehicle": v,
        "param": "cabin_temp_c",
        "state": "UNACK",
        "severity": "RED",
        "level": "RED",
        "value": 1.0,
        "trigger_check": "limits",
        "limits_sha256": "a",
        "set_sha256": "b",
        "opened_at": t,
        "closed_at": None,
        "last": last,
        "ack_request": None,
    }
    cmd: dict[str, Any] = {
        "cmd_id": str(uuid4()),
        "vehicle": v,
        "name": "vent_cabin",
        "args": {},
        "hazardous": True,
        "reason": None,
        "issued_by": "fc1",
        "state": "QUEUED",
        "failure_reason": None,
        "queued_at": t,
        "sent_at": None,
        "received_at": None,
        "verified_at": None,
        "failed_at": None,
        "late_verified_at": None,
        "late_ack_at": None,
        "late_ack_accepted": None,
        "outcome": None,
    }
    out: dict[str, dict[str, Any]] = {
        "alarm": {"kind": "alarm", "alarm": alarm},
        "cmd": {"kind": "cmd", "command": cmd},
        "link": {
            "kind": "link",
            "event": {"key": "e", "vehicle": v, "kind": "LOS", "t_onboard": t, "details": {}},
        },
        "resync": {"kind": "resync", "vehicle": v},
    }
    return out[kind]


async def _through_hub(live: LiveApi, msg: dict[str, Any]) -> list[bytes]:
    hub = live.app.state.hub
    client_ = hub.subscribe()
    try:
        await hub._handle("1-0", json.dumps(msg))
        await hub._handle("2-0", json.dumps(_live_msg("link", None)))  # ground event: sentinel
        return [client_.queue.get_nowait() for _ in range(client_.queue.qsize())]
    finally:
        hub.unsubscribe(client_)


@pytest.mark.parametrize("kind", ["alarm", "cmd", "link", "resync"])
async def test_hub_drops_other_vehicles_on_every_arm(live_api: LiveApi, kind: str) -> None:
    outside, inside = vid(), live_api.vehicle  # `outside` stands in for NYX-TA9
    assert [f.split(b"\n")[0] for f in await _through_hub(live_api, _live_msg(kind, outside))] == [
        b"event: link"  # only the ground sentinel
    ]
    got = await _through_hub(live_api, _live_msg(kind, inside))
    # an own-vehicle message passes (a resync becomes a snapshot, or nothing without the database)
    assert len(got) >= (1 if kind == "resync" else 2)


async def test_503_when_archive_store_down(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = vehicle()
    hot_hour(v, H, n=20, rate_hz=1.0)
    async with app_conn() as conn:
        await archive_and_delete(conn, v, H)

    async def down(*_: Any) -> bytes:
        raise ConnectionRefusedError("minio down")

    monkeypatch.setattr("gs.archiver.window.get", down)
    async with client(live_api) as c:
        r = await c.post("/api/replays", json=body(v))
    assert r.status_code == 503 and r.json()["error"] == "UNAVAILABLE"
    assert registry(live_api) == {}


async def test_503_when_database_down(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_: Any, **__: Any) -> Any:
        raise PoolTimeout("no connection")

    monkeypatch.setattr(live_api.app.state.pool, "connection", down)
    async with client(live_api) as c:
        r = await c.post("/api/replays", json=body(vehicle()))
    assert r.status_code == 503 and r.json()["error"] == "UNAVAILABLE"
    assert registry(live_api) == {}


async def test_mid_stream_failure_ends_the_stream(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = vehicle()
    for h in (H, H + HOUR):
        hot_hour(v, h, n=20, rate_hz=1.0)
    async with app_conn() as conn:
        for h in (H, H + HOUR):
            await archive_and_delete(conn, v, h)
    async with client(live_api) as c:
        rid = await create(c, body(v, H, H + HOUR + SPAN))
        calls = 0

        async def second_fails(*a: Any) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ConnectionResetError("minio went away")
            return await real_get(*a)

        monkeypatch.setattr("gs.archiver.window.get", second_fails)
        async with c.stream("GET", f"/api/replays/{rid}/events", timeout=20) as r:
            got = [f async for f in frames(r)]
    assert got[0][0] == "replay_start" and "replay_end" not in [k for k, _ in got]
    assert [k for k, _ in got].count("tm") == 20  # the first hour was served
    await until(lambda: registry(live_api) == {}, 1)
