"""The API follows mission-db reloads (Task 12; D-EXT-02, D-CMD-09, G31, EX-25, D-EXT-19)."""

import asyncio
import dataclasses
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import psycopg
import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.api.app import create_app
from gs.common.interfaces import Link
from gs.common.migrate import add_columns
from gs.common.missiondb import load_dir
from gs.ingest.events import GroundEvent, rejected_key
from gs.ingest.main import publish_events
from gs.simulator.frames import make_frame
from tests.support.env import now, until, vid
from tests.support.http import api_settings, authed, get_json
from tests.support.missionfiles import add_raw_parameter, edit, mission_copy, set_formula
from tests.support.pg import app_dsn, audit_rows, events_for, owner_dsn, rows, throwaway_db
from tests.support.redisx import aclient, stream_entries

RELOAD_S = 5  # two equal polls 1 s apart, plus margin


def api_on(users_file: Path, m: Path) -> Any:
    return create_app(dataclasses.replace(api_settings(users_file), mission_db=m))


async def mission_db(c: httpx.AsyncClient) -> dict[str, Any]:
    body: dict[str, Any] = await get_json(c, "/api/mission-db")
    return body


async def command(c: httpx.AsyncClient, name: str, args: dict[str, Any]) -> httpx.Response:
    return await c.post("/api/commands", json={"vehicle": "NYX-TA1", "name": name, "args": args})


