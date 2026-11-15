import functools
import json
import signal
import time
import urllib.request
from pathlib import Path

import pytest

import gs.ingest.main as main_mod
from gs.ingest.errors import OperationalDbError
from gs.ingest.events import rejected_key
from tests.support.env import vid, wait_until
from tests.support.ingest import start_ingest
from tests.support.missionfiles import edit, mission_copy, set_formula
from tests.support.pg import rows
from tests.support.redisx import stream_entries, validate_ui_event


def test_sigterm_exits_zero_and_health_ok() -> None:
    p, port = start_ingest()
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
                break
            except OSError:
                assert p.poll() is None and time.monotonic() < deadline
                time.sleep(0.1)
        assert b'"status": "ok"' in body
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()


def test_task_error_exits_nonzero() -> None:  # crash-only: a DB failure ends the process
    p, _ = start_ingest(db_password="wrong")
    try:
        assert p.wait(timeout=10) == 1
    finally:
        if p.poll() is None:
            p.kill()


def test_operational_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:  # D-ING-24: a vehicle task's OperationalDbError ends the process with 1
    async def failing() -> None:
        raise ExceptionGroup("task group", [OperationalDbError("25006: read-only")])

    monkeypatch.setattr(main_mod, "_main", failing)
    monkeypatch.setattr(main_mod, "setup_logging", lambda service: None)  # keep caplog's handler
    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 1 and "25006: read-only" in caplog.text


def test_ingest_health_reports_used_memory() -> None:
    p, port = start_ingest()
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
                body = json.loads(raw)
                checks = body["checks"]  # owned fills once the lock is granted: wait for both
                if checks.get("redis_used_memory") and checks.get("owned"):
                    break
            except OSError:
                pass
            assert p.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        assert body["status"] == "ok" and body["checks"]["redis_used_memory"] > 0
        assert len(body["checks"]["owned"]) == 1  # its one home vehicle
    finally:
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0


def test_main_starts_on_a_fallback_set_and_watches_mission(tmp_path: Path) -> None:  # D-ALM-12
    m, token = mission_copy(tmp_path), vid()  # set keys are content hashes: make them new
    edit(m / "commands.yaml", "Close the cabin isolation valve", f"Close valve {token}")
    with (m / "parameters.yaml").open("a") as f:  # no telemetry column: start falls back
        f.write("  cabin_humidity_pct: {unit: pct, type: float}\n")
    rejected = rejected_key(set_formula(m))
    p, _ = start_ingest(MISSION_DB=str(m))
    try:
        wait_until(lambda: bool(rows("select 1 from events where key=%s", rejected)), 10)
        edit(m / "parameters.yaml", "  cabin_humidity_pct: {unit: pct, type: float}\n", "")
        edit(m / "commands.yaml", token, f"{token} (main)")
        new = set_formula(m)  # the watcher applies (and stores) it within two polls
        wait_until(lambda: bool(rows("select 1 from limits_versions where set_sha256=%s", new)), 5)
        assert p.poll() is None
    finally:
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0


def _links(key: str) -> list[dict[str, object]]:
    msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
    return [m for m in msgs if m["kind"] == "link" and m["event"]["key"] == key]


def _healthy(port: int) -> bool:  # serves health only after the start set and its events
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
    except OSError:
        return False
    return True


def test_start_rejection_publishes_link_once(tmp_path: Path) -> None:  # P-pf9, Task 27b
    m, token = mission_copy(tmp_path), vid()
    edit(m / "commands.yaml", "Close the cabin isolation valve", f"Close valve {token}")
    with (m / "parameters.yaml").open("a") as f:  # no telemetry column: start falls back
        f.write("  cabin_humidity_pct: {unit: pct, type: float}\n")
    rejected = rejected_key(set_formula(m))
    for _ in range(2):  # the second start finds the row: its insert lost, so no second link
        p, port = start_ingest(MISSION_DB=str(m))
        try:
            wait_until(functools.partial(_healthy, port), 10)
            wait_until(lambda: bool(_links(rejected)), 3)
        finally:
            p.send_signal(signal.SIGTERM)
            assert p.wait(timeout=5) == 0
    [msg] = _links(rejected)
    validate_ui_event(msg)
    assert msg["event"]["kind"] == "LIMITS_REJECTED"  # type: ignore[index]
