"""GET /api/alarms and POST /api/alarms/{alarm_id}/ack (D-ALM-14, Task 10)."""

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from gs.api import alarms
from gs.api.app import create_app
from gs.ingest.acks import ACK_PROPAGATION_S
from tests.support.env import dotenv, now, until, vid
from tests.support.http import CLIENT_HEADERS, FC2_PW, api_settings, authed, get_json
from tests.support.ingest import worker
from tests.support.pg import app_dsn, audit_rows, open_alarm, rows
from tests.support.redisx import drained, produce_values

TEMP = "cabin_temp_c"


async def alarm_of(v: str, value: float, nominal_after: int = 0) -> str:
    """An alarm on cabin_temp_c at `value` (31 RED, 28 YELLOW) raised by a worker that has
    stopped; `nominal_after` nominal frames follow (3 or more return it)."""
    base = now() - timedelta(minutes=1)
    produce_values(v, {TEMP: [22.0] * 5 + [value] * 5 + [22.0] * nominal_after}, base=base)
    async with worker([v]):
        await until(
            lambda: drained(v) and bool(rows("select 1 from alarms where vehicle=%s", v)), 5
        )
    (a,) = rows("select alarm_id from alarms where vehicle=%s", v)
    await asyncio.sleep(ACK_PROPAGATION_S)  # on screen long enough to be acknowledged
    return str(a["alarm_id"])


async def ack(
    c: httpx.AsyncClient, alarm_id: str, note: str = "seen, venting", rid: str = ""
) -> httpx.Response:
    body = {"request_id": rid or str(uuid.uuid4()), "note": note}
    return await c.post(f"/api/alarms/{alarm_id}/ack", json=body)


def ack_audits(rid: str) -> list[dict[str, Any]]:
    return [r for r in audit_rows("ALARM_ACK") if r["details"].get("request_id") == rid]


@pytest.fixture
def app(users_file: Path) -> Any:
    return create_app(api_settings(users_file))


async def test_ack_202_pending_then_applied(app: Any) -> None:
    v = vid()
    produce_values(v, {TEMP: [22.0] * 5 + [31.0] * 5})
    async with worker([v]), authed(app) as c:
        await until(lambda: open_alarm(v, TEMP) is not None and drained(v), 5)
        a = open_alarm(v, TEMP)
        assert a is not None
        await asyncio.sleep(ACK_PROPAGATION_S)
        rid = str(uuid.uuid4())
        r = await ack(c, str(a["alarm_id"]), rid=rid)
        assert (r.status_code, r.json()) == (202, {"request_id": rid, "status": "pending"})
        (audit,) = ack_audits(rid)
        assert (audit["actor"], audit["reason"]) == ("fc1", "seen, venting")
        assert audit["details"] == {"vehicle": v, "alarm_id": str(a["alarm_id"]), "request_id": rid}

        async def applied() -> bool:
            (view,) = await get_json(c, f"/api/alarms?vehicle={v}")
            return bool(view["state"] == "ACKED" and view["ack_request"]["status"] == "applied")

        deadline = now() + timedelta(seconds=1.5)
        while not await applied():
            assert now() < deadline, "ACK not applied within 1.5 s"
            await asyncio.sleep(0.05)


async def test_ack_idempotent_on_request_id(app: Any) -> None:
    v = vid()
    aid = await alarm_of(v, 31.0)
    rid = str(uuid.uuid4())
    async with authed(app) as c:
        first, second = await ack(c, aid, rid=rid), await ack(c, aid, rid=rid)
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    assert len(rows("select 1 from alarm_ack_request where request_id=%s", rid)) == 1
    assert len(ack_audits(rid)) == 1


async def test_ack_errors(app: Any) -> None:
    v = vid()
    aid = await alarm_of(v, 31.0)
    async with authed(app) as c:
        blank = await ack(c, aid, note="   ")
        unknown = await ack(c, str(uuid.uuid4()))
        acked = await ack(c, aid)
        assert acked.status_code == 202
        produce_values(v, {TEMP: [22.0] * 5}, start_seq=10, base=now() - timedelta(minutes=1))
        async with worker([v]):  # applies the ACK, then 3 nominal frames clear the alarm
            await until(lambda: open_alarm(v, TEMP) is None, 5)
        cleared = await ack(c, aid)
    got = [(r.status_code, r.json()["error"]) for r in (blank, unknown, cleared)]
    assert got == [(422, "NOTE_REQUIRED"), (404, "ALARM_NOT_FOUND"), (409, "ALARM_CLEARED")]
    for r in (blank, unknown, cleared):
        assert set(r.json()) == {"error", "message"} and r.json()["message"]
    assert rows("select count(*) as n from alarm_ack_request where alarm_id=%s", aid)[0]["n"] == 1


