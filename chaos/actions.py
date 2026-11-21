"""The infrastructure actions (design 11.5), each undone in a `finally`, and the I5 check:
after an action ends, /healthz is ok and both vehicles are fresh within 60 s, with no manual
step."""

import json
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from chaos.schedule import INGEST, VEHICLES, Action
from tests.support.compose import ONE_SHOTS, api_url, compose, compose_ps, stack_problems
from tests.support.ingest import set_read_only
from tests.support.pg import admin_dsn, newest_t

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
    cid = compose("ps", "-aq", service).stdout.strip()
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", cid],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    (net,) = [n for n in json.loads(out) if n.endswith("_" + suffix)]
    return cid, net


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


# the soak's stack runs with these (make chaos); a recreate here must not use .env's values
CHAOS_PINS = {"STEAL_IDLE_MS": "30000", "RECLAIM_IDLE_MS": "30000"}


def _up(*services: str) -> None:
    compose("up", "-d", *services, env=CHAOS_PINS)


def _read_only_on() -> bool:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        row = c.execute("SHOW default_transaction_read_only").fetchone()
    return row is not None and row[0] == "on"


@dataclass
class Restore:
    repairs: list[str] = field(default_factory=list)  # what it had to fix
    failures: list[str] = field(default_factory=list)  # what it could not

    def lines(self) -> list[str]:
        return [*self.repairs, *(f"FAILED {f}" for f in self.failures)]


def restore_stack() -> Restore:
    """Back to a known good state, whatever an earlier run left: every service up, ingest
    unpaused, ingest on both networks, Postgres writable. Every step runs; reports each repair
    made and each step that failed. Used at soak start (a crashed run must not poison the next)
    and when it ends."""
    r = Restore()

    def step(name: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except (subprocess.CalledProcessError, psycopg.Error, OSError, ValueError) as e:
            r.failures.append(f"{name}: {type(e).__name__}: {e}")

    def up() -> None:
        ps = compose_ps()
        for svc, row in sorted(ps.items()):
            if svc not in ONE_SHOTS and row.get("State") != "running":
                r.repairs.append(f"{svc} was {row.get('State')}: started")
        _up()

    def unpause() -> None:
        for svc, row in sorted(compose_ps().items()):
            if svc in INGEST and row.get("State") == "paused":
                compose("unpause", svc)
                r.repairs.append(f"{svc} was paused: unpaused")

    def reconnect() -> None:
        nets = {"redis": _networks("redis", "redisnet")[1], "db": _networks("db", "dbnet")[1]}
        for t in INGEST:
            cid = compose("ps", "-aq", t).stdout.strip()
            for svc, net in nets.items():
                p = _docker("network", "connect", net, cid, check=False)
                if p.returncode == 0:
                    r.repairs.append(f"network reconnect {t} from {svc}")
                elif "already exists" not in p.stderr:  # connected already: fine
                    raise subprocess.CalledProcessError(p.returncode, "docker network connect")

    def read_only() -> None:
        if _read_only_on():
            r.repairs.append("read-only was on: reset")
        set_read_only(False)

    step("up", up)
    step("unpause", unpause)
    step("networks", reconnect)
    step("read-only reset", read_only)
    return r


def run_action(a: Action, sleep: Callable[[float], object] = time.sleep) -> None:
    """Blocks for the action and its undo; `sleep` is the soak's stop event's wait, so a stop ends
    the action early and the undo (the `finally`) still runs. Worker kill and the two partitions
    name an ingest worker; the others act on the one service."""
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
                _up(t)
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
                _docker("network", "connect", net, cid, check=False)  # recreated meanwhile: ok
        case "pg_readonly":
            try:
                set_read_only(True)
                sleep(a.duration_s)
            finally:
                set_read_only(False)
        case _:
            raise ValueError(f"unknown action {a.kind}")
