"""Fake-clock harnesses for the simulator's tick and send loops."""

import asyncio
import json
import random
from datetime import UTC, datetime, timedelta

import redis.exceptions

from gs.simulator.buffer import FrameBuffer, send_loop
from gs.simulator.frames import tick_loop

EPOCH = datetime(2026, 11, 14, 10, tzinfo=UTC)


async def run_ticks_with_fake_clock(
    n: int, stall_after_tick: int, stall_s: float
) -> tuple[list[float], list[float], list[int]]:
    """Returns (stamp offsets in s, sleep args, seqs). The stall hits after tick `stall_after_tick`
    is stamped (processing took stall_s); the clock starts at 0."""
    clock, sleeps, stamps = [0.0], [], []
    buf, stop = FrameBuffer(), asyncio.Event()

    async def sleep(d: float) -> None:
        sleeps.append(d)
        clock[0] += d

    def now() -> datetime:
        stamps.append(round(clock[0], 6))
        t = EPOCH + timedelta(seconds=clock[0])
        if len(stamps) - 1 == stall_after_tick:
            clock[0] += stall_s
        if len(stamps) == n:
            stop.set()
        return t

    await tick_loop("NYX-TA1", buf, random.Random("1:NYX-TA1"), stop, lambda: clock[0], sleep, now)
    seqs = [json.loads(d)["seq"] for _, d in buf._q]
    return stamps, sleeps, seqs


async def drive_send_loop(frames: list[str], failures: int) -> tuple[list[str], list[float]]:
    """Send `frames`; the first `failures` xadd calls raise a Redis ConnectionError."""
    buf, stop, sent, sleeps, calls = FrameBuffer(), asyncio.Event(), [], [], [0]
    for f in frames:
        buf.append("tm:NYX-TA1", f)

    async def xadd(stream: str, data: str) -> object:
        calls[0] += 1
        if calls[0] <= failures:
            raise redis.exceptions.ConnectionError("down")
        sent.append(data)
        if len(sent) == len(frames):
            stop.set()
        return b"1-0"

    async def sleep(d: float) -> None:
        sleeps.append(d)

    await send_loop(buf, xadd, stop, sleep)
    return sent, sleeps
