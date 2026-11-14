import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from tests.support.env import REPO, dotenv, vid

CODE = "from gs.ingest.main import main; main()"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _start(db_password: str | None = None) -> tuple[subprocess.Popen[bytes], int]:
    env, port = dotenv(), _free_port()
    proc_env = {
        **os.environ,
        "REDIS_URL": f"redis://ingest:{env['REDIS_INGEST_PW']}@127.0.0.1:{env['REDIS_HOST_PORT']}/0",
        "DATABASE_URL": f"postgresql://gs_app:{db_password or env['GS_APP_PASSWORD']}"
        f"@127.0.0.1:{env['DB_HOST_PORT']}/gs",
        "CONSUMER_NAME": "ingest-main-test",
        "VEHICLES": vid(),
        "MISSION_DB": str(REPO / "mission"),
        "BIND_HOST": "127.0.0.1",
        "HEALTH_PORT": str(port),
    }
    return subprocess.Popen([sys.executable, "-c", CODE], cwd=REPO, env=proc_env), port


def test_sigterm_exits_zero_and_health_ok() -> None:
    p, port = _start()
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
    p, _ = _start(db_password="wrong")
    try:
        assert p.wait(timeout=10) == 1
    finally:
        if p.poll() is None:
            p.kill()
