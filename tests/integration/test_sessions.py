import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from psycopg_pool import AsyncConnectionPool

from gs.api import dbguard
from gs.api.app import create_app
from tests.support.env import vid
from tests.support.http import CLIENT_HEADERS as H
from tests.support.http import FC2_PW, api_settings, authed, login
from tests.support.pg import audit_rows


@pytest.fixture
async def sess(users_file: Path) -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """(client logged in as fc1, vehicle a, vehicle b): two fresh vehicles, no sessions."""
    a, b = vid(), vid()
    app = create_app(dataclasses.replace(api_settings(users_file), vehicles=(a, b)))
    async with authed(app) as c:
        yield c, a, b


async def _start(c: httpx.AsyncClient, vehicle: str, name: str = "pass 1") -> httpx.Response:
    return await c.post("/api/sessions", json={"vehicle": vehicle, "name": name})


async def test_start_stop(sess: tuple[httpx.AsyncClient, str, str]) -> None:
    c, a, _ = sess
    r = await _start(c, a)
    assert r.status_code == 201
    s = r.json()
    assert (s["vehicle"], s["name"], s["conductor"], s["ended_at"]) == (a, "pass 1", "fc1", None)
    await login(c, "fc2", FC2_PW)  # fc2 stops it
    stop = await c.post(f"/api/sessions/{s['id']}/stop", headers=H)
    assert stop.status_code == 200
    done = stop.json()
    assert (done["ended_by"], done["conductor"]) == ("fc2", "fc1")
    assert done["started_at"] < done["ended_at"]
    start_row = next(
        x for x in audit_rows(action="SESSION_START") if x["details"].get("session_id") == s["id"]
    )
    stop_row = next(
        x for x in audit_rows(action="SESSION_STOP") if x["details"].get("session_id") == s["id"]
    )
    assert (start_row["actor"], stop_row["actor"]) == ("fc1", "fc2")


async def test_one_active_per_vehicle(sess: tuple[httpx.AsyncClient, str, str]) -> None:
    c, a, b = sess
    assert (await _start(c, a)).status_code == 201
    dup = await _start(c, a, "again")
    assert (dup.status_code, dup.json()["error"]) == (409, "SESSION_ACTIVE")
    assert (await _start(c, b)).status_code == 201


async def test_stop_twice_409(sess: tuple[httpx.AsyncClient, str, str]) -> None:
    c, a, _ = sess
    sid = (await _start(c, a)).json()["id"]
    assert (await c.post(f"/api/sessions/{sid}/stop")).status_code == 200
    again = await c.post(f"/api/sessions/{sid}/stop")
    assert (again.status_code, again.json()["error"]) == (409, "SESSION_STOPPED")
    assert (
        len([x for x in audit_rows(action="SESSION_STOP") if x["details"]["session_id"] == sid])
        == 1
    )


async def test_stop_unknown_404(sess: tuple[httpx.AsyncClient, str, str]) -> None:
    r = await sess[0].post("/api/sessions/999999999/stop")
    assert (r.status_code, r.json()["error"]) == (404, "NOT_FOUND")


async def test_unknown_vehicle_422(sess: tuple[httpx.AsyncClient, str, str]) -> None:
    r = await _start(sess[0], "NYX-NOPE")
    assert (r.status_code, r.json()["error"]) == (422, "INVALID_ARGS")


async def test_list_newest_first(sess: tuple[httpx.AsyncClient, str, str]) -> None:
    c, a, b = sess
    first = (await _start(c, a)).json()["id"]
    second = (await _start(c, b)).json()["id"]
    listed = (await c.get("/api/sessions")).json()
    ids = [s["id"] for s in listed]
    assert ids.index(second) < ids.index(first)
    started = [s["started_at"] for s in listed]
    assert started == sorted(started, reverse=True)


@pytest.mark.parametrize("route", ["start", "stop", "list"])
async def test_database_down_is_503(
    sess: tuple[httpx.AsyncClient, str, str], monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    c, a, _ = sess
    sid = (await _start(c, a)).json()["id"]
    monkeypatch.setattr(dbguard, "DB_TIMEOUT_S", 0.3)
    app = c._transport.app  # type: ignore[attr-defined]
    bad = AsyncConnectionPool("postgresql://x@127.0.0.1:1/x", min_size=1, open=False)
    await bad.open(wait=False)
    app.state.pool = bad
    try:
        r = await {
            "start": lambda: c.post("/api/sessions", json={"vehicle": a, "name": "n"}),
            "stop": lambda: c.post(f"/api/sessions/{sid}/stop"),
            "list": lambda: c.get("/api/sessions"),
        }[route]()
    finally:
        await bad.close()
    assert (r.status_code, r.json()["error"]) == (503, "UNAVAILABLE")
    assert r.json()["message"] == "database unreachable"
