"""View loaders (gs.common.views): rows inserted by the test as gs_app, read back as SSE views."""

import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from jsonschema import Draft202012Validator
from psycopg import sql
from psycopg.types.json import Jsonb

from gs.common.interfaces import Alarm, Cmd, Link
from gs.common.missiondb import load_dir
from gs.common.views import alarm_views, command_views, event_views
from tests.support.env import now, vid
from tests.support.ingest import ensure_set
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn
from tests.support.redisx import load_schema

SET = load_dir(MISSION_DIR)


@pytest.fixture
async def conn() -> AsyncIterator[psycopg.AsyncConnection]:
    await ensure_set()
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        yield c


async def insert(conn: psycopg.AsyncConnection, table: str, **cols: Any) -> None:
    vals = [Jsonb(v) if isinstance(v, dict) else v for v in cols.values()]
    await conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        ),
        vals,
    )


async def command(conn: psycopg.AsyncConnection, v: str, queued_at: datetime, **over: Any) -> UUID:
    cmd_id = uuid4()
    cols = {
        "cmd_id": cmd_id, "vehicle": v, "name": "isolate_valve", "args": {"valve": "A"},
        "args_hash": "h", "hazardous": False, "reason": None, "issued_by": "fc1",
        "state": "SENT", "queued_at": queued_at, "sent_at": queued_at,
    }  # fmt: skip
    await insert(conn, "commands", **(cols | over))
    return cmd_id


async def alarm(
    conn: psycopg.AsyncConnection, v: str, param: str, state: str, severity: str, opened: datetime
) -> UUID:
    alarm_id = uuid4()
    await insert(
        conn, "alarms", alarm_id=alarm_id, vehicle=v, param=param, state=state,
        severity=severity, trigger_check="limit", limits_sha256=SET.limits_sha256,
        set_sha256=SET.set_sha256, opened_at=opened,
        closed_at=opened + timedelta(seconds=5) if state == "CLEARED" else None,
    )  # fmt: skip
    return alarm_id


async def transition(
    conn: psycopg.AsyncConnection,
    alarm_id: UUID,
    v: str,
    param: str,
    kind: str,
    t: datetime,
    level: str | None,
    value: float | None,
) -> None:
    await insert(
        conn, "alarm_events", key=f"{alarm_id}/{kind}/{t.isoformat()}", alarm_id=alarm_id,
        vehicle=v, param=param, kind=kind, check_kind="limit", level=level,
        severity=(level or "NOMINAL").split("_")[0], value=value,
        trigger_t_onboard=t, alert=kind in ("RAISE", "ESCALATE"),
        limits_sha256=SET.limits_sha256, set_sha256=SET.set_sha256,
    )  # fmt: skip


async def test_command_view_outcome_and_order(conn: psycopg.AsyncConnection) -> None:
    v, t0 = vid(), now()
    late = await command(
        conn, v, t0, state="FAILED", failure_reason="NOT_RECEIVED",
        failed_at=t0 + timedelta(seconds=5), late_verified_at=t0 + timedelta(seconds=7),
    )  # fmt: skip
    newer = await command(conn, v, t0 + timedelta(seconds=1))
    views = await command_views(conn, vehicle=v)
    assert [c.cmd_id for c in views] == [newer, late]  # newest queued_at first
    assert (views[1].state, views[1].failure_reason, views[1].outcome) == (
        "FAILED", "NOT_RECEIVED", "EXECUTED_LATE",
    )  # fmt: skip
    assert views[0].outcome is None and views[0].args == {"valve": "A"}
    assert [c.cmd_id for c in await command_views(conn, cmd_ids=[late])] == [late]
    assert [c.cmd_id for c in await command_views(conn, vehicle=v, limit=1)] == [newer]
    window = await command_views(conn, vehicle=v, since=t0, until=t0 + timedelta(seconds=1))
    assert [c.cmd_id for c in window] == [late]  # until is exclusive
    assert len(await command_views(conn, vehicle=v, limit=None)) == 2


async def test_alarm_view_level_last_and_ack_request(conn: psycopg.AsyncConnection) -> None:
    v, t0 = vid(), now()
    a = await alarm(conn, v, "cabin_pressure_kpa", "UNACK", "RED", t0)
    await transition(conn, a, v, "cabin_pressure_kpa", "RAISE", t0, "YELLOW_LOW", 97.9)
    await transition(
        conn, a, v, "cabin_pressure_kpa", "ESCALATE", t0 + timedelta(seconds=1), "RED_LOW", 95.5
    )
    req = uuid4()
    await insert(
        conn, "alarm_ack_request", request_id=req, alarm_id=a, vehicle=v, by_user="fc1", note="n"
    )
    (view,) = await alarm_views(conn, vehicle=v)
    assert (view.level, view.value, view.last.kind, view.last.value) == (
        "RED_LOW", 95.5, "ESCALATE", 95.5,
    )  # fmt: skip
    assert view.ack_request is not None and view.ack_request.request_id == req
    assert view.ack_request.status == "pending" and view.last.alert
    # an ACK row has no level or value: the alarm keeps both (P-pf4)
    await transition(conn, a, v, "cabin_pressure_kpa", "ACK", t0 + timedelta(seconds=2), None, None)
    await conn.execute("UPDATE alarms SET state='ACKED' WHERE alarm_id=%s", (a,))
    (acked,) = await alarm_views(conn, alarm_ids=[a])
    assert (acked.level, acked.value, acked.last.kind) == ("RED_LOW", 95.5, "ACK")
    rtn = await alarm(conn, v, "cabin_temp_c", "RTN_UNACK", "YELLOW", t0 + timedelta(seconds=3))
    await transition(conn, rtn, v, "cabin_temp_c", "RAISE", t0, "YELLOW_HIGH", 30.0)
    views = await alarm_views(conn, vehicle=v)
    assert [x.alarm_id for x in views] == [rtn, a]  # newest opened_at first
    assert views[0].level == "NOMINAL" and views[0].ack_request is None