async def test_definitions_follow_reload(users_file: Path, tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    async with authed(api_on(users_file, m)) as c:
        before = (await mission_db(c))["set_sha256"]
        edit(m / "commands.yaml", "max: 10", "max: 8")
        want = set_formula(m)
        deadline = now().timestamp() + RELOAD_S
        while (db := await mission_db(c))["set_sha256"] != want:
            assert now().timestamp() < deadline, "the API did not follow the reload"
            await asyncio.sleep(0.1)
        assert want != before
        assert db["commands"]["vent_cabin"]["args"]["duration_s"]["max"] == 8
        r = await command(c, "vent_cabin", {"duration_s": 9})  # in bounds before the reload
    assert (r.status_code, r.json()["error"]) == (422, "INVALID_ARGS")


async def test_hazardous_downgrade_needs_restart(users_file: Path, tmp_path: Path) -> None:
    m = mission_copy(tmp_path)  # G31: ingest applies it, the API's own reload rejects it
    app = api_on(users_file, m)
    async with authed(app) as c:
        before = await mission_db(c)
        strip = app.state.hub.subscribe()  # what the API's SSE clients receive
        # a set no earlier run stored: only the service whose insert wins announces it
        edit(m / "commands.yaml", "hazardous: true", f"hazardous: false  # {vid()}")
        key = rejected_key(set_formula(m))

        seen: list[str] = []  # link keys the API's clients got (it forwards other traffic too)

        def strip_has_it() -> bool:
            while not strip.queue.empty():
                f = strip.queue.get_nowait().decode()
                if f.startswith("event: link"):
                    seen.append(json.loads(f.split("data: ", 1)[1])["event"]["key"])
            return key in seen

        await until(strip_has_it, RELOAD_S)
        after = await mission_db(c)
        health = (await c.get("/healthz")).json()  # P-pf46: the API lags ingest until restart
        assert health["status"] == "degraded"
        assert health["checks"]["mission_db"] == "pending API restart"
        vent = await command(c, "vent_cabin", {"duration_s": 5})
    assert (vent.status_code, vent.json()["error"]) == (409, "HAZARDOUS_CONFIRMATION_REQUIRED")
    assert seen.count(key) == 1
    (ev,) = [e for e in events_for(None, "LIMITS_REJECTED") if e["key"] == key]
    d = ev["details"]
    assert (d["file"], d["by"]) == ("commands.yaml", "api")
    assert d["error"] == (
        "mission db not updated in the API until API restart (hazardous downgrade: vent_cabin)"
    )
    assert after["set_sha256"] == before["set_sha256"]
    assert after["commands"]["vent_cabin"]["hazardous"] is True  # still enforced


async def test_hazardous_change_audited(users_file: Path, tmp_path: Path) -> None:  # EX-25
    m = mission_copy(tmp_path)
    t0 = now()
    async with authed(api_on(users_file, m)) as c:
        before = (await mission_db(c))["set_sha256"]
        edit(
            m / "commands.yaml",
            "hazardous: false",
            "hazardous: true\n    hazard: Isolates the cabin from the supply",
        )
        want = set_formula(m)

        def reloaded() -> list[dict[str, Any]]:
            rows = audit_rows("LIMITS_RELOADED", since=t0)
            return [r for r in rows if r["details"]["set_sha256"] == want]

        await until(lambda: bool(reloaded()), RELOAD_S)
        assert (await mission_db(c))["commands"]["isolate_valve"]["hazardous"] is True
        r = await command(c, "isolate_valve", {"valve": "A"})  # D-CMD-06 from the new set
    assert (r.status_code, r.json()["error"]) == (409, "HAZARDOUS_CONFIRMATION_REQUIRED")
    (row,) = reloaded()
    assert row["actor"] == "system" and row["details"]["hazardous_changed"] == ["isolate_valve"]
    assert row["details"]["previous_set_sha256"] == before
    assert set(row["details"]) == {
        "set_sha256", "limits_sha256", "commands_sha256", "parameters_sha256",
        "hazardous_changed", "previous_set_sha256",
    }  # fmt: skip


async def test_frame_schema_served(users_file: Path) -> None:  # D-EXT-19
    app = create_app(api_settings(users_file))
    async with authed(app) as c:
        schema = await get_json(c, "/api/mission-db/frame-schema")
    mdb = app.state.watcher.current
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(mdb.raw_names) <= set(schema["properties"])  # a frame carries raw values only
    assert not set(mdb.derived_names) & set(schema["properties"])  # derived: computed on ground
    frame = json.loads(make_frame("NYX-TA1", 7, datetime.now(UTC), random.Random(7)))
    jsonschema.Draft202012Validator(schema).validate(frame)
    del frame["seq"]
    assert not jsonschema.Draft202012Validator(schema).is_valid(frame)


async def test_ingest_publishes_rejected_sets_as_links() -> None:  # D-UI-19
    v = vid()
    ev = GroundEvent(f"LIMITS_REJECTED/test-{v}", None, "LIMITS_REJECTED", None, {"file": "x"})
    r = aclient("ingest")
    try:
        await publish_events(r)([ev])
    finally:
        await r.aclose()
    msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
    (m,) = [x for x in msgs if x["kind"] == "link" and x["event"]["key"] == ev.key]
    Link.model_validate(m)


async def test_ingest_rejected_set_publish_error_is_contained() -> None:
    down = redis.asyncio.Redis(host="127.0.0.1", port=1, retry=Retry(NoBackoff(), 0))
    ev = GroundEvent(f"LIMITS_REJECTED/test-{vid()}", None, "LIMITS_REJECTED", None, {})
    try:
        await publish_events(down)([ev])  # logged, never raised into the watcher
    finally:
        await down.aclose()


async def test_api_start_rejection_reaches_consoles_in_the_snapshot(
    users_file: Path, tmp_path: Path
) -> None:  # P-pf9, Task 27b: no console is connected while the lifespan starts
    m, token = mission_copy(tmp_path), vid()
    edit(m / "commands.yaml", "Close the cabin isolation valve", f"Close valve {token}")
    add_raw_parameter(m, "  cabin_humidity_pct: {unit: pct, type: float}")  # no column: fall back
    key = rejected_key(set_formula(m))
    app = api_on(users_file, m)
    async with authed(app):
        snap = await app.state.hub.snapshot()
    assert [e.kind for e in snap.events if e.key == key] == ["LIMITS_REJECTED"]


async def test_calibration_and_unit_change_audited(users_file: Path, tmp_path: Path) -> None:
    """R23 M1. A throwaway database takes the new tank_pressure_bar_raw column."""
    m, shipped = mission_copy(tmp_path / "new"), mission_copy(tmp_path / "start")
    cal = "calibration: {table: [[0, 0], [9, 90]]}, "
    edit(m / "parameters.yaml", "bus_current_a: {unit: A,", "bus_current_a: {unit: mA,")
    edit(
        m / "parameters.yaml",
        "tank_pressure_bar: {unit: bar, ",
        f"tank_pressure_bar: {{unit: bar, {cal}",
    )
    with throwaway_db() as db:
        with psycopg.connect(owner_dsn(db), autocommit=True) as c:
            add_columns(c, load_dir(m))
        cfg = dataclasses.replace(
            api_settings(users_file, database_url=app_dsn(db)), mission_db=shipped
        )

        def reloaded() -> list[dict[str, Any]]:
            return rows("select * from audit_log where action='LIMITS_RELOADED' order by id", db=db)

        async with authed(create_app(cfg)):
            (shipped / "parameters.yaml").write_bytes((m / "parameters.yaml").read_bytes())
            await until(lambda: len(reloaded()) >= 1, RELOAD_S)
            assert reloaded()[-1]["details"]["parameters_changed"] == {
                "bus_current_a": ["unit"], "tank_pressure_bar": ["calibration"],
            }  # fmt: skip
            edit(shipped / "limits.yaml", "yellow_high: 27.0", "yellow_high: 26.0")  # limits only
            await until(lambda: len(reloaded()) >= 2, RELOAD_S)
        assert "parameters_changed" not in reloaded()[-1]["details"]
