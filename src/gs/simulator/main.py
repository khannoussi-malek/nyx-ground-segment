"""gs-sim: two-vehicle 10 Hz telemetry simulator writing tm:<vehicle> streams."""

import asyncio
import os
import random
import signal
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.config import ConfigError, require, require_int, vehicle_list
from gs.common.health import State, serve_health
from gs.common.logs import setup_logging
from gs.common.missiondb import load_dir
from gs.simulator.buffer import FrameBuffer, send_loop
from gs.simulator.commands import CommandBox, tc_loop
from gs.simulator.faults import FaultBoard, fault_routes
from gs.simulator.frames import tick_loop

DEFAULT_RATE_HZ, MAX_RATE_HZ = 10, 100  # D-PERF-05: run P drives 100 Hz
MAXLEN = 108_000  # 3 h at 10 Hz: the 2 h soak window + margin; ~47 MB per vehicle (D-ING-06)
DEGRADED_BACKLOG = 50  # frames queued: Redis unreachable for more than ~1.5 s with 2 vehicles


def _rate_hz() -> int:
    """Optional SIM_RATE_HZ, 1..100, default 10."""
    if not os.environ.get("SIM_RATE_HZ"):
        return DEFAULT_RATE_HZ
    rate = require_int("SIM_RATE_HZ")
    if not 1 <= rate <= MAX_RATE_HZ:
        raise ConfigError(f"SIM_RATE_HZ must be 1..{MAX_RATE_HZ}, got {rate}")
    return rate


@dataclass(frozen=True)
class SimSettings:
    redis_url: str
    vehicles: tuple[str, ...]
    seed: str
    bind_host: str
    health_port: int
    mission_db: Path
    rate_hz: int = DEFAULT_RATE_HZ

    @classmethod
    def from_env(cls) -> SimSettings:
        return cls(
            redis_url=require("REDIS_URL"),
            vehicles=vehicle_list(require("SIM_VEHICLES"), "SIM_VEHICLES"),
            seed=require("SIM_SEED"),
            bind_host=require("BIND_HOST"),
            health_port=require_int("HEALTH_PORT"),
            mission_db=Path(require("MISSION_DB")),
            rate_hz=_rate_hz(),
        )


async def xadd_tm(r: redis.asyncio.Redis, stream: str, data: str, maxlen: int = MAXLEN) -> object:
    return await r.execute_command(  # type: ignore[no-untyped-call]
        "XADD", stream, "ACKED", "MAXLEN", "~", maxlen, "*", "data", data
    )


def sim_status(
    buf: FrameBuffer, boxes: Iterable[CommandBox] = ()
) -> tuple[State, dict[str, object]]:
    """Degraded on backlog, overflow, or any schema-rejected tc entry until restart (D-ING-27)."""
    schema_errors = sum(b.schema_errors for b in boxes)
    bad = len(buf) >= DEGRADED_BACKLOG or buf.overflow or schema_errors
    state: State = "degraded" if bad else "ok"
    return state, {"buffered": len(buf), "overflow": buf.overflow, "schema_errors": schema_errors}


async def run(s: SimSettings, stop: asyncio.Event) -> None:
    buf = FrameBuffer()
    # loaded once: a bound changed in commands.yaml reaches the simulator at its next restart
    commands = load_dir(s.mission_db).commands
    boxes = {v: CommandBox(commands) for v in s.vehicles}
    # redis-py 8.1: Redis(...) defaults to 10 retries, from_url(...) to 0; keep it 0 explicitly
    r = redis.asyncio.Redis.from_url(
        s.redis_url,
        socket_timeout=2,
        # retry OFF: send_loop owns retries (D-SIM-05). At-least-once: a timed-out XADD may
        # have landed and is re-sent; ingest's idempotent key dedups it (G30)
        retry=Retry(NoBackoff(), 0),
    )

    board = FaultBoard()
    routes = fault_routes(board, s.vehicles, time.monotonic)
    server = await serve_health(
        s.bind_host, s.health_port, lambda: sim_status(buf, boxes.values()), routes
    )
    try:
        async with asyncio.TaskGroup() as tg:
            for v in s.vehicles:
                rng = random.Random(f"{s.seed}:{v}")  # noqa: S311 simulated telemetry, not crypto
                tg.create_task(
                    tick_loop(v, buf, rng, stop, faults=board, box=boxes[v], period_s=1 / s.rate_hz)
                )
                tg.create_task(tc_loop(r, v, boxes[v], stop))
            tg.create_task(send_loop(buf, lambda st, d: xadd_tm(r, st, d), stop))
    finally:
        server.close()
        await server.wait_closed()
        await r.aclose()


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(SimSettings.from_env(), stop)


def main() -> None:
    setup_logging("gs-sim")
    asyncio.run(_main())
