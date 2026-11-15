"""ENG-05: every service logs one JSON object per line, times in UTC with Z (D-ENG-18), and the
frame path names the vehicle and the seq."""

import json
from datetime import datetime
from typing import Any

from gs.common.time import iso_z
from tests.support.compose import compose
from tests.support.env import now, wait_until
from tests.support.ingest import INGEST
from tests.support.redisx import client
from tests.support.sim import post_fault

SERVICES = ("simulator", "ingest-1", "ingest-2", "api", "migrate")
TAIL = 50


def lines(svc: str, since: datetime | None = None) -> list[dict[str, Any]]:
    args = [
        "logs",
        "--no-log-prefix",
        *(["--since", iso_z(since)] if since else ["--tail", str(TAIL)]),
    ]
    out = compose(*args, svc).stdout
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]  # a non-JSON line raises


def test_every_service_logs_json() -> None:
    # the simulator, and an ingest worker that an earlier test recreated, are silent while
    # healthy: dropping their Redis connections makes them warn; they reconnect by themselves
    for user in ("sim", "ingest"):
        client("admin").client_kill_filter(user=user)
    wait_until(lambda: all(lines(s) for s in ("simulator", *INGEST)), 15)
    for svc in SERVICES:
        got = lines(svc)
        assert got, f"{svc} logged nothing"
        for rec in got:
            assert isinstance(rec["t"], str) and rec["t"].endswith("Z"), (svc, rec)
            datetime.fromisoformat(rec["t"])  # a real timestamp
            assert {"level", "service", "logger", "msg"} <= set(rec), (svc, rec)


def test_frame_path_lines_carry_vehicle_and_seq() -> None:
    t0 = now()
    assert post_fault("NYX-TA1", "seq_jump") == 204

    def jump_lines() -> list[dict[str, Any]]:
        found = [r for svc in INGEST for r in lines(svc, t0)]
        return [r for r in found if r["msg"].startswith("SEQ_JUMP ")]

    wait_until(lambda: bool(jump_lines()), 10)
    for rec in jump_lines():  # a redelivered batch may log its event again
        assert rec["vehicle"] == "NYX-TA1"
        assert isinstance(rec["seq"], int) and not isinstance(rec["seq"], bool)
