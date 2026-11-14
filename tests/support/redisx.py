"""Redis helpers: clients per ACL user against the compose redis, timing, ACL log check."""

import asyncio
import subprocess
import sys
import time
from typing import Any

import redis.asyncio

import redis
from gs.simulator.main import SimSettings, run
from tests.support.env import REPO, dotenv, vid

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


async def run_sim(vehicles: list[str], seconds: float, user: str = "sim") -> None:
    """Run the simulator in-process against the compose redis for `seconds`."""
    env = dotenv()
    url = f"redis://{user}:{env[f'REDIS_{user.upper()}_PW']}@{HOST}:{env['REDIS_HOST_PORT']}/0"
    settings = SimSettings(url, tuple(vehicles), "1", HOST, 0)
    stop = asyncio.Event()
    task = asyncio.create_task(run(settings, stop))
    await asyncio.sleep(seconds)
    stop.set()
    await task


def recorded_frames(per_vehicle: int = 20) -> list[str]:
    """Raw `data` fields of a fresh simulator burst, read back through admin."""
    v = vid()
    asyncio.run(run_sim([v], seconds=1.0))
    entries: Any = client("admin").xrange(f"tm:{v}", count=per_vehicle)
    return [f["data"] for _, f in entries]
