import json
import subprocess
import time
import urllib.request
from collections.abc import Iterator

import pytest

from tests.support.env import dotenv, sh
from tests.support.redisx import client
from tests.support.s3 import inspect_health


@pytest.fixture(scope="module")
def up() -> Iterator[None]:
    sh("docker", "compose", "config", "-q")  # R16
    sh("docker", "compose", "up", "-d", "--build", "--wait", "redis", "simulator")
    yield
    sh("docker", "compose", "stop", "simulator")


@pytest.mark.usefixtures("up")
def test_healthy_and_health_endpoint_on_env_port() -> None:
    assert inspect_health("simulator") == "healthy"
    url = f"http://127.0.0.1:{dotenv()['SIM_HOST_PORT']}/healthz"
    body = json.loads(urllib.request.urlopen(url, timeout=5).read())
    assert body["status"] in {"ok", "degraded"} and "buffered" in body["checks"]


@pytest.mark.usefixtures("up")
def test_stream_grows() -> None:
    r = client("admin")

    def newest() -> tuple[int, int]:  # XLEN stalls once the ~MAXLEN trim cap is reached
        rows = r.xrevrange("tm:NYX-TA1", "+", "-", count=1)
        assert rows
        ms, seq = str(rows[0][0]).split("-")
        return int(ms), int(seq)

    a = newest()
    time.sleep(1)
    assert newest() > a


@pytest.mark.usefixtures("up")
def test_container_is_not_root() -> None:  # R24
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "simulator", "id", "-u"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out != "0"
