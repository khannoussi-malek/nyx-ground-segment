"""Simulator command path against the compose Redis (D-SIM-06, D-SIM-10, D-ING-27)."""

import asyncio
import json
import time
import urllib.request
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator

from tests.support.env import vid
from tests.support.redisx import HOST, client, load_schema, stream_entries
from tests.support.sim import running_sim, send_tc, tcacks


async def _until(pred: Any, timeout: float) -> float:
    """Seconds until pred() is truthy; fails after `timeout`."""
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "timed out"
        await asyncio.sleep(0.01)
    return time.monotonic() - t0


def _health(port: int) -> dict[str, Any]:
    with urllib.request.urlopen(f"http://{HOST}:{port}/healthz", timeout=3) as r:
        body: dict[str, Any] = json.loads(r.read())
        return body


def _echoed(v: str, cmd_id: UUID) -> bool:
    return any(
        json.loads(f["data"]).get("last_cmd_id") == str(cmd_id)
        for _, f in stream_entries(f"tm:{v}")
    )


async def test_isolate_valve_acked_and_echoed() -> None:
    v = vid()
    async with running_sim(v):
        cmd_id = await asyncio.to_thread(send_tc, v, "isolate_valve", {"valve": "A"})
        took = await _until(lambda: tcacks(v), 1.0)
        assert took <= 0.3  # tcack within 0.3 s
        assert [(a["cmd_id"], a["accepted"], a["code"]) for a in tcacks(v)] == [
            (str(cmd_id), True, "OK")
        ]
        await _until(lambda: _echoed(v, cmd_id), 1.0)  # a tm frame with last_cmd_id == cmd_id
    assert client("admin").xpending(f"tc:{v}", "sim")["pending"] == 0


async def test_schema_invalid_tc_degrades_health() -> None:
    v, u = vid(), uuid4()
    async with running_sim(v) as port:
        assert (await asyncio.to_thread(_health, port))["status"] == "ok"
        bad = json.dumps({"cmd_id": str(u), "name": "vent_cabin"})  # no args/issued_by/…
        client("api").xadd(f"tc:{v}", {"data": bad})
        await _until(lambda: tcacks(v), 1.0)
        [ack] = tcacks(v)
        assert (ack["cmd_id"], ack["accepted"], ack["code"]) == (str(u), False, "SCHEMA")
        assert client("admin").xpending(f"tc:{v}", "sim")["pending"] == 0  # acked
        h = await asyncio.to_thread(_health, port)
        assert h["status"] == "degraded" and h["checks"]["schema_errors"] == 1
        await asyncio.sleep(0.3)
        assert not _echoed(v, u)  # never executed


async def test_tcack_entries_match_schema() -> None:
    v = vid()
    validator = Draft202012Validator(load_schema("tcack"))
    async with running_sim(v):
        await asyncio.to_thread(send_tc, v, "isolate_valve", {"valve": "B"})
        await asyncio.to_thread(send_tc, v, "vent_cabin", {"duration_s": 11})
        client("api").xadd(f"tc:{v}", {"data": json.dumps({"cmd_id": str(uuid4())})})
        await _until(lambda: len(tcacks(v)) == 3, 2.0)
    acks = tcacks(v)
    assert sorted(a["code"] for a in acks) == ["INVALID_ARGS", "OK", "SCHEMA"]
    for _, f in stream_entries(f"tcack:{v}"):
        assert set(f) == {"data"}  # D-SIM-04
        validator.validate(json.loads(f["data"]))


async def test_command_written_while_down_never_runs() -> None:  # P-pf29
    v = vid()
    async with running_sim(v):
        pass  # the sim group exists from now on
    cmd_id = await asyncio.to_thread(send_tc, v, "vent_cabin", {"duration_s": 5})
    async with running_sim(v):  # restart: BUSYGROUP, the group is moved to the stream end
        await asyncio.sleep(0.6)
        later = await asyncio.to_thread(send_tc, v, "isolate_valve", {"valve": "A"})
        await _until(lambda: tcacks(v), 1.0)
        await _until(lambda: _echoed(v, later), 1.0)
    assert [a["cmd_id"] for a in tcacks(v)] == [str(later)]  # no tcack for the old vent
    assert not _echoed(v, cmd_id)  # and never executed
