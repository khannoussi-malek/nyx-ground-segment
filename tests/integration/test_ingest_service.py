import subprocess
import time
from collections.abc import Iterator

import psycopg
import pytest

from tests.support.env import sh
from tests.support.pg import app_dsn
from tests.support.s3 import inspect_health


def _count() -> int:
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute("select count(*) from telemetry where vehicle='NYX-TA1'").fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture(scope="module")
def up() -> Iterator[None]:
    sh("docker", "compose", "config", "-q")  # R16
    sh("docker", "compose", "up", "-d", "--build", "--wait", "redis", "db", "simulator", "ingest-1")
    yield
    sh("docker", "compose", "stop", "ingest-1", "simulator")


@pytest.mark.usefixtures("up")
def test_healthy_and_stores_telemetry() -> None:
    assert inspect_health("ingest-1") == "healthy"
    a = _count()
    time.sleep(2)
    assert _count() >= a + 10  # ~10 Hz


@pytest.mark.usefixtures("up")
def test_container_is_not_root() -> None:  # R24
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "ingest-1", "id", "-u"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out != "0"
