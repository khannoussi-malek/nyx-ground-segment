"""The shared pg/redis/http helpers behave as their signatures promise (Track 0 contract)."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from jsonschema import ValidationError
from psycopg.rows import dict_row

from gs.api.app import create_app
from tests.support.env import now, vid
from tests.support.http import FC1_PW, api_settings, authed, get_json
from tests.support.pg import (
    ack_requests_for,
    app_dsn,
    audit_rows,
    commands_for,
    events_for,
    insert_rows,
    open_alarm,
    stored,
)
from tests.support.redisx import produce_values, stream_entries, validate_ui_event, xadd_data


def test_events_for_none_matches_ground_events() -> None:
    t0 = now()
    key = f"TEST/{vid()}"
    with psycopg.connect(app_dsn("gs")) as c:
        c.execute(
            "insert into events (key, vehicle, kind) values (%s, NULL, 'INTERFACE_MISMATCH')",
            (key,),
        )
    assert [e["key"] for e in events_for(None, "INTERFACE_MISMATCH", since=t0)] == [key]
    assert events_for(vid()) == []


def test_audit_rows_filters() -> None:
    actor = "t-" + vid()
    t0 = now()
    with psycopg.connect(app_dsn("gs")) as c:
        for action in ("A1", "A2"):
            c.execute("insert into audit_log (actor, action) values (%s, %s)", (actor, action))
    assert [r["action"] for r in audit_rows(actor=actor, since=t0)] == ["A1", "A2"]
    assert [r["action"] for r in audit_rows("A2", actor)] == ["A2"]
    assert audit_rows(actor=actor, since=t0 + timedelta(hours=1)) == []


def test_command_ack_alarm_lookups_are_empty_for_a_new_vehicle() -> None:
    v = vid()
    assert (
        commands_for(v) == []
        and ack_requests_for(v) == []
        and open_alarm(v, "cabin_temp_c") is None
    )


def test_insert_rows_writes_telemetry() -> None:
    v, t = vid(), now()
    insert_rows(v, [{"t_onboard": t, "seq": 1}, {"t_onboard": t + timedelta(seconds=1), "seq": 2}])
    assert stored(v) == 2
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        (mode,) = {
            r["mode"] for r in c.execute("select mode from telemetry where vehicle=%s", (v,))
        }
    assert mode == "TEST"


def test_produce_values_and_last_cmd_id() -> None:
    from uuid import uuid4

    v, cid = vid(), uuid4()
    produce_values(v, {"cabin_temp_c": [22.0, 31.0]}, cid, start_seq=5)
    frames = [json.loads(f["data"]) for _, f in stream_entries(f"tm:{v}")]
    assert [(f["seq"], f["cabin_temp_c"], f["last_cmd_id"]) for f in frames] == [
        (5, 22.0, str(cid)),
        (6, 31.0, str(cid)),
    ]


def test_xadd_data_and_stream_entries() -> None:
    s = f"tm:{vid()}"
    entry_id = xadd_data(s, '{"a":1}')
    assert stream_entries(s) == [(entry_id, {"data": '{"a":1}'})]


def test_validate_ui_event_takes_raw_or_parsed() -> None:
    good = {"kind": "resync", "vehicle": "NYX-TA1"}
    validate_ui_event(good)
    validate_ui_event(json.dumps(good))
    with pytest.raises(ValidationError):
        validate_ui_event({"kind": "resync", "vehicle": "bad"})


async def test_authed_client_is_logged_in_and_get_json_reads_it(users_file: Path) -> None:
    app = create_app(api_settings(users_file))
    async with authed(app, "fc1", FC1_PW) as client:
        assert isinstance(client, httpx.AsyncClient)
        body = await get_json(client, "/api/vehicles")
    assert body
