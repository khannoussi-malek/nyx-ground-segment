import signal
import time
import urllib.request

import pytest

import gs.ingest.main as main_mod
from gs.ingest.errors import OperationalDbError
from tests.support.ingest import start_ingest


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
    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 1 and "25006: read-only" in caplog.text
