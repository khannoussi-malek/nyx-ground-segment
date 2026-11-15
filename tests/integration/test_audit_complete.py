"""Every operator action leaves exactly its audit rows (D-CMD-08), one fresh user and vehicle."""

import dataclasses
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from gs.api.app import create_app
from gs.api.auth import hash_password
from gs.ingest import acks
from tests.support.env import until, vid
from tests.support.http import CLIENT_HEADERS, api_settings, login
from tests.support.ingest import owned
from tests.support.pg import audit_rows, rows
from tests.support.redisx import produce_values

PW = "audit-test-password"
TEMP = "cabin_temp_c"
REASON = "venting for the audit test"


@pytest.fixture(autouse=True)
def no_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not about P-pf42b: no 2 s propagation margin."""
    monkeypatch.setattr(acks, "ACK_PROPAGATION_S", 0.0)


def settled(alarm_id: str) -> bool:
    q = "select status from alarm_ack_request where alarm_id=%s"
    return "pending" not in {r["status"] for r in rows(q, alarm_id)}


async def ack(c: httpx.AsyncClient, alarm_id: str, seen_key: str) -> None:
    body = {"request_id": str(uuid.uuid4()), "note": "seen it", "seen_key": seen_key}
    r = await c.post(f"/api/alarms/{alarm_id}/ack", json=body)
    assert r.status_code == 202, r.text


async def command(c: httpx.AsyncClient, body: dict[str, Any], status: int) -> dict[str, Any]:
    r = await c.post("/api/commands", json=body)
    assert r.status_code == status, r.text
    out: dict[str, Any] = r.json()
    return out


async def test_audit_complete(tmp_path: Path) -> None:
    user, v, t0 = f"t-{vid()}".lower(), vid(), datetime.now(UTC)
    users = tmp_path / "users.yaml"
    users.write_text(yaml.safe_dump({"users": {user: hash_password(PW)}}))
    app = create_app(dataclasses.replace(api_settings(users), vehicles=(v,)))
    iso = {"vehicle": v, "name": "isolate_valve", "args": {"valve": "A"}}
    vent = {"vehicle": v, "name": "vent_cabin", "args": {"duration_s": 3}}
    produce_values(v, {TEMP: [22.0] * 5 + [31.0] * 5})  # a RED alarm to acknowledge
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://api", headers=CLIENT_HEADERS
        ) as c:
            assert (await login(c, user, "wrong")).status_code == 401
            assert (await login(c, user, PW)).status_code == 204
            async with owned(v):  # applies the ACKs; no command is sent while a tracker runs
                await until(lambda: bool(rows("select 1 from alarms where vehicle=%s", v)), 5)
                [alarm] = (await c.get("/api/alarms", params={"vehicle": v})).json()
                await ack(c, alarm["alarm_id"], "not-what-the-console-showed")  # superseded
                await until(lambda: settled(alarm["alarm_id"]), 5)
                await ack(c, alarm["alarm_id"], alarm["last"]["key"])  # applied
                await until(lambda: settled(alarm["alarm_id"]), 5)
            await command(c, {**iso, "reason": "closing A for the test"}, 202)
            await command(c, {**iso, "args": {"valve": "B"}}, 202)
            await command(c, iso, 409)  # the same command within 2 s: DUPLICATE_COMMAND
            token = (await command(c, vent, 409))["confirm_token"]
            await command(c, {**vent, "confirm_token": token, "reason": REASON}, 202)
            s = (await c.post("/api/sessions", json={"vehicle": v, "name": "audit"})).json()
            assert (await c.post(f"/api/sessions/{s['id']}/stop")).status_code == 200
            assert (await c.post("/api/logout")).status_code == 204
    mine = audit_rows(actor=user, since=t0)
    system = [
        r for r in audit_rows(actor="system", since=t0) if (r["details"] or {}).get("vehicle") == v
    ]
    got = Counter((r["action"], r["actor"]) for r in [*mine, *system])
    assert got == Counter(
        {
            ("LOGIN_FAILED", user): 1,
            ("LOGIN", user): 1,
            ("ALARM_ACK", user): 2,
            ("ALARM_ACK_APPLIED", "system"): 2,
            ("CMD_REQUESTED", user): 3,
            ("CMD_STATE", user): 3,  # SENT, before each XADD
            ("CMD_REJECTED", user): 1,
            ("CMD_CONFIRM_REQUIRED", user): 1,
            ("SESSION_START", user): 1,
            ("SESSION_STOP", user): 1,
            ("LOGOUT", user): 1,
        }
    )
    assert all(r["at"].utcoffset() is not None for r in [*mine, *system])  # every row timed
    assert sorted(r["details"]["result"] for r in system) == ["applied", "superseded"]
    requested = [
        (r["details"]["name"], r["details"]["args"], r["reason"])
        for r in mine
        if r["action"] == "CMD_REQUESTED"
    ]
    assert requested == [
        ("isolate_valve", {"valve": "A"}, "closing A for the test"),
        ("isolate_valve", {"valve": "B"}, None),
        ("vent_cabin", {"duration_s": 3}, REASON),
    ]
    [dup] = [r for r in mine if r["action"] == "CMD_REJECTED"]
    assert dup["reason"] == "DUPLICATE_COMMAND"
