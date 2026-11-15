import asyncio
import csv
import dataclasses
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from gs.api import dbguard, report
from gs.api.app import create_app
from gs.api.bodies import ReportConflict, SessionReport
from gs.api.sessions import _view
from gs.common.missiondb import load_dir
from tests.support.env import vid
from tests.support.http import CLIENT_HEADERS as H
from tests.support.http import FC2_PW, api_settings, authed, login
from tests.support.ingest import ensure_set
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn, audit_rows, insert_rows


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


# ---- Task 22: the frozen report -------------------------------------------------------------
SET = load_dir(MISSION_DIR)
P1, P2 = list(SET.parameters)[:2]
T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)
SEC = timedelta(seconds=1)


def _sql(query: str, *args: object) -> None:
    with psycopg.connect(app_dsn("gs"), autocommit=True) as c:
        c.execute(query, args)


def _session(v: str, ended: bool = True, seconds: int = 10) -> int:
    with psycopg.connect(app_dsn("gs"), autocommit=True) as c:
        row = c.execute(
            "INSERT INTO test_session (vehicle, name, conductor, started_at, ended_at, ended_by)"
            " VALUES (%s, 'pass', 'fc1', %s, %s, %s) RETURNING id",
            (v, T0, T0 + seconds * SEC if ended else None, "fc1" if ended else None),
        ).fetchone()
    assert row
    return int(row[0])


def _watermark(v: str, last_t: datetime, period: float = 0.1) -> None:
    _sql(
        "INSERT INTO seq_state (vehicle, epoch, last_seq, last_t, period_s)"
        " VALUES (%s, %s, 0, %s, %s)",
        v, T0, last_t, period,
    )  # fmt: skip


def _frames(v: str, n: int, p2_every: int = 10, extra_seq: int = 0) -> None:
    insert_rows(
        v,
        [
            {"t_onboard": T0 + i * timedelta(milliseconds=100), "seq": extra_seq + i, P1: float(i)}
            | ({P2: 5.0} if i < p2_every else {})
            for i in range(n)
        ],
    )


@pytest.fixture
async def rep(users_file: Path) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    v = vid()
    app = create_app(dataclasses.replace(api_settings(users_file), vehicles=(v,)))
    async with authed(app) as c:
        yield c, v


def _event(v: str, kind: str, t: datetime, **details: object) -> None:
    _sql(
        "INSERT INTO events (key, vehicle, kind, t_onboard, details) VALUES (%s, %s, %s, %s, %s)",
        f"{kind}/{v}/{t.isoformat()}", v, kind, t, Jsonb(details),
    )  # fmt: skip


async def _alarm_and_command(v: str) -> None:
    await ensure_set()
    alarm_id = uuid4()
    _sql(
        "INSERT INTO alarms (alarm_id, vehicle, param, state, severity, trigger_check,"
        " limits_sha256, set_sha256, opened_at) VALUES (%s, %s, %s, 'UNACK', 'RED',"
        " 'limit', %s, %s, %s)",
        alarm_id, v, P1, SET.limits_sha256, SET.set_sha256, T0 + 2 * SEC,
    )  # fmt: skip
    for kind, at, level in (("RAISE", 2, "RED_HIGH"), ("RAISE", 20, "RED_HIGH")):  # 2nd is outside
        _sql(
            "INSERT INTO alarm_events (key, alarm_id, vehicle, param, kind, check_kind, level,"
            " severity, value, trigger_t_onboard, processed_at, alert, limits_sha256, set_sha256)"
            " VALUES (%s, %s, %s, %s, %s, 'limit', %s, 'RED', 9.5, %s, %s, true, %s, %s)",
            f"{alarm_id}/{at}", alarm_id, v, P1, kind, level, T0 + at * SEC, T0 + at * SEC,
            SET.limits_sha256, SET.set_sha256,
        )  # fmt: skip
    _sql(
        "INSERT INTO commands (cmd_id, vehicle, name, args, args_hash, hazardous, issued_by, state,"
        " failure_reason, queued_at, sent_at, failed_at) VALUES (%s, %s, 'isolate_valve', %s, 'h',"
        " false, 'fc1', 'FAILED', 'REJECTED', %s, %s, %s)",
        uuid4(), v, Jsonb({"valve": "A"}), T0 + 3 * SEC, T0 + 3 * SEC, T0 + 4 * SEC,
    )  # fmt: skip


