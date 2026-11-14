import signal
import socket
import subprocess
import sys
import time
import urllib.request

from tests.support.env import REPO, dotenv, vid

CODE = "from gs.simulator.main import main; main()"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def test_sigterm_exits_zero() -> None:
    env = dotenv()
    port = _free_port()
    proc_env = {
        "REDIS_URL": f"redis://sim:{env['REDIS_SIM_PW']}@127.0.0.1:{env['REDIS_HOST_PORT']}/0",
        "SIM_VEHICLES": vid(),
        "SIM_SEED": "1",
        "BIND_HOST": "127.0.0.1",
        "HEALTH_PORT": str(port),
    }
    p = subprocess.Popen([sys.executable, "-c", CODE], cwd=REPO, env=proc_env)
    try:
        deadline = time.monotonic() + 10
        while True:  # health answering means the loop and signal handlers are up
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                break
            except OSError:
                assert p.poll() is None and time.monotonic() < deadline
                time.sleep(0.1)
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()
