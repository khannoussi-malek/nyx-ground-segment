"""POST/GET /api/commands: confirm token, 2 s interlock, send and SEND_ERROR (D-CMD-06/07/11,
P-pf12/16). API in-process, real Redis and Postgres, a fresh vid() vehicle per test."""

import asyncio
import dataclasses
import errno
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import redis.asyncio
import redis.asyncio.client
import redis.exceptions
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from redis._parsers.base import BaseParser

import gs.api.commands as commands_mod
from gs.api.app import create_app
from gs.api.commands import PreSendError, args_hash, lock_key, mark_sent, not_sent, release_lock
from gs.ingest.events import late_key
from tests.support.env import until, vid
from tests.support.http import CLIENT_HEADERS, FC1_PW, FC2_PW, api_settings, login
from tests.support.ingest import worker
from tests.support.pg import app_dsn, audit_rows, commands_for, events_for
from tests.support.redisx import HOST, aclient, client, load_schema, stream_entries
from tests.support.sim import post_fault, running_sim

ARGS = {"duration_s": 5}
HAZARD = "Irreversible loss of cabin atmosphere"
REASON = "venting for the leak test"


@dataclasses.dataclass
class Api:
    app: FastAPI
    v: str
    fc1: httpx.AsyncClient
    fc2: httpx.AsyncClient
    t0: datetime

    def vent(self, **args: object) -> dict[str, Any]:
        return {"vehicle": self.v, "name": "vent_cabin", "args": args or ARGS}

    def iso(self, valve: str = "A") -> dict[str, Any]:
        return {"vehicle": self.v, "name": "isolate_valve", "args": {"valve": valve}}

    def tc(self) -> list[dict[str, Any]]:
        return [json.loads(f["data"]) for _, f in stream_entries(f"tc:{self.v}")]

    def audits(self, actor: str | None = None) -> list[dict[str, Any]]:
        mine = audit_rows(actor=actor, since=self.t0)
        return [a for a in mine if (a["details"] or {}).get("vehicle") == self.v]


@pytest.fixture
async def api(users_file: Path) -> AsyncIterator[Api]:
    v = vid()
    app = create_app(dataclasses.replace(api_settings(users_file), vehicles=(v,)))
    async with app.router.lifespan_context(app):
        t = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=t, base_url="http://api", headers=CLIENT_HEADERS) as fc1,
            httpx.AsyncClient(transport=t, base_url="http://api", headers=CLIENT_HEADERS) as fc2,
        ):
            assert (await login(fc1, "fc1", FC1_PW)).status_code == 204
            assert (await login(fc2, "fc2", FC2_PW)).status_code == 204
            yield Api(app, v, fc1, fc2, datetime.now(UTC))


async def confirm_token(c: httpx.AsyncClient, body: dict[str, Any]) -> str:
    r = await c.post("/api/commands", json=body)
    assert r.status_code == 409 and r.json()["error"] == "HAZARDOUS_CONFIRMATION_REQUIRED", r.text
    return str(r.json()["confirm_token"])


