"""Redis helpers: clients per ACL user against the compose redis, timing, ACL log check."""

import asyncio
import json
import random
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import redis
import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.retry import Retry as SyncRetry

from gs.simulator.frames import make_frame
from gs.simulator.main import SimSettings, run
from tests.support.env import REPO, dotenv, vid

HOST = "127.0.0.1"


def _creds(user: str) -> tuple[int, str]:
    env = dotenv()
    return int(env["REDIS_HOST_PORT"]), env[f"REDIS_{user.upper()}_PW"]


def client(user: str, decode: bool = True) -> redis.Redis:
    port, pw = _creds(user)
    return redis.Redis(
        host=HOST,
        port=port,
        username=user,
        password=pw,
        decode_responses=decode,
        retry=SyncRetry(NoBackoff(), 0),  # retry OFF like the services; Redis() defaults to 10
    )


def aclient(user: str, decode: bool = True, retry: Retry | None = None) -> redis.asyncio.Redis:
    port, pw = _creds(user)
    extra: dict[str, Any] = {"retry": retry or Retry(NoBackoff(), 0)}  # OFF unless a test asks
    return redis.asyncio.Redis(
        host=HOST,
        port=port,
        username=user,
        password=pw,
        decode_responses=decode,
        **extra,
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


GROUP = "ingest"


def time_base() -> datetime:
    """A minute ago; frame times are base + 100 ms per seq (G14: one base per produce() call)."""
    return datetime.now(UTC) - timedelta(minutes=1)


def frame(v: str, seq: int, base: datetime | None = None) -> str:
    """One valid simulator frame; t_onboard is `base` (default a minute ago) + 100 ms*seq."""
    t = (base or time_base()) + timedelta(milliseconds=100 * seq)
    return make_frame(v, seq, t, random.Random(seq))


def xadd_raw(v: str, raw: str | bytes) -> str:
    return str(client("admin").xadd(f"tm:{v}", {"data": raw}))


def produce(v: str, n: int, start_seq: int = 0) -> None:
    base = time_base()  # once per call: per-frame now() would add XADD latency to the period
    for seq in range(start_seq, start_seq + n):
        xadd_raw(v, frame(v, seq, base))


def pending(v: str) -> int:
    """PEL size of group ingest; -1 while the group does not exist yet."""
    try:
        return int(client("admin").xpending(f"tm:{v}", GROUP)["pending"])
    except redis.ResponseError:
        return -1


def drained(v: str) -> bool:
    """Group exists, every entry was delivered (lag 0) and acknowledged."""
    groups: Any = client("admin").xinfo_groups(f"tm:{v}")
    return any(g["name"] == GROUP and g["lag"] == 0 and g["pending"] == 0 for g in groups)


def read_without_ack(v: str, consumer: str) -> list[tuple[str, dict[bytes, bytes]]]:
    r = client("ingest", decode=False)
    try:
        r.xgroup_create(f"tm:{v}", GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        assert "BUSYGROUP" in str(e)
    res: Any = r.xreadgroup(GROUP, consumer, {f"tm:{v}": ">"}, count=100)
    return [(i.decode(), f) for i, f in res[0][1]] if res else []


def ui_tm(v: str) -> list[str]:
    """Raw `data` of every ui:events entry for vehicle v."""
    entries: Any = client("admin").xrange("ui:events")
    return [f["data"] for _, f in entries if json.loads(f["data"]).get("vehicle") == v]


def ui_tm_count(v: str) -> int:
    return len(ui_tm(v))


def dlq_entries(v: str) -> list[str]:
    """Raw `data` of every tm:dlq record that came from stream tm:<v>."""
    entries: Any = client("admin").xrange("tm:dlq")
    return [f["data"] for _, f in entries if json.loads(f["data"]).get("stream") == f"tm:{v}"]


def dlq_since(stream: str, since: datetime) -> list[str]:
    """Raw `data` of tm:dlq records for `stream` added at or after `since` (by entry id)."""
    ms = int(since.timestamp() * 1000)
    entries: Any = client("admin").xrange("tm:dlq", min=str(ms))
    return [f["data"] for _, f in entries if json.loads(f["data"]).get("stream") == stream]


def pending_of(v: str, consumer: str) -> int:
    """Entries in group ingest's PEL owned by `consumer` on tm:<v>; 0 while there is no group."""
    try:
        rows: Any = client("admin").xpending_range(f"tm:{v}", GROUP, "-", "+", 10_000, consumer)
    except redis.ResponseError as e:
        if "NOGROUP" not in str(e):
            raise
        return 0
    return len(rows)


def dlq_entry_for(entry_id: str) -> str:
    entries: Any = client("admin").xrange("tm:dlq")
    (data,) = [f["data"] for _, f in entries if json.loads(f["data"]).get("entry_id") == entry_id]
    return str(data)


def redis_time_offset(r: redis.Redis) -> float:
    """Redis server clock minus the host clock (s), host time taken at the midpoint of TIME."""
    t0 = time.time()
    sec, usec = r.time()
    t1 = time.time()
    return sec + usec / 1e6 - (t0 + t1) / 2
