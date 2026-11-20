"""GET /api/summary: the "Since..." list (D-EXT-21, EXT-05)."""

import dataclasses
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from gs.api import dbguard
from gs.api.app import create_app
from gs.common.migrate import add_columns
from gs.common.missiondb import load_dir
from tests.support.env import vid
from tests.support.http import CLIENT_HEADERS as H
from tests.support.http import FC1_PW, api_settings, authed, login
from tests.support.ingest import ensure_set
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn, audit_rows, owner_dsn, throwaway_db

SET = load_dir(MISSION_DIR)
P1 = next(iter(SET.parameters))
SEC = timedelta(seconds=1)
Client = tuple[httpx.AsyncClient, str]


def _z(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sql(query: str, *args: object, db: str = "gs") -> None:
    with psycopg.connect(app_dsn(db), autocommit=True) as c:
        c.execute(query, args)


def _event(v: str, kind: str, t: datetime | None, db: str = "gs", **details: object) -> None:
    _sql(
        "INSERT INTO events (key, vehicle, kind, t_onboard, details) VALUES (%s, %s, %s, %s, %s)",
        f"{kind}/{v}/{uuid4()}", v, kind, t, Jsonb(details), db=db,
    )  # fmt: skip


def _alarm(v: str, transitions: list[tuple[str, datetime, str, str | None]]) -> None:
    """One RED alarm with (kind, t, by_user, note) rows."""
    alarm_id = uuid4()
    _sql(
        "INSERT INTO alarms (alarm_id, vehicle, param, state, severity, trigger_check,"
        " limits_sha256, set_sha256, opened_at) VALUES (%s, %s, %s, 'CLEARED', 'RED',"
        " 'limit', %s, %s, %s)",
        alarm_id, v, P1, SET.limits_sha256, SET.set_sha256, transitions[0][1],
    )  # fmt: skip
    for kind, t, user, note in transitions:
        _sql(
            "INSERT INTO alarm_events (key, alarm_id, vehicle, param, kind, check_kind, level,"
            " severity, value, trigger_t_onboard, processed_at, alert, limits_sha256, set_sha256,"
            " by_user, note) VALUES (%s, %s, %s, %s, %s, 'limit', 'RED_HIGH', 'RED', 9.5,"
            " %s, %s, false, %s, %s, %s, %s)",
            f"{alarm_id}/{kind}", alarm_id, v, P1, kind,
            None if kind == "ACK" else t, t, SET.limits_sha256, SET.set_sha256, user, note,
        )  # fmt: skip


def _command(v: str, t: datetime) -> None:
    _sql(
        "INSERT INTO commands (cmd_id, vehicle, name, args, args_hash, hazardous, issued_by, state,"
        " failure_reason, queued_at, sent_at, failed_at) VALUES (%s, %s, 'isolate_valve', %s, 'h',"
        " false, 'fc1', 'FAILED', 'REJECTED', %s, %s, %s)",
        uuid4(), v, Jsonb({"valve": "A"}), t, t, t + SEC,
    )  # fmt: skip


@pytest.fixture
async def summ(users_file: Path) -> AsyncIterator[Client]:
    v = vid()
    app = create_app(dataclasses.replace(api_settings(users_file), vehicles=(v,)))
    async with authed(app) as c:
        yield c, v


@pytest.fixture
def empty_db() -> Iterator[str]:
    """No LOGOUT and no session anywhere: the defaults of `since` can be told apart."""
    with throwaway_db() as db:
        with psycopg.connect(owner_dsn(db), autocommit=True) as c:
            add_columns(c, SET)
        yield db


@pytest.fixture
async def fresh(users_file: Path, empty_db: str) -> AsyncIterator[Client]:
    v = vid()
    s = dataclasses.replace(api_settings(users_file, database_url=app_dsn(empty_db)), vehicles=(v,))
    async with authed(create_app(s)) as c:
        yield c, v


async def test_lists_everything_in_order(summ: Client) -> None:
    c, v = summ
    await ensure_set()
    t0 = datetime.now(UTC) - timedelta(minutes=10)
    _alarm(v, [("RAISE", t0 + 1 * SEC, "system", None),
               ("ACK", t0 + 20 * SEC, "fc2", "valve closed, leak stopped"),
               ("CLEAR", t0 + 40 * SEC, "system", None)])  # fmt: skip
    _command(v, t0 + 10 * SEC)
    _event(v, "LOS", t0 + 30 * SEC)
    _event(v, "AOS", t0 + 35 * SEC, lost_frames=120)
    _event(v, "LIMITS_APPLIED", t0 + 50 * SEC, limits_sha256="a" * 64)
    _event(v, "SEQ_JUMP", t0 + 55 * SEC)  # not on the list
    _event(v, "LOS", t0 - 3600 * SEC)  # before since
    r = await c.get("/api/summary", params={"vehicle": v, "since": _z(t0 - SEC)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["vehicle"], body["since_source"]) == (v, "query")
    items = body["items"]
    assert [(i["source"], i["kind"]) for i in items] == [
        ("alarm", "RAISE"), ("command", "COMMAND"), ("alarm", "ACK"), ("event", "LOS"),
        ("event", "AOS"), ("alarm", "CLEAR"), ("event", "LIMITS_APPLIED"),
    ]  # fmt: skip
    assert [i["t"] for i in items] == sorted(i["t"] for i in items)
    assert all(i["t"].endswith("Z") for i in items) and body["since"].endswith("Z")
    text = {i["kind"]: i["text"] for i in items}
    assert text["ACK"] == f"{v} {P1} alarm acknowledged by fc2: valve closed, leak stopped"
    assert items[2]["alarm"]["transition"]["note"] == "valve closed, leak stopped"
    assert "not executed" in text["COMMAND"] or "did not execute" in text["COMMAND"]
    assert items[1]["command"]["outcome"] == "NOT_EXECUTED"
    assert text["AOS"] == f"{v} AOS – 120 frames lost"
    assert text["LOS"].startswith(f"{v} LOS since ") and text["LOS"].endswith("Z")
    assert text["LIMITS_APPLIED"].startswith("mission db applied: limits aaaaaaaa")


async def test_default_since_last_logout(summ: Client) -> None:
    c, v = summ
    _event(v, "GAP", datetime.now(UTC) - 60 * SEC, lost=3)  # before the logout
    assert (await c.post("/api/logout", headers=H)).status_code == 204
    _event(v, "GAP", datetime.now(UTC), lost=7)  # after it
    assert (await login(c, "fc1", FC1_PW)).status_code == 204
    r = await c.get("/api/summary", params={"vehicle": v})
    assert r.status_code == 200, r.text
    body = r.json()
    out = audit_rows(action="LOGOUT", actor="fc1")[-1]["at"]
    assert (body["since_source"], body["since"]) == ("last_logout", _z(out))
    assert [i["text"] for i in body["items"]] == [f"{v} GAP – 7 frames lost"]


async def test_default_since_session_start(fresh: Client, empty_db: str) -> None:
    c, v = fresh
    started = datetime.now(UTC) - timedelta(hours=1)
    _sql("INSERT INTO test_session (vehicle, name, conductor, started_at)"
         " VALUES (%s, 'pass', 'fc1', %s)", v, started, db=empty_db)  # fmt: skip
    _event(v, "GAP", started - 60 * SEC, db=empty_db, lost=1)
    _event(v, "GAP", started + 60 * SEC, db=empty_db, lost=2)
    body = (await c.get("/api/summary", params={"vehicle": v})).json()
    assert (body["since_source"], body["since"]) == ("session_start", _z(started))
    assert [i["text"] for i in body["items"]] == [f"{v} GAP – 2 frames lost"]


async def test_since_required(fresh: Client) -> None:
    c, v = fresh
    r = await c.get("/api/summary", params={"vehicle": v})
    assert (r.status_code, r.json()["error"]) == (400, "SINCE_REQUIRED")


async def test_window_too_large(summ: Client) -> None:
    c, v = summ
    now = datetime.now(UTC)
    ok = await c.get("/api/summary", params={"vehicle": v, "since": _z(now - timedelta(days=6))})
    assert ok.status_code == 200
    r = await c.get(
        "/api/summary", params={"vehicle": v, "since": _z(now - timedelta(days=7, seconds=5))}
    )
    assert (r.status_code, r.json()["error"]) == (400, "WINDOW_TOO_LARGE")


@pytest.mark.parametrize("since", ["yesterday", "2020-01-01T00:00:00+02:00", "2020-01-01"])
async def test_unparsable_since_is_422(summ: Client, since: str) -> None:
    c, v = summ
    assert (await c.get("/api/summary", params={"vehicle": v, "since": since})).status_code == 422


async def test_future_since_is_400(summ: Client) -> None:
    c, v = summ
    since = _z(datetime.now(UTC) + timedelta(minutes=5))
    r = await c.get("/api/summary", params={"vehicle": v, "since": since})
    assert (r.status_code, r.json()["error"]) == (400, "INVALID_WINDOW")


async def test_login_required(summ: Client) -> None:
    c, v = summ
    anon = httpx.AsyncClient(transport=c._transport, base_url="http://api")
    r = await anon.get("/api/summary", params={"vehicle": v, "since": _z(datetime.now(UTC))})
    assert (r.status_code, r.json()["error"]) == (401, "UNAUTHENTICATED")
    await anon.aclose()


async def test_unknown_vehicle_404_and_recorded_vehicle_accepted(summ: Client) -> None:
    c, _ = summ
    since = _z(datetime.now(UTC) - 60 * SEC)
    r = await c.get("/api/summary", params={"vehicle": vid(), "since": since})
    assert (r.status_code, r.json()["error"]) == (404, "NOT_FOUND")
    recorded = vid()  # not live here, but the ingest stored frames for it once
    _sql("INSERT INTO seq_state (vehicle, epoch, last_seq, last_t, period_s)"
         " VALUES (%s, now(), 0, now(), 0.1)", recorded)  # fmt: skip
    r = await c.get("/api/summary", params={"vehicle": recorded, "since": since})
    assert r.status_code == 200


async def test_database_down_is_503(summ: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    c, v = summ
    monkeypatch.setattr(dbguard, "DB_TIMEOUT_S", 0.3)
    bad = AsyncConnectionPool("postgresql://x@127.0.0.1:1/x", min_size=1, open=False)
    await bad.open(wait=False)
    c._transport.app.state.pool = bad  # type: ignore[attr-defined]
    try:
        r = await c.get("/api/summary", params={"vehicle": v, "since": _z(datetime.now(UTC))})
    finally:
        await bad.close()
    assert (r.status_code, r.json()["error"]) == (503, "UNAVAILABLE")
