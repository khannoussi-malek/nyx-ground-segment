"""G29 / D-SIM-01+05: the tick loop never waits for Redis. With a sender that refuses or hangs
(how a stopped / paused broker looks to the client) frames keep their 0.1 s t_onboard grid."""

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
import redis.exceptions

from gs.simulator.buffer import FrameBuffer, send_loop
from gs.simulator.frames import PERIOD_S, tick_loop

RUN_S = 1.5
MAX_GAP_S = 3 * PERIOD_S  # a tick that waited on the sender would leave a hole of 2 s or more


async def refuse(stream: str, data: str) -> object:
    raise redis.exceptions.ConnectionError("broker down")


async def hang(stream: str, data: str) -> object:
    await asyncio.sleep(3600)  # a paused broker: the socket never answers
    return None


@pytest.mark.parametrize("xadd", [refuse, hang], ids=["refused", "hanging"])
async def test_ticks_keep_the_grid_while_the_sender_cannot_send(
    xadd: Callable[[str, str], Awaitable[object]],
) -> None:
    buf, stop = FrameBuffer(), asyncio.Event()
    ticker = asyncio.create_task(tick_loop("NYX-TA1", buf, random.Random(1), stop))
    sender = asyncio.create_task(send_loop(buf, xadd, stop))
    await asyncio.sleep(RUN_S)
    stop.set()
    sender.cancel()  # a hanging xadd never returns on its own
    await ticker
    ts = [datetime.fromisoformat(json.loads(d)["t_onboard"]).timestamp() for _, d in buf._q]
    assert len(ts) >= RUN_S / PERIOD_S - 2
    assert max(b - a for a, b in zip(ts, ts[1:], strict=False)) <= MAX_GAP_S


async def test_tick_grid_at_100hz() -> None:
    """A fake clock: 100 frames per second on the absolute grid of period 0.01 s."""
    clock, stamps, stop, buf = [0.0], [], asyncio.Event(), FrameBuffer()

    async def sleep(d: float) -> None:
        clock[0] += d

    def now() -> datetime:
        stamps.append(clock[0])
        if len(stamps) == 301:
            stop.set()
        return datetime.fromtimestamp(clock[0], UTC)

    await tick_loop(
        "NYX-TA1", buf, random.Random(1), stop, lambda: clock[0], sleep, now, period_s=0.01
    )
    assert len(stamps) == 301
    assert all(abs(t - i * 0.01) < 1e-9 for i, t in enumerate(stamps))
    assert sum(1 for t in stamps if t < 1.0 - 1e-9) == 100
