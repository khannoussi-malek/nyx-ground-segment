"""ENG-05: every service logs one JSON object per line, times in UTC with Z (D-ENG-18), and the
frame path names the vehicle and the seq."""

import json
import time
import urllib.request
from datetime import datetime
from typing import Any

from gs.common.time import iso_z
from tests.support.compose import api_url, compose
from tests.support.env import now, wait_until
from tests.support.ingest import INGEST
from tests.support.redisx import client
from tests.support.sim import post_fault

SERVICES = ("simulator", "ingest-1", "ingest-2", "api", "migrate")


def lines(svc: str, since: datetime) -> list[dict[str, Any]]:
    out = compose("logs", "--no-log-prefix", "--since", iso_z(since), svc).stdout
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]  # a non-JSON line raises


def test_every_service_logs_json() -> None:
    # The simulator and the ingest workers are silent while healthy. Dropping the simulator's
    # Redis connections makes its blocking tc read warn. The ingest clients replace a
    # server-closed connection before use (P-pf47), so dropping theirs may log nothing:
    # a seq_jump on each vehicle makes its owner log the SEQ_JUMP event instead (repeated,
    # because a worker recreated by an earlier test gets its home vehicle back within ~5 s).
    # The api logs each request (uvicorn.access) and the exited migrate container logs again when
    # started, so both get one line of ours; every service is then read since the test start.
    started = now()  # earlier tests leave lines (a deliberate watchdog dump); read only ours
    urllib.request.urlopen(f"{api_url()}/healthz", timeout=5).close()
    compose("start", "migrate")
    client("admin").client_kill_filter(user="sim")
    last_poke = 0.0

    def all_logged() -> bool:
        nonlocal last_poke
        if time.monotonic() - last_poke > 2.0:
            for vehicle in ("NYX-TA1", "NYX-TA2"):
                post_fault(vehicle, "seq_jump")
            last_poke = time.monotonic()
        return all(lines(s, started) for s in SERVICES)

    wait_until(all_logged, 20)
    for svc in SERVICES:
        got = lines(svc, started)
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