async def send_hazardous(c: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    token = await confirm_token(c, body)
    return await c.post("/api/commands", json={**body, "confirm_token": token, "reason": REASON})


# ---- confirm token (D-CMD-06)
async def test_hazardous_requires_confirm_and_reason(api: Api) -> None:
    r = await api.fc1.post("/api/commands", json=api.vent())
    body = r.json()
    assert r.status_code == 409 and body["error"] == "HAZARDOUS_CONFIRMATION_REQUIRED"
    assert (body["hazard"], body["expires_in_s"]) == (HAZARD, 60) and body["confirm_token"]
    assert api.tc() == [] and commands_for(api.v) == []
    token = body["confirm_token"]
    short = {**api.vent(), "confirm_token": token, "reason": "  short   "}
    r = await api.fc1.post("/api/commands", json=short)
    assert r.status_code == 422 and r.json()["error"] == "REASON_REQUIRED"
    r = await api.fc1.post("/api/commands", json={**short, "reason": "0123456789"})  # same token
    assert r.status_code == 202
    [tc] = api.tc()
    assert tc["reason"] == "0123456789" and tc["cmd_id"] == r.json()["cmd_id"]
    acts = [a["action"] for a in api.audits("fc1")]
    assert acts == ["CMD_CONFIRM_REQUIRED", "CMD_REQUESTED", "CMD_STATE"]


async def test_hazardous_one_shot_with_computed_hash_rejected(api: Api) -> None:
    forged = {**api.vent(), "confirm_token": args_hash(api.v, "vent_cabin", ARGS), "reason": REASON}
    r = await api.fc1.post("/api/commands", json=forged)
    assert r.status_code == 409 and r.json()["error"] == "CONFIRMATION_INVALID"
    assert [a["action"] for a in api.audits("fc1")] == ["CMD_REJECTED"]
    assert api.tc() == [] and commands_for(api.v) == []


async def test_confirm_token_single_use_and_user_bound(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await confirm_token(api.fc1, api.vent())
    ok = {**api.vent(), "confirm_token": token, "reason": REASON}
    assert (await api.fc1.post("/api/commands", json=ok)).status_code == 202
    reuse = await api.fc1.post("/api/commands", json=ok)
    assert reuse.status_code == 409 and reuse.json()["error"] == "CONFIRMATION_INVALID"
    other = await confirm_token(api.fc1, api.vent())
    stolen = await api.fc2.post("/api/commands", json={**ok, "confirm_token": other})
    assert stolen.status_code == 409 and stolen.json()["error"] == "CONFIRMATION_INVALID"
    monkeypatch.setattr(commands_mod, "CONFIRM_TTL_MS", 200)
    short_lived = await confirm_token(api.fc1, api.vent())
    await asyncio.sleep(0.4)
    late = await api.fc1.post("/api/commands", json={**ok, "confirm_token": short_lived})
    assert late.status_code == 409 and late.json()["error"] == "CONFIRMATION_INVALID"
    rejected = [a for a in api.audits() if a["action"] == "CMD_REJECTED"]
    assert [(a["actor"], a["reason"]) for a in rejected] == [
        ("fc1", "CONFIRMATION_INVALID"),
        ("fc2", "CONFIRMATION_INVALID"),
        ("fc1", "CONFIRMATION_INVALID"),
    ]
    assert len(api.tc()) == 1


async def test_confirm_args_mismatch(api: Api) -> None:
    token = await confirm_token(api.fc1, api.vent(duration_s=5))
    r = await api.fc1.post(
        "/api/commands", json={**api.vent(duration_s=6), "confirm_token": token, "reason": REASON}
    )
    assert r.status_code == 409 and r.json()["error"] == "CONFIRMATION_INVALID"
    assert api.tc() == []


# ---- interlock (D-CMD-07)
async def test_duplicate_within_2s(api: Api) -> None:
    first = await api.fc1.post("/api/commands", json=api.iso("A"))
    assert first.status_code == 202
    dup = await api.fc2.post("/api/commands", json=api.iso("A"))
    body = dup.json()
    assert dup.status_code == 409 and body["error"] == "DUPLICATE_COMMAND"
    assert body["by"] == "fc1" and 0 <= body["seconds_ago"] < 2
    assert body["cmd_id"] == first.json()["cmd_id"]
    [rej] = [a for a in api.audits("fc2") if a["action"] == "CMD_REJECTED"]
    assert rej["reason"] == "DUPLICATE_COMMAND"
    assert len(api.tc()) == 1


async def test_hazardous_lock_ignores_args(api: Api) -> None:
    assert (await send_hazardous(api.fc1, api.vent(duration_s=5))).status_code == 202
    dup = await send_hazardous(api.fc2, api.vent(duration_s=6))
    assert dup.status_code == 409 and dup.json()["error"] == "DUPLICATE_COMMAND"
    assert dup.json()["by"] == "fc1"


async def test_ordinary_lock_uses_args(api: Api) -> None:
    assert (await api.fc1.post("/api/commands", json=api.iso("A"))).status_code == 202
    assert (await api.fc1.post("/api/commands", json=api.iso("B"))).status_code == 202
    assert len(api.tc()) == 2


async def test_lock_expires(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_mod, "LOCK_TTL_MS", 200)
    assert (await api.fc1.post("/api/commands", json=api.iso())).status_code == 202
    await asyncio.sleep(0.4)
    assert (await api.fc2.post("/api/commands", json=api.iso())).status_code == 202


async def test_release_lock_deletes_only_its_own() -> None:  # compare-and-delete (D-CMD-11)
    key, r = f"cmdlock:{vid()}:isolate_valve:h", aclient("api")
    try:
        await r.set(key, "theirs|fc2", px=2000)
        await release_lock(r, key, "mine|fc1")
        assert await r.get(key) == "theirs|fc2"
        await release_lock(r, key, "theirs|fc2")
        assert await r.get(key) is None
        await release_lock(r, key, "theirs|fc2")  # already gone: no error
    finally:
        await r.aclose()


def test_lock_key_shape() -> None:
    assert lock_key("NYX-TA1", "vent_cabin", True, "h") == "cmdlock:NYX-TA1:vent_cabin"
    assert lock_key("NYX-TA1", "isolate_valve", False, "h") == "cmdlock:NYX-TA1:isolate_valve:h"


def test_args_hash_is_canonical() -> None:
    a = args_hash("NYX-TA1", "x", {"b": 1, "a": "z"})
    assert a == args_hash("NYX-TA1", "x", {"a": "z", "b": 1}) and len(a) == 64
    assert a != args_hash("NYX-TA2", "x", {"b": 1, "a": "z"})
    assert a != args_hash("NYX-TA1", "y", {"b": 1, "a": "z"})  # the name is part of it


# ---- send and SEND_ERROR (D-CMD-11, P-pf12/16)
def lock_value(api: Api, name: str = "isolate_valve") -> str | None:
    keys = client("admin").keys(f"cmdlock:{api.v}:{name}*")
    return str(client("admin").get(keys[0])) if keys else None


async def refused_send(r: Any, *a: Any) -> bool:
    """The real send against a closed port: redis-py's ConnectionError from ECONNREFUSED."""
    closed = redis.asyncio.Redis(host=HOST, port=1, socket_timeout=1)
    try:
        return await REAL_SEND(closed, *a)
    finally:
        await closed.aclose()


REAL_SEND = commands_mod.send_locked


async def test_send_error_releases_lock(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_mod, "send_locked", refused_send)
    r = await api.fc1.post("/api/commands", json=api.iso())
    assert r.status_code == 503 and r.json()["error"] == "BROKER_DOWN"
    assert "safe to retry" in r.json()["message"]
    [row] = commands_for(api.v)
    assert (row["state"], row["failure_reason"]) == ("FAILED", "SEND_ERROR")
    [view] = (await api.fc1.get("/api/commands", params={"vehicle": api.v})).json()
    assert view["outcome"] == "NOT_SENT"
    assert lock_value(api) is None  # released
    states = [a["details"]["state"] for a in api.audits("fc1") if a["action"] == "CMD_STATE"]
    assert states == ["SENT", "FAILED"]
    monkeypatch.setattr(commands_mod, "send_locked", REAL_SEND)
    assert (await api.fc1.post("/api/commands", json=api.iso())).status_code == 202


async def test_acl_refusal_is_send_error(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    sim = aclient("sim")  # NOPERM: the sim user may not touch cmdlock:* or write tc:

    async def as_sim(r: Any, *a: Any) -> bool:
        return await REAL_SEND(sim, *a)

    monkeypatch.setattr(commands_mod, "send_locked", as_sim)
    try:
        r = await api.fc1.post("/api/commands", json=api.iso())
    finally:
        await sim.aclose()
    assert r.status_code == 503 and "safe to retry" in r.json()["message"]
    assert commands_for(api.v)[0]["failure_reason"] == "SEND_ERROR"


REPLIES = [  # error replies to MULTI XADD EXEC: the server refused it atomically (P-pf38)
    "OOM command not allowed when used memory > 'maxmemory'.",
    "LOADING Redis is loading the dataset in memory",
    "READONLY You can't write against a read only replica.",
    "MISCONF Redis is configured to save RDB snapshots, but it's currently unable to persist",
    "WRONGTYPE Operation against a key holding the wrong kind of value",
    "ERR any error reply",
    "EXECABORT Transaction discarded because of previous errors.",
    "NOPERM User api has no permissions to run the 'xadd' command",
]


@pytest.mark.parametrize("reply", REPLIES)
async def test_error_reply_is_send_error(
    api: Api, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    async def refused(*a: Any) -> bool:
        raise BaseParser.parse_error(reply)  # type: ignore[no-untyped-call]  # redis-py raises this

    monkeypatch.setattr(commands_mod, "send_locked", refused)
    r = await api.fc1.post("/api/commands", json=api.iso())
    assert r.status_code == 503 and "safe to retry" in r.json()["message"]
    [row] = commands_for(api.v)
    assert (row["state"], row["failure_reason"]) == ("FAILED", "SEND_ERROR")
    assert lock_value(api) is None


def lose_exec_reply(monkeypatch: pytest.MonkeyPatch, err: Exception) -> None:
    """The real pipeline: Redis applies MULTI XADD EXEC, then reading the EXEC reply fails
    (G30). redis-py, still WATCHing, turns that into a WatchError with `err` as its context."""
    real = redis.asyncio.client.Pipeline.parse_response
    calls = [0]

    async def parse(self: Any, conn: Any, command_name: Any, **options: Any) -> Any:
        got = await real(self, conn, command_name, **options)
        if command_name == "_":  # MULTI, XADD (QUEUED), EXEC: the third is EXEC's reply
            calls[0] += 1
            if calls[0] == 3:
                raise err
        return got

    monkeypatch.setattr(redis.asyncio.client.Pipeline, "parse_response", parse)


@pytest.mark.parametrize(
    "err",
    [
        redis.exceptions.TimeoutError("Timeout reading from socket"),
        redis.exceptions.ConnectionError("Connection closed by server."),
    ],
)
async def test_timed_out_xadd_leaves_row_sent(
    api: Api, monkeypatch: pytest.MonkeyPatch, err: Exception
) -> None:  # P-pf12: it may have landed (G30); the tracker judges by evidence
    lose_exec_reply(monkeypatch, err)
    r = await api.fc1.post("/api/commands", json=api.iso())
    assert r.status_code == 503 and r.json()["error"] == "BROKER_DOWN"
    assert "may have been sent" in r.json()["message"]
    [row] = commands_for(api.v)
    assert (row["state"], row["failure_reason"]) == ("SENT", None)
    assert [tc["cmd_id"] for tc in api.tc()] == [str(row["cmd_id"])]  # it did land
    assert lock_value(api) is not None  # kept: a retry within 2 s is a duplicate
    monkeypatch.undo()
    dup = await api.fc2.post("/api/commands", json=api.iso())
    assert dup.status_code == 409 and dup.json()["error"] == "DUPLICATE_COMMAND"


async def test_lock_changed_before_exec_is_not_sent(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:  # EXEC returns nil: the watched lock changed, nothing was applied
    real = redis.asyncio.client.Pipeline.parse_response

    async def parse(self: Any, conn: Any, command_name: Any, **options: Any) -> Any:
        got = await real(self, conn, command_name, **options)
        if command_name == "GET" and str(got).endswith("|fc1"):  # send_locked's own check
            keys = client("admin").keys(f"cmdlock:{api.v}:*")
            client("admin").set(keys[0], "other|fc2", px=2000)  # between GET and EXEC
        return got

    monkeypatch.setattr(redis.asyncio.client.Pipeline, "parse_response", parse)
    r = await api.fc1.post("/api/commands", json=api.iso())
    assert r.status_code == 503 and "safe to retry" in r.json()["message"]
    [row] = commands_for(api.v)
    assert (row["state"], row["failure_reason"]) == ("FAILED", "SEND_ERROR")
    assert api.tc() == []
    assert lock_value(api) == "other|fc2"  # not ours: left alone


def test_not_sent_allow_list() -> None:
    def chained(cause: OSError) -> redis.exceptions.ConnectionError:
        e = redis.exceptions.ConnectionError("Error connecting")  # how connect() raises it
        e.__context__ = cause
        return e

    assert not_sent(chained(ConnectionRefusedError(errno.ECONNREFUSED, "refused")))
    assert not_sent(redis.exceptions.AuthenticationError("invalid username-password pair"))
    assert not_sent(redis.exceptions.AuthorizationError("NOAUTH"))
    assert not_sent(redis.exceptions.ExternalAuthProviderError("provider down"))
    assert not_sent(PreSendError("before the MULTI"))
    for reply in REPLIES:  # incl. LOADING: BusyLoadingError is a ConnectionError subclass
        assert not_sent(BaseParser.parse_error(reply)), reply  # type: ignore[no-untyped-call]
    assert not not_sent(redis.exceptions.TimeoutError("Timeout reading from socket"))
    assert not not_sent(redis.exceptions.ConnectionError("Error while writing to socket"))
    assert not not_sent(redis.exceptions.ConnectionError("Connection closed by server."))
    assert not not_sent(chained(OSError("Multiple exceptions: ...")))  # errno unknown
    # redis-py's own check after EXEC replied: the transaction ran, so it may have landed
    assert not not_sent(
        redis.exceptions.ResponseError("Wrong number of response items from pipeline execution")
    )


async def test_queued_then_sent_before_xadd(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def spy(*a: Any) -> bool:
        seen.append(commands_for(api.v)[0]["state"])
        return await REAL_SEND(*a)

    monkeypatch.setattr(commands_mod, "send_locked", spy)
    r = await api.fc1.post("/api/commands", json={**api.iso(), "reason": "check valve A"})
    assert r.status_code == 202 and seen == ["SENT"]  # SENT is committed before the XADD
    [row] = commands_for(api.v)
    assert row["state"] == "SENT" and row["sent_at"] >= row["queued_at"]
    assert row["issued_by"] == "fc1" and row["hazardous"] is False
    assert row["args_hash"] == args_hash(api.v, "isolate_valve", {"valve": "A"})
    req, state = api.audits("fc1")
    assert (req["action"], req["reason"]) == ("CMD_REQUESTED", "check valve A")
    assert req["details"]["cmd_id"] == r.json()["cmd_id"]
    assert (state["action"], state["details"]["state"]) == ("CMD_STATE", "SENT")


# ---- the interlock covers the send (P-pf39) and concurrent requests
async def test_lock_lost_before_send_at_most_one_xadd(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands_mod, "LOCK_TTL_MS", 200)
    real_mark, calls = commands_mod.mark_sent, [0]

    async def stalled(*a: Any) -> bool:  # only the first request's DB phase stalls
        calls[0] += 1
        if calls[0] == 1:
            await asyncio.sleep(0.5)  # its 200 ms lock runs out meanwhile
        return await real_mark(*a)

    monkeypatch.setattr(commands_mod, "mark_sent", stalled)

    async def second() -> httpx.Response:
        await asyncio.sleep(0.3)  # after the first lock expired, before its send
        return await api.fc2.post("/api/commands", json=api.iso())

    first, late = await asyncio.gather(api.fc1.post("/api/commands", json=api.iso()), second())
    assert late.status_code == 202
    assert first.status_code == 503 and "safe to retry" in first.json()["message"]
    [tc] = api.tc()  # at most one XADD
    assert tc["cmd_id"] == late.json()["cmd_id"] and tc["issued_by"] == "fc2"
    lost = [r for r in commands_for(api.v) if str(r["cmd_id"]) == first_id(api, "fc1")]
    assert [(r["state"], r["failure_reason"]) for r in lost] == [("FAILED", "SEND_ERROR")]


def first_id(api: Api, user: str) -> str:
    [r] = [r for r in commands_for(api.v) if r["issued_by"] == user]
    return str(r["cmd_id"])


async def test_send_locked_needs_our_owner() -> None:
    v, r = vid(), aclient("api")
    key = f"cmdlock:{v}:isolate_valve:h"
    try:
        await r.set(key, "theirs|fc2", px=2000)
        assert await REAL_SEND(r, key, "mine|fc1", f"tc:{v}", "{}") is False
        assert stream_entries(f"tc:{v}") == []
        assert await REAL_SEND(r, key, "theirs|fc2", f"tc:{v}", "{}") is True
        assert len(stream_entries(f"tc:{v}")) == 1
    finally:
        await r.aclose()


async def test_two_confirms_with_one_token(api: Api) -> None:
    token = await confirm_token(api.fc1, api.vent())
    body = {**api.vent(), "confirm_token": token, "reason": REASON}
    a, b = await asyncio.gather(
        api.fc1.post("/api/commands", json=body), api.fc1.post("/api/commands", json=body)
    )
    assert sorted([a.status_code, b.status_code]) == [202, 409]
    assert {a.json().get("error"), b.json().get("error")} == {None, "CONFIRMATION_INVALID"}
    assert len(api.tc()) == 1


async def test_two_identical_posts_race(api: Api) -> None:
    a, b = await asyncio.gather(
        api.fc1.post("/api/commands", json=api.iso()), api.fc2.post("/api/commands", json=api.iso())
    )
    assert sorted([a.status_code, b.status_code]) == [202, 409]
    assert {a.json().get("error"), b.json().get("error")} == {None, "DUPLICATE_COMMAND"}
    assert len(api.tc()) == 1


async def test_ttls_are_the_design_values(api: Api) -> None:
    assert (commands_mod.LOCK_TTL_MS, commands_mod.CONFIRM_TTL_MS) == (2_000, 60_000)
    assert commands_mod.REASON_MIN == 10
    assert (await api.fc1.post("/api/commands", json=api.iso())).status_code == 202
    [key] = client("admin").keys(f"cmdlock:{api.v}:*")
    assert 1_000 < int(client("admin").pttl(key)) <= 2_000


async def test_monotonic_transitions() -> None:
    cmd_id, v = uuid4(), vid()
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO commands (cmd_id, vehicle, name, args, args_hash, hazardous, issued_by,"
            " state, queued_at, received_at)"
            " VALUES (%s, %s, 'isolate_valve', '{}', 'h', false, 'fc1', 'RECEIVED', now(), now())",
            (cmd_id, v),
        )
        assert await mark_sent(conn, cmd_id, "fc1", v) is False  # a late SENT never wins
    [row] = commands_for(v)
    assert (row["state"], row["sent_at"]) == ("RECEIVED", None)
    assert [a for a in audit_rows(action="CMD_STATE") if a["details"].get("vehicle") == v] == []


async def test_tc_entry_matches_schema(api: Api) -> None:
    r = await api.fc1.post("/api/commands", json=api.iso("B"))
    [tc] = api.tc()
    Draft202012Validator(load_schema("tc")).validate(tc)
    assert (tc["cmd_id"], tc["issued_by"], tc["name"], tc["args"]) == (
        r.json()["cmd_id"],
        "fc1",
        "isolate_valve",
        {"valve": "B"},
    )
    [(_, fields)] = stream_entries(f"tc:{api.v}")
    assert set(fields) == {"data"}  # D-SIM-04


# ---- input and dependency failures
async def test_invalid_args_422_without_side_effects(api: Api) -> None:
    confirms = set(client("admin").keys("confirm:*"))
    bad = [
        (api.vent(duration_s=11), "duration_s"),
        ({**api.iso(), "vehicle": "NYX-NOPE"}, "unknown vehicle"),
        ({**api.iso(), "name": "self_destruct"}, "unknown command"),
        ({**api.iso(), "args": {}}, "valve"),
    ]
    for body, text in bad:
        r = await api.fc1.post("/api/commands", json=body)
        assert r.status_code == 422 and r.json()["error"] == "INVALID_ARGS", r.text
        assert text in r.json()["message"]
    assert commands_for(api.v) == [] and api.tc() == []
    assert set(client("admin").keys("confirm:*")) == confirms
    assert client("admin").keys(f"cmdlock:{api.v}:*") == []


@pytest.mark.parametrize("hazardous", [False, True])
async def test_broker_down_503(api: Api, monkeypatch: pytest.MonkeyPatch, hazardous: bool) -> None:
    async def down(*a: Any, **k: Any) -> Any:  # logged in first: only the command path fails
        raise redis.exceptions.ConnectionError("Error 111 connecting to redis. Connection refused.")

    monkeypatch.setattr(api.app.state.redis, "set", down)
    r = await api.fc1.post("/api/commands", json=api.vent() if hazardous else api.iso())
    assert r.status_code == 503 and r.json()["error"] == "BROKER_DOWN"
    assert commands_for(api.v) == [] and api.tc() == []


async def test_db_down_before_send_503(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_db(*a: Any, **k: Any) -> Any:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(api.app.state.pool, "connection", no_db)
    r = await api.fc1.post("/api/commands", json=api.iso())
    assert r.status_code == 503 and r.json()["error"] == "UNAVAILABLE"
    assert api.tc() == [] and lock_value(api) is None  # nothing sent, lock given back
    g = await api.fc1.get("/api/commands")
    assert g.status_code == 503 and g.json()["error"] == "UNAVAILABLE"


async def test_row_moved_before_sent_is_not_sent(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    async def lost(*a: Any) -> bool:  # the tracker's 5 s timeout won the race for the row
        return False

    monkeypatch.setattr(commands_mod, "mark_sent", lost)
    r = await api.fc1.post("/api/commands", json=api.iso())
    assert r.status_code == 503 and "not sent" in r.json()["message"]
    assert api.tc() == [] and lock_value(api) is None


# ---- GET /api/commands
async def test_list_commands_filters(api: Api) -> None:
    first = (await api.fc1.post("/api/commands", json=api.iso("A"))).json()["cmd_id"]
    [row] = commands_for(api.v)
    second = (await api.fc1.post("/api/commands", json=api.iso("B"))).json()["cmd_id"]
    got = (await api.fc1.get("/api/commands", params={"vehicle": api.v})).json()
    assert [c["cmd_id"] for c in got] == [second, first]  # newest first
    assert got[0]["state"] == "SENT" and got[0]["outcome"] is None
    since = row["queued_at"].isoformat().replace("+00:00", "Z")
    later = (await api.fc1.get("/api/commands", params={"vehicle": api.v, "since": since})).json()
    assert first in [c["cmd_id"] for c in later]  # since is inclusive
    assert (await api.fc1.get("/api/commands", params={"vehicle": vid()})).json() == []
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://api")
    async with anon:
        assert (await anon.get("/api/commands")).status_code == 401


# ---- ATP-03 backend: API + in-process simulator + ingest worker on one vehicle (Task 7)
@dataclasses.dataclass
class Live:
    api: Api
    sim_port: int

    def row(self, cmd_id: str) -> dict[str, Any]:
        [r] = [r for r in commands_for(self.api.v) if str(r["cmd_id"]) == cmd_id]
        return r

    async def until_state(self, cmd_id: str, state: str, timeout: float) -> dict[str, Any]:
        await until(lambda: self.row(cmd_id)["state"] == state, timeout)
        return self.row(cmd_id)

    async def fault(self, name: str, **params: object) -> None:
        status = await asyncio.to_thread(post_fault, self.api.v, name, self.sim_port, **params)
        assert status == 204

    async def outcome(self, cmd_id: str) -> str | None:
        got = (await self.api.fc1.get("/api/commands", params={"vehicle": self.api.v})).json()
        [view] = [c for c in got if c["cmd_id"] == cmd_id]
        return view["outcome"]  # type: ignore[no-any-return]

    def actions(self, cmd_id: str) -> list[str]:
        mine = [a for a in self.api.audits() if a["details"].get("cmd_id") == cmd_id]
        return [a["action"] for a in mine]


@pytest.fixture
async def live(api: Api) -> AsyncIterator[Live]:
    async with running_sim(api.v) as port, worker([api.v]):
        await until(lambda: bool(stream_entries(f"tm:{api.v}")), 5)
        yield Live(api, port)


async def send_iso(live: Live) -> str:
    r = await live.api.fc1.post("/api/commands", json=live.api.iso())
    assert r.status_code == 202, r.text
    return str(r.json()["cmd_id"])


async def test_isolate_valve_verified(live: Live) -> None:
    cmd_id = await send_iso(live)
    r = await live.until_state(cmd_id, "VERIFIED", 1.0)
    assert r["queued_at"] <= r["sent_at"]
    assert r["received_at"] is not None and r["verified_at"] is not None
    assert r["received_at"] <= r["verified_at"]  # vehicle clock: tcack, then the echo frame
    assert r["sent_at"] - timedelta(seconds=1) <= r["received_at"]  # same host clock, loosely
    assert live.actions(cmd_id) == ["CMD_REQUESTED", "CMD_STATE", "CMD_STATE", "CMD_STATE"]


async def test_not_received_5s(live: Live) -> None:  # ATP-03: pause 6
    await live.fault("pause", duration_s=6)
    cmd_id = await send_iso(live)
    r = await live.until_state(cmd_id, "FAILED", 8)
    assert r["failure_reason"] == "NOT_RECEIVED"
    assert 5.0 <= (r["failed_at"] - r["sent_at"]).total_seconds() <= 6.5
    await until(lambda: live.row(cmd_id)["late_verified_at"] is not None, 5)
    r = live.row(cmd_id)
    assert r["late_ack_accepted"] is True
    assert [e["key"] for e in events_for(live.api.v, "LATE_VERIFICATION")] == [
        late_key(UUID(cmd_id))
    ]
    assert await live.outcome(cmd_id) == "EXECUTED_LATE"
    acts = live.actions(cmd_id)
    assert "CMD_LATE_ACK" in acts and "CMD_LATE_VERIFICATION" in acts


async def test_not_verified_15s(live: Live) -> None:  # ATP-03: hold_exec 16
    await live.fault("hold_exec", duration_s=16)
    cmd_id = await send_iso(live)
    await live.until_state(cmd_id, "RECEIVED", 2)
    r = await live.until_state(cmd_id, "FAILED", 18)
    assert r["failure_reason"] == "NOT_VERIFIED"
    assert 15.0 <= (r["failed_at"] - r["sent_at"]).total_seconds() <= 16.5
    await until(lambda: live.row(cmd_id)["late_verified_at"] is not None, 4)
    assert await live.outcome(cmd_id) == "EXECUTED_LATE"
    assert len(events_for(live.api.v, "LATE_VERIFICATION")) == 1


async def test_queued_never_sent_fails_not_received(live: Live) -> None:
    cmd_id = uuid4()
    with psycopg.connect(app_dsn("gs"), autocommit=True) as c:
        c.execute(
            "INSERT INTO commands (cmd_id, vehicle, name, args, args_hash, hazardous, issued_by,"
            " state, queued_at) VALUES (%s, %s, 'isolate_valve', '{}', 'h', false, 'fc1',"
            " 'QUEUED', clock_timestamp())",
            (cmd_id, live.api.v),
        )  # the API died between QUEUED and SENT (D-CMD-11)
    r = await live.until_state(str(cmd_id), "FAILED", 7)
    assert r["failure_reason"] == "NOT_RECEIVED" and r["sent_at"] is None
    assert 5.0 <= (r["failed_at"] - r["queued_at"]).total_seconds() <= 6.5
    assert await live.outcome(str(cmd_id)) == "UNKNOWN"


async def test_command_during_dropout(live: Live) -> None:  # OPS-3, R2-26
    await live.fault("dropout", duration_s=10)
    cmd_id = await send_iso(live)
    r = await live.until_state(cmd_id, "FAILED", 7)
    assert r["failure_reason"] == "NOT_RECEIVED"
    await until(lambda: live.row(cmd_id)["late_verified_at"] is not None, 8)  # after AOS
    assert live.row(cmd_id)["late_ack_at"] is None  # the tcack was lost with the link
    assert await live.outcome(cmd_id) == "EXECUTED_LATE"