async def test_alarm_views_open_only_and_filters(conn: psycopg.AsyncConnection) -> None:
    v, t0 = vid(), now()
    ids = {}
    for i, (param, state, sev) in enumerate(
        [("bus_voltage_v", "CLEARED", "YELLOW"), ("bus_current_a", "UNACK", "RED"),
         ("tank_pressure_bar", "ACKED", "YELLOW")]
    ):  # fmt: skip
        ids[state] = await alarm(conn, v, param, state, sev, t0 + timedelta(seconds=i))
        await transition(conn, ids[state], v, param, "RAISE", t0, f"{sev}_HIGH", 1.0)

    def got(views: Sequence[Any]) -> set[UUID]:
        return {x.alarm_id for x in views}

    assert got(await alarm_views(conn, vehicle=v)) == {ids["UNACK"], ids["ACKED"]}
    assert got(await alarm_views(conn, vehicle=v, open_only=False)) == set(ids.values())
    assert got(await alarm_views(conn, vehicle=v, severity="RED")) == {ids["UNACK"]}
    assert got(await alarm_views(conn, vehicle=v, state="ACKED")) == {ids["ACKED"]}
    assert got(await alarm_views(conn, vehicle=v, state="CLEARED")) == set()  # open only
    cleared = await alarm_views(conn, alarm_ids=[ids["CLEARED"]], open_only=False)
    assert cleared[0].level == "NOMINAL" and cleared[0].closed_at is not None


async def test_event_views_include_vehicle_null(conn: psycopg.AsyncConnection) -> None:
    v, other, t0 = vid(), vid(), now() - timedelta(seconds=10)
    keys = {k: f"{k}/{uuid4()}" for k in ("gap", "los", "rejected", "other")}
    await insert(conn, "events", key=keys["gap"], vehicle=v, kind="GAP", t_onboard=t0,
                 details={"lost": 3, "at": "2026-11-15T00:00:00.000000Z"})  # fmt: skip
    await insert(conn, "events", key=keys["los"], vehicle=v, kind="LOS",
                 t_onboard=t0 + timedelta(seconds=1))  # fmt: skip
    await insert(conn, "events", key=keys["rejected"], vehicle=None, kind="LIMITS_REJECTED",
                 details={"file": "limits.yaml"})  # fmt: skip
    await insert(conn, "events", key=keys["other"], vehicle=other, kind="GAP", t_onboard=t0)
    got = [e.key for e in await event_views(conn, vehicle=v, since=t0, limit=None)]
    assert keys["rejected"] in got and keys["other"] not in got
    assert got.index(keys["rejected"]) < got.index(keys["los"]) < got.index(keys["gap"])
    mine = await event_views(conn, vehicle=v, kinds=["GAP", "LOS"], since=t0)
    assert [e.key for e in mine] == [keys["los"], keys["gap"]]
    assert mine[1].details == {"lost": 3, "at": "2026-11-15T00:00:00.000000Z"}
    until = await event_views(
        conn, vehicle=v, kinds=["GAP", "LOS"], until=t0 + timedelta(seconds=1)
    )
    assert keys["gap"] in [e.key for e in until] and keys["los"] not in [e.key for e in until]
    assert len(await event_views(conn, vehicle=v, kinds=["GAP", "LOS"], since=t0, limit=1)) == 1


async def test_views_validate_against_schemas(conn: psycopg.AsyncConnection) -> None:
    v, t0 = vid(), now()
    await command(conn, v, t0, state="FAILED", failure_reason="SEND_ERROR", failed_at=t0)
    a = await alarm(conn, v, "rate_x_dps", "UNACK", "YELLOW", t0)
    await transition(conn, a, v, "rate_x_dps", "RAISE", t0, "YELLOW_HIGH", 2.5)
    await insert(conn, "events", key=f"LOS/{uuid4()}", vehicle=v, kind="LOS", t_onboard=t0)
    msgs = (
        [("sse/cmd", Cmd(command=c)) for c in await command_views(conn, vehicle=v)]
        + [("sse/alarm", Alarm(alarm=x)) for x in await alarm_views(conn, vehicle=v)]
        + [("sse/link", Link(event=e)) for e in await event_views(conn, vehicle=v, kinds=["LOS"])]
    )
    assert len(msgs) == 3
    for name, msg in msgs:
        Draft202012Validator(load_schema(name)).validate(json.loads(msg.model_dump_json()))
