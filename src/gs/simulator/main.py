"""gs-sim: two-vehicle 10 Hz telemetry simulator writing tm:<vehicle> streams."""

import asyncio
import logging
import random
import signal
from dataclasses import dataclass

import redis.asyncio

from gs.common.config import require
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
            vehicles=tuple(v for v in require("SIM_VEHICLES").split(",") if v),
            seed=require("SIM_SEED"),
            bind_host=require("BIND_HOST"),
            health_port=int(require("HEALTH_PORT")),
        )


async def xadd_tm(r: redis.asyncio.Redis, stream: str, data: str) -> object:
    return await r.execute_command(  # type: ignore[no-untyped-call]
        "XADD", stream, "ACKED", "MAXLEN", "~", MAXLEN, "*", "data", data
    )


async def run(s: SimSettings, stop: asyncio.Event) -> None:
    buf = FrameBuffer()
    r = redis.asyncio.Redis.from_url(s.redis_url, socket_timeout=2)

    def status() -> tuple[State, dict[str, object]]:
        state: State = "degraded" if len(buf) >= DEGRADED_BACKLOG or buf.overflow else "ok"
        return state, {"buffered": len(buf), "overflow": buf.overflow}

    server = await serve_health(s.bind_host, s.health_port, status)
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