async def test_report_contents(rep: tuple[httpx.AsyncClient, str]) -> None:
    c, v = rep
    sid = _session(v)
    _frames(v, 100)
    insert_rows(v, [{"t_onboard": T0 + 10 * SEC, "seq": 500, P1: 1000.0}])  # end is exclusive
    insert_rows(v, [{"t_onboard": T0 - SEC, "seq": 501, P1: -1000.0}])
    _watermark(v, T0 + 10 * SEC)
    _event(v, "GAP", T0 + 1 * SEC, lost=100)
    _event(v, "SEQ_RESET", T0 + 2 * SEC)
    _event(v, "POISON", T0 + 3 * SEC)  # not a link event
    _event(v, "GAP", T0 + 60 * SEC, lost=1)  # outside the window
    await _alarm_and_command(v)
    r = await c.get(f"/api/sessions/{sid}/report")
    assert r.status_code == 200, r.text
    rp = r.json()
    assert (rp["complete"], rp["note"], rp["frames"], rp["expected"]) == (True, None, 100, 100)
    assert rp["duration_s"] == 10.0
    assert rp["parameters"][P1] == {
        "unit": SET.parameters[P1].unit, "min": 0.0, "max": 99.0, "avg": 49.5, "n": 100,
    }  # fmt: skip
    assert rp["parameters"][P2]["n"] == 10 and rp["parameters"][P2]["avg"] == 5.0
    kinds = sorted(e["kind"] for e in rp["link_events"])
    assert kinds == ["GAP", "SEQ_RESET"]
    assert next(e for e in rp["link_events"] if e["kind"] == "GAP")["details"]["lost"] == 100
    assert [(a["param"], a["transition"]["kind"]) for a in rp["alarms"]] == [(P1, "RAISE")]
    assert [(x["name"], x["outcome"]) for x in rp["commands"]] == [
        ("isolate_valve", "NOT_EXECUTED")
    ]


