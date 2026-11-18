"""Redis helpers: clients per ACL user against the compose redis, timing, ACL log check."""

import asyncio
import json
import random
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import redis
import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.retry import Retry as SyncRetry

from gs.simulator.frames import build_frame, make_frame
from gs.simulator.main import SimSettings, run
from gs.simulator.nominals import NOMINALS
from tests.support.env import REPO, dotenv, now, vid
from tests.support.mission import MISSION_DIR

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


def xadd_bound_met(batches: list[list[float]], p99_max: float = 0.010) -> bool:
    """D-ING-07: true when some batch of serial XADD latencies (s) has p99 < p99_max."""
    return any(sorted(b)[int(len(b) * 0.99) - 1] < p99_max for b in batches)


def run_check_acl_log() -> int:
    script = REPO / "scripts" / "check_acl_log.py"
    return subprocess.run([sys.executable, str(script)], cwd=REPO, capture_output=True).returncode


async def run_sim(vehicles: list[str], seconds: float, user: str = "sim") -> None:
    """Run the simulator in-process against the compose redis for `seconds`."""
    env = dotenv()
    url = f"redis://{user}:{env[f'REDIS_{user.upper()}_PW']}@{HOST}:{env['REDIS_HOST_PORT']}/0"
    settings = SimSettings(url, tuple(vehicles), "1", HOST, 0, MISSION_DIR)
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


NOMINAL = {p: nom for p, (nom, _) in NOMINALS.items()}
Values = Callable[[float], Mapping[str, float]]


def produce_values(
    v: str,
    values: Mapping[str, Sequence[float]],
    last_cmd_id: UUID | None = None,
    base: datetime | None = None,
    start_seq: int = 0,
) -> None:
    """XADD one frame per index of the `values` series (equal lengths): every other parameter
    at its nominal, 100 ms apart from `base` (default a minute ago), `last_cmd_id` on each."""
    lengths = {len(x) for x in values.values()}
    assert len(lengths) == 1 and 0 not in lengths, (
        f"series must be non-empty and equal length: {lengths}"
    )
    (n,) = lengths
    base = base or time_base()
    for i in range(n):
        seq = start_seq + i
        vals = NOMINAL | {p: x[i] for p, x in values.items()}
        xadd_raw(
            v, build_frame(v, seq, base + timedelta(milliseconds=100 * seq), vals, last_cmd_id)
        )


@asynccontextmanager
async def stream_values(v: str, values: Values) -> AsyncIterator[None]:
    """Live 10 Hz frames from now on: t_onboard = start + 100 ms x seq, every parameter at its
    nominal, overridden by `values(seconds since start)`."""
    stop, t0, w0 = asyncio.Event(), now(), time.monotonic()

    async def run_frames() -> None:
        seq = 0
        while not stop.is_set():
            t = seq / 10
            xadd_raw(v, build_frame(v, seq, t0 + timedelta(seconds=t), NOMINAL | dict(values(t))))
            seq += 1
            await asyncio.sleep(max(0.0, w0 + seq / 10 - time.monotonic()))

    task = asyncio.create_task(run_frames())
    try:
        yield
    finally:
        stop.set()
        await task


def xadd_data(stream: str, data: str, user: str = "admin") -> str:
    """XADD {"data": data} to `stream` as ACL user `user`; returns the entry id."""
    return str(client(user).xadd(stream, {"data": data}))


def alarm_msgs(v: str) -> list[dict[str, Any]]:
    """Every ui:events alarm message for vehicle v, parsed."""
    msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
    return [m for m in msgs if m.get("kind") == "alarm" and m["alarm"]["vehicle"] == v]


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
    """Raw `data` of every ui:events tm entry for vehicle v (resync also has a top-level
    vehicle, P-pf31)."""
    entries: Any = client("admin").xrange("ui:events")
    msgs = [(f["data"], json.loads(f["data"])) for _, f in entries]
    return [d for d, m in msgs if m.get("kind") == "tm" and m.get("vehicle") == v]


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


def stream_entries(stream: str) -> list[tuple[str, dict[str, str]]]:
    """Every entry of `stream`, read back through admin."""
    entries: Any = client("admin").xrange(stream)
    return list(entries)


def load_schema(name: str) -> dict[str, Any]:
    """interfaces/schemas/<name>.schema.json, e.g. load_schema("ui-event")."""
    path = REPO / "interfaces" / "schemas" / f"{name}.schema.json"
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return schema


def validate_ui_event(msg: str | Mapping[str, Any]) -> None:
    """Raise if a ui:events payload (raw JSON or parsed) does not match the ui-event schema."""
    from jsonschema import Draft202012Validator

    Draft202012Validator(load_schema("ui-event")).validate(
        json.loads(msg) if isinstance(msg, str) else msg
    )
