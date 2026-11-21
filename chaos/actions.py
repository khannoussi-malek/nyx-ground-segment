"""The infrastructure actions (design 11.5), each undone in a `finally`, and the I5 check:
after an action ends, /healthz is ok and both vehicles are fresh within 60 s, with no manual
step."""

import json
import subprocess
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg

from chaos.schedule import INGEST, VEHICLES, Action
from tests.support.compose import api_url, compose, compose_ps, stack_problems
from tests.support.ingest import set_read_only
from tests.support.pg import newest_t

RECOVERY_S = 60.0
FRESH_S = 5.0  # newest stored frame at most this old, both vehicles


def api_ok() -> bool:
    try:
        return bool(urllib.request.urlopen(f"{api_url()}/healthz", timeout=3).status == 200)
    except OSError:  # URLError, HTTPError (503), timeouts
        return False


def recovered() -> bool:
    if stack_problems(api_ok(), compose_ps()):
        return False
    now = datetime.now(UTC)
    newest = [newest_t(v) for v in VEHICLES]
    return all(t is not None and (now - t).total_seconds() < FRESH_S for t in newest)


def wait_recovered(timeout: float = RECOVERY_S) -> float | None:
    """Seconds until recovered, None if it did not within `timeout`."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if recovered():
                return time.monotonic() - t0
        except OSError, psycopg.Error, subprocess.CalledProcessError:
            pass  # a probe that fails is "not recovered yet"
        time.sleep(1)
    return None


def _networks(service: str, suffix: str) -> tuple[str, str]:
    """(container id, this project's network ending in `suffix`) of `service`."""
    cid = compose("ps", "-q", service).stdout.strip()
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", cid],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    (net,) = [n for n in json.loads(out) if n.endswith("_" + suffix)]
    return cid, net


def _docker(*args: str) -> None:
    subprocess.run(["docker", *args], capture_output=True, text=True, check=True)


def run_action(a: Action, sleep: Callable[[float], None] = time.sleep) -> None:
    """Blocks for the action and its undo. Worker kill and the two partitions name an ingest
    worker; the others act on the one service."""
    t = a.target
    assert t in INGEST or a.kind not in (
        "worker_kill",
        "worker_hang",
        "partition_redis",
        "partition_db",
    )
    match a.kind:
        case "worker_kill":
            try:
                compose("kill", t)
                sleep(a.duration_s)
            finally:
                compose("up", "-d", t)
        case "worker_hang":
            try:
                compose("pause", t)
                sleep(a.duration_s)
            finally:
                compose("unpause", t, check=False)
        case "api_restart":
            compose("restart", "api")
        case "sim_restart":
            compose("restart", "simulator")
        case "broker_restart":
            try:
                compose("stop", "redis")
                sleep(a.duration_s)
            finally:
                compose("start", "redis")
        case "partition_redis" | "partition_db":
            cid, net = _networks(t, "redisnet" if a.kind == "partition_redis" else "dbnet")
            try:
                _docker("network", "disconnect", net, cid)
                sleep(a.duration_s)
            finally:
                _docker("network", "connect", net, cid)
        case "pg_readonly":
            try:
                set_read_only(True)
                sleep(a.duration_s)
            finally:
                set_read_only(False)
        case _:
            raise ValueError(f"unknown action {a.kind}")