async def test_report_pending_until_watermark(
    rep: tuple[httpx.AsyncClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, v = rep
    monkeypatch.setattr(report, "FREEZE_AFTER_S", 10**9)  # the session "just ended"
    sid = _session(v)
    _frames(v, 50)
    _watermark(v, T0 + 5 * SEC)  # vehicle data has not reached ended_at
    r = await c.get(f"/api/sessions/{sid}/report")
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "REPORT_PENDING"
    assert (r.json()["frames"], r.json()["expected"]) == (50, 100)
    csv_r = await c.get(f"/api/sessions/{sid}/report.csv")
    assert (csv_r.status_code, csv_r.json()["error"]) == (409, "REPORT_PENDING")
    _frames(v, 100, extra_seq=1000)  # rows past the first 50 arrive ... (same t, new seq)
    _sql("UPDATE seq_state SET last_t = %s WHERE vehicle = %s", T0 + 10 * SEC, v)
    done = await c.get(f"/api/sessions/{sid}/report")
    assert done.status_code == 200 and done.json()["complete"] is True


async def test_report_frozen(rep: tuple[httpx.AsyncClient, str]) -> None:
    c, v = rep
    sid = _session(v)
    _frames(v, 100)
    _watermark(v, T0 + 10 * SEC)
    first = (await c.get(f"/api/sessions/{sid}/report")).json()
    assert first["frames"] == 100
    insert_rows(v, [{"t_onboard": T0 + 5 * SEC + timedelta(milliseconds=50), "seq": 9000, P1: 7.0}])
    _event(v, "GAP", T0 + 4 * SEC, lost=9)
    assert (await c.get(f"/api/sessions/{sid}/report")).json() == first
    again = await c.get(f"/api/sessions/{sid}/report.csv")
    assert again.status_code == 200
    stored = (
        psycopg.connect(app_dsn("gs"))
        .execute("SELECT csv FROM session_report WHERE session_id = %s", (sid,))
        .fetchone()
    )
    assert stored and stored[0] == again.text


async def test_report_incomplete_after_30s(
    rep: tuple[httpx.AsyncClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, v = rep
    sid = _session(v)
    _frames(v, 50)
    _watermark(v, T0 + 5 * SEC)
    monkeypatch.setattr(report, "FREEZE_AFTER_S", -1_000_000)  # now >= ended_at + FREEZE_AFTER_S
    rp = (await c.get(f"/api/sessions/{sid}/report")).json()
    assert (rp["complete"], rp["note"]) == (False, "incomplete: 50 of 100 frames")


async def test_report_running_409(rep: tuple[httpx.AsyncClient, str]) -> None:
    c, v = rep
    sid = _session(v, ended=False)
    for path in ("report", "report.csv"):
        r = await c.get(f"/api/sessions/{sid}/{path}")
        assert (r.status_code, r.json()["error"]) == (409, "SESSION_RUNNING")


async def test_report_unknown_session_404(rep: tuple[httpx.AsyncClient, str]) -> None:
    r = await rep[0].get("/api/sessions/999999999/report")
    assert (r.status_code, r.json()["error"]) == (404, "NOT_FOUND")


async def test_report_csv_route(rep: tuple[httpx.AsyncClient, str]) -> None:
    c, v = rep
    sid = _session(v)
    _frames(v, 100)
    _watermark(v, T0 + 10 * SEC)
    js = (await c.get(f"/api/sessions/{sid}/report")).json()
    r = await c.get(f"/api/sessions/{sid}/report.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    p1 = next(x for x in rows if x["section"] == "parameter" and x["key"] == P1)
    assert (float(p1["min"]), float(p1["max"]), float(p1["avg"]), int(p1["n"])) == (
        js["parameters"][P1]["min"], js["parameters"][P1]["max"],
        js["parameters"][P1]["avg"], js["parameters"][P1]["n"],
    )  # fmt: skip
    assert [x for x in rows if x["section"] == "summary"][0]["n"] == "100"
    assert [x["key"] for x in rows if x["section"] == "parameter"] == list(SET.parameters)


async def test_silent_session_report(
    rep: tuple[httpx.AsyncClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, v = rep
    sid = _session(v)  # no frames, no seq_state at all
    monkeypatch.setattr(report, "FREEZE_AFTER_S", -1_000_000)
    rp = (await c.get(f"/api/sessions/{sid}/report")).json()
    assert (rp["complete"], rp["note"], rp["frames"]) == (False, "incomplete: 0 of 100 frames", 0)
    assert rp["parameters"][P1]["min"] is None and rp["parameters"][P1]["n"] == 0
    csv_r = await c.get(f"/api/sessions/{sid}/report.csv")
    assert csv_r.status_code == 200
    row = next(x for x in csv.DictReader(io.StringIO(csv_r.text)) if x["key"] == P1)
    assert (row["min"], row["max"], row["avg"]) == ("", "", "")


async def test_report_never_truncated(rep: tuple[httpx.AsyncClient, str]) -> None:
    c, v = rep
    sid = _session(v)
    _watermark(v, T0 + 10 * SEC)
    for i in range(120):  # more than any list default (limit=None, P-pf11)
        _event(v, "GAP", T0 + i * timedelta(milliseconds=50), lost=1)
    rp = (await c.get(f"/api/sessions/{sid}/report")).json()
    assert len(rp["link_events"]) == 120


@pytest.mark.parametrize("path", ["report", "report.csv"])
async def test_report_database_down_is_503(
    rep: tuple[httpx.AsyncClient, str], monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    c, v = rep
    sid = _session(v)
    monkeypatch.setattr(dbguard, "DB_TIMEOUT_S", 0.3)
    bad = AsyncConnectionPool("postgresql://x@127.0.0.1:1/x", min_size=1, open=False)
    await bad.open(wait=False)
    c._transport.app.state.pool = bad  # type: ignore[attr-defined]
    try:
        r = await c.get(f"/api/sessions/{sid}/{path}")
    finally:
        await bad.close()
    assert (r.status_code, r.json()["error"]) == (503, "UNAVAILABLE")


async def test_concurrent_readers_share_one_frozen_copy(rep: tuple[httpx.AsyncClient, str]) -> None:
    c, v = rep
    sid = _session(v)
    _frames(v, 100)
    _watermark(v, T0 + 10 * SEC)
    got = await asyncio.gather(*(c.get(f"/api/sessions/{sid}/report") for _ in range(4)))
    assert len({r.json()["frozen_at"] for r in got}) == 1


async def test_the_30s_rule_with_the_real_constant(rep: tuple[httpx.AsyncClient, str]) -> None:
    _, v = rep
    sid = _session(v)
    _frames(v, 50)
    _watermark(v, T0 + 5 * SEC)  # never reaches ended_at
    assert report.FREEZE_AFTER_S == 30
    end = T0 + 10 * SEC
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
        pending = await report.frozen_report(conn, sid, SET, end + timedelta(seconds=29.999))
        assert isinstance(pending, ReportConflict) and pending.error == "REPORT_PENDING"
        done = await report.frozen_report(conn, sid, SET, end + timedelta(seconds=30))
    assert isinstance(done, SessionReport)
    assert (done.complete, done.note) == (False, "incomplete: 50 of 100 frames")


async def test_report_survives_a_non_utc_session_timezone(
    rep: tuple[httpx.AsyncClient, str],
) -> None:
    _, v = rep
    sid = _session(v)
    _frames(v, 100)
    _watermark(v, T0 + 10 * SEC)
    await _alarm_and_command(v)
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
        await conn.execute("SET TIME ZONE 'Asia/Tokyo'")
        got = await report.frozen_report(conn, sid, SET, datetime.now(UTC))
    assert isinstance(got, SessionReport) and len(got.alarms) == 1
    assert got.session.started_at.utcoffset() == timedelta(0)


def test_session_view_converts_to_utc() -> None:
    tokyo = timezone(timedelta(hours=9))
    row = (1, "NYX-TA1", "n", "fc1", T0.astimezone(tokyo), None, None)
    assert _view(row).started_at.utcoffset() == timedelta(0)
