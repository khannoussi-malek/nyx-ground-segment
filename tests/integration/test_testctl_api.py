"""Test controls (D-SIM-10): the API forwards fault requests to the simulator, only when
GS_TEST_CONTROLS is on. API in-process, a fake simulator on a local port, real Redis and
Postgres."""

import dataclasses
import json
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from gs.api.app import create_app
from tests.support.http import CLIENT_HEADERS, api_settings, authed
from tests.support.pg import audit_rows

FAULT = "/api/test-controls/fault"


class FakeSim:
    """Records every request; answers `status` (and a JSON body) to all of them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.status = 204
        me = self

        class Handler(BaseHTTPRequestHandler):
            def _do(self) -> None:
                n = int(self.headers.get("Content-Length", 0))
                me.calls.append((self.command, self.path, json.loads(self.rfile.read(n))))
                body = b'{"error":"INVALID_FAULT","message":"nope"}' if me.status == 422 else b""
                self.send_response(me.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_DELETE = _do

            def log_message(self, *a: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def sim() -> Iterator[FakeSim]:
    s = FakeSim()
    yield s
    s.server.shutdown()
    s.server.server_close()


def closed_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


async def client_for(
    users_file: Path, test_controls: bool, sim_url: str
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        dataclasses.replace(api_settings(users_file), test_controls=test_controls, sim_url=sim_url)
    )
    async with authed(app) as c:
        yield c


@pytest.fixture
async def on(users_file: Path, sim: FakeSim) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(users_file, True, sim.url):
        yield c


@pytest.fixture
async def off(users_file: Path, sim: FakeSim) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(users_file, False, sim.url):
        yield c


async def test_flag_off_is_404_on_all_three(off: httpx.AsyncClient, sim: FakeSim) -> None:
    body = {"vehicle": "NYX-TA1", "fault": "leak"}
    rs = [
        await off.get("/api/test-controls"),
        await off.post(FAULT, json=body),
        await off.request("DELETE", FAULT, json=body),
    ]
    for r in rs:
        assert r.status_code == 404
        assert r.json() == {"error": "NOT_FOUND", "message": "test controls are off"}
    assert sim.calls == []


async def test_get_lists_live_vehicles_and_faults(on: httpx.AsyncClient) -> None:
    r = await on.get("/api/test-controls")
    assert r.status_code == 200
    body = r.json()
    assert body["vehicles"] == ["NYX-TA1"]
    assert {"leak", "overheat", "dropout", "step"} <= set(body["faults"])


async def test_post_forwards_and_audits(on: httpx.AsyncClient, sim: FakeSim) -> None:
    n = len(audit_rows("TEST_FAULT"))
    body = {"vehicle": "NYX-TA1", "fault": "dropout", "params": {"duration_s": 5}}
    assert (await on.post(FAULT, json=body)).status_code == 204
    assert sim.calls == [("POST", "/sim/fault", body)]
    row = audit_rows("TEST_FAULT")[n:][-1]
    assert row["actor"] == "fc1"
    assert row["details"] == {"vehicle": "NYX-TA1", "fault": "dropout", "params": {"duration_s": 5}}


async def test_delete_forwards_and_audits(on: httpx.AsyncClient, sim: FakeSim) -> None:
    n = len(audit_rows("TEST_FAULT_CLEAR"))
    body = {"vehicle": "NYX-TA1", "fault": "leak"}
    assert (await on.request("DELETE", FAULT, json=body)).status_code == 204
    assert sim.calls == [("DELETE", "/sim/fault", body)]
    row = audit_rows("TEST_FAULT_CLEAR")[n:][-1]
    assert (row["actor"], row["details"]) == ("fc1", body)


@pytest.mark.parametrize(
    "body",
    [
        {"vehicle": "NYX-TA1", "fault": "explode"},
        {"vehicle": "NYX-TA1", "fault": "dropout"},  # duration_s is required
        {"vehicle": "NYX-TA1", "fault": "leak", "params": {"bogus": 1}},
        {"vehicle": "NYX-TA9", "fault": "leak"},  # no live display
        {"vehicle": "NYX-TA2", "fault": "leak"},  # valid name, not in this API's live list
    ],
)
async def test_bad_post_is_422_and_not_forwarded(
    on: httpx.AsyncClient, sim: FakeSim, body: dict[str, object]
) -> None:
    n = len(audit_rows("TEST_FAULT"))
    r = await on.post(FAULT, json=body)
    assert (r.status_code, r.json()["error"]) == (422, "VALIDATION_ERROR")
    assert sim.calls == [] and len(audit_rows("TEST_FAULT")) == n


async def test_bad_delete_is_422_and_not_forwarded(on: httpx.AsyncClient, sim: FakeSim) -> None:
    r = await on.request("DELETE", FAULT, json={"vehicle": "NYX-TA9", "fault": "leak"})
    assert (r.status_code, r.json()["error"]) == (422, "VALIDATION_ERROR")
    assert sim.calls == []


async def test_simulator_422_is_passed_through(on: httpx.AsyncClient, sim: FakeSim) -> None:
    sim.status = 422
    r = await on.post(FAULT, json={"vehicle": "NYX-TA1", "fault": "leak"})
    assert (r.status_code, r.json()["error"]) == (422, "VALIDATION_ERROR")


@pytest.mark.parametrize("status", [500, 404])
async def test_simulator_error_is_503(on: httpx.AsyncClient, sim: FakeSim, status: int) -> None:
    sim.status = status
    r = await on.post(FAULT, json={"vehicle": "NYX-TA1", "fault": "leak"})
    assert r.status_code == 503
    assert r.json() == {"error": "UNAVAILABLE", "message": "simulator unreachable"}


async def test_simulator_down_is_503(users_file: Path) -> None:
    async for c in client_for(users_file, True, closed_url()):
        r = await c.post(FAULT, json={"vehicle": "NYX-TA1", "fault": "leak"})
        assert r.status_code == 503
        assert r.json() == {"error": "UNAVAILABLE", "message": "simulator unreachable"}


async def test_session_and_client_header_are_required(users_file: Path, sim: FakeSim) -> None:
    app = create_app(
        dataclasses.replace(api_settings(users_file), test_controls=True, sim_url=sim.url)
    )
    body = {"vehicle": "NYX-TA1", "fault": "leak"}
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as anon:
            assert (await anon.get("/api/test-controls")).status_code == 401
            r = await anon.post(FAULT, json=body, headers=CLIENT_HEADERS)
            assert r.status_code == 401
            assert (await anon.post(FAULT, json=body)).status_code == 403  # no X-GS-Client
    async for c in client_for(users_file, True, sim.url):
        r = await c.post(FAULT, json=body, headers={"X-GS-Client": "curl"})
        assert (r.status_code, r.json()["error"]) == (403, "CLIENT_HEADER_REQUIRED")
    assert sim.calls == []
