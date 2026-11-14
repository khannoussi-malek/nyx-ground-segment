"""gs-sim: two-vehicle 10 Hz telemetry simulator writing tm:<vehicle> streams."""

import asyncio
import logging
import random
import signal
from dataclasses import dataclass

import redis.asyncio
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.config import require, require_int, vehicle_list
from gs.common.health import State, serve_health
from gs.simulator.buffer import FrameBuffer, send_loop
from gs.simulator.frames import tick_loop

MAXLEN = 360_000  # ~10 h at 10 Hz per vehicle (D-ING-06)
DEGRADED_BACKLOG = 50  # frames queued: Redis unreachable for more than ~1.5 s with 2 vehicles


@dataclass(frozen=True)
class SimSettings:
    redis_url: str
    vehicles: tuple[str, ...]
    seed: str
    bind_host: str
    health_port: int

    @classmethod
    def from_env(cls) -> SimSettings:
        return cls(
            redis_url=require("REDIS_URL"),
            vehicles=vehicle_list(require("SIM_VEHICLES"), "SIM_VEHICLES"),
            seed=require("SIM_SEED"),
            bind_host=require("BIND_HOST"),
            health_port=require_int("HEALTH_PORT"),
        )


async def xadd_tm(r: redis.asyncio.Redis, stream: str, data: str) -> object:
    return await r.execute_command(  # type: ignore[no-untyped-call]
        "XADD", stream, "ACKED", "MAXLEN", "~", MAXLEN, "*", "data", data
    )


def sim_status(buf: FrameBuffer) -> tuple[State, dict[str, object]]:
    state: State = "degraded" if len(buf) >= DEGRADED_BACKLOG or buf.overflow else "ok"
    return state, {"buffered": len(buf), "overflow": buf.overflow}


async def run(s: SimSettings, stop: asyncio.Event) -> None:
    buf = FrameBuffer()
    # redis-py 8.1: Redis(...) defaults to 10 retries, from_url(...) to 0; keep it 0 explicitly
    r = redis.asyncio.Redis.from_url(
        s.redis_url,
        socket_timeout=2,
        # retry OFF: send_loop owns retries, so an XADD is never silently re-sent (D-SIM-05)
        retry=Retry(NoBackoff(), 0),
    )

    server = await serve_health(s.bind_host, s.health_port, lambda: sim_status(buf))
    try:
        async with asyncio.TaskGroup() as tg:
            for v in s.vehicles:
                tg.create_task(tick_loop(v, buf, random.Random(f"{s.seed}:{v}"), stop))
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
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
