"""Redis helpers: clients per ACL user against the compose redis, timing, ACL log check."""

import subprocess
import sys
import time

import redis.asyncio

import redis
from tests.support.env import REPO, dotenv

HOST = "127.0.0.1"


def _creds(user: str) -> tuple[int, str]:
    env = dotenv()
    return int(env["REDIS_HOST_PORT"]), env[f"REDIS_{user.upper()}_PW"]


def client(user: str) -> redis.Redis:
    port, pw = _creds(user)
    return redis.Redis(host=HOST, port=port, username=user, password=pw, decode_responses=True)


def aclient(user: str) -> redis.asyncio.Redis:
    port, pw = _creds(user)
    return redis.asyncio.Redis(
        host=HOST, port=port, username=user, password=pw, decode_responses=True
    )


def acl_log_reset() -> None:
    client("admin").execute_command("ACL", "LOG", "RESET")  # type: ignore[no-untyped-call]


def timed_serial_xadds(r: redis.Redis, stream: str, n: int, payload: str) -> list[float]:
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        r.xadd(stream, {"data": payload})
        lat.append(time.perf_counter() - t0)
    return lat


def run_check_acl_log() -> int:
    script = REPO / "scripts" / "check_acl_log.py"
    return subprocess.run([sys.executable, str(script)], cwd=REPO, capture_output=True).returncode