async def test_list_filters(app: Any) -> None:
    red, yellow, gone = vid(), vid(), vid()
    red_id, yellow_id = await alarm_of(red, 31.0), await alarm_of(yellow, 28.0)
    gone_id = await alarm_of(gone, 28.0, nominal_after=5)  # RTN_UNACK
    async with authed(app) as c:
        assert (await ack(c, gone_id)).status_code == 202
        async with worker([gone]):  # ACK of a returned alarm clears it
            await until(lambda: open_alarm(gone, TEMP) is None, 5)

        async def ids(query: str) -> set[str]:
            return {a["alarm_id"] for a in await get_json(c, f"/api/alarms?{query}")}

        mine = {red_id, yellow_id, gone_id}
        assert await ids(f"vehicle={red}") == {red_id}
        assert await ids("severity=YELLOW") & mine == {yellow_id}
        assert await ids("severity=RED") & mine == {red_id}
        assert await ids("state=UNACK") & mine == {red_id, yellow_id}
        assert await ids("") & mine == {red_id, yellow_id}  # open alarms only by default
        assert await ids("state=CLEARED") & mine == {gone_id}  # Track 0 note: open_only False
        (view,) = await get_json(c, f"/api/alarms?vehicle={gone}&state=CLEARED")
        assert (view["level"], view["severity"]) == ("YELLOW_HIGH", "YELLOW")  # P-pf20
        bad = await c.get("/api/alarms?severity=GREEN")
        assert (bad.status_code, bad.json()["error"]) == (422, "VALIDATION_ERROR")


async def test_ack_requires_session_and_header(app: Any) -> None:
    body = {"request_id": str(uuid.uuid4()), "note": "seen"}
    path = f"/api/alarms/{uuid.uuid4()}/ack"
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as anon:
            r = await anon.post(path, json=body, headers=CLIENT_HEADERS)
            assert (r.status_code, r.json()["error"]) == (401, "UNAUTHENTICATED")
            assert (await anon.get("/api/alarms")).status_code == 401
    async with authed(app) as c:
        r = await c.post(path, json=body, headers={"X-GS-Client": ""})
        assert (r.status_code, r.json()["error"]) == (403, "CLIENT_HEADER_REQUIRED")


async def test_database_down_is_503(users_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alarms, "DB_TIMEOUT_S", 0.5)  # a closed port: the pool never connects
    down = create_app(api_settings(users_file, database_url=app_dsn("gs").replace(
        f":{dotenv()['DB_HOST_PORT']}/", ":1/")))  # fmt: skip
    async with authed(down) as c:
        for r in (await c.get("/api/alarms"), await ack(c, str(uuid.uuid4()))):
            assert (r.status_code, r.json()["error"]) == (503, "UNAVAILABLE")


async def test_request_id_reused_for_another_ack_is_422(app: Any) -> None:  # review of T10
    a, b = vid(), vid()
    aid, bid = await alarm_of(a, 31.0), await alarm_of(b, 31.0)
    rid = str(uuid.uuid4())
    async with authed(app) as c:
        assert (await ack(c, aid, rid=rid)).status_code == 202
        other_alarm = await ack(c, bid, rid=rid)
        other_note = await ack(c, aid, note="a different note", rid=rid)
        async with authed(app, "fc2", FC2_PW) as c2:
            other_user = await ack(c2, aid, rid=rid)
    for r in (other_alarm, other_note, other_user):
        assert (r.status_code, r.json()["error"]) == (422, "REQUEST_ID_REUSED")
    assert rows("select alarm_id from alarm_ack_request where request_id=%s", rid) == [
        {"alarm_id": uuid.UUID(aid)}
    ]
    assert len(ack_audits(rid)) == 1  # no row and no audit for the refused ones


async def test_repeat_after_cleared_returns_stored_status(app: Any) -> None:
    v = vid()
    aid = await alarm_of(v, 31.0)
    rid = str(uuid.uuid4())
    async with authed(app) as c:
        assert (await ack(c, aid, rid=rid)).status_code == 202
        produce_values(v, {TEMP: [22.0] * 5}, start_seq=10, base=now() - timedelta(minutes=1))
        async with worker([v]):  # applies the ACK, then the nominal frames clear the alarm
            await until(lambda: open_alarm(v, TEMP) is None, 5)
        again = await ack(c, aid, rid=rid)
    assert (again.status_code, again.json()) == (202, {"request_id": rid, "status": "applied"})
    assert len(ack_audits(rid)) == 1
