"""Fake-clock harnesses for the simulator's tick and send loops."""

import asyncio
import json
import random
import socket
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.exceptions

from gs.simulator.buffer import FrameBuffer, send_loop
from gs.simulator.faults import FaultBoard, FaultRequest
from gs.simulator.frames import tick_loop
from gs.simulator.main import SimSettings, run
from tests.support.env import dotenv
from tests.support.mission import MISSION_DIR
from tests.support.redisx import HOST, client

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


def _fault_call(method: str, body: dict[str, object], port: int | None) -> int:
    url = f"http://127.0.0.1:{port or dotenv()['SIM_HOST_PORT']}/sim/fault"
    req = urllib.request.Request(url, json.dumps(body).encode(), method=method)
    try:
        return int(urllib.request.urlopen(req, timeout=3).status)
    except urllib.error.HTTPError as e:
        return e.code


def post_fault(vehicle: str, fault: str, port: int | None = None, **params: object) -> int:
    """POST /sim/fault on the compose sim (SIM_HOST_PORT) or `port`; returns the HTTP status."""
    return _fault_call("POST", {"vehicle": vehicle, "fault": fault, "params": params}, port)


def delete_fault(vehicle: str, fault: str, port: int | None = None) -> int:
    return _fault_call("DELETE", {"vehicle": vehicle, "fault": fault}, port)


@asynccontextmanager
async def running_sim(v: str) -> AsyncIterator[int]:
    """In-process sim for `v` against the compose redis; yields its free health port."""
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        port = sock.getsockname()[1]
    env = dotenv()
    url = f"redis://sim:{env['REDIS_SIM_PW']}@{HOST}:{env['REDIS_HOST_PORT']}/0"
    stop = asyncio.Event()
    task = asyncio.create_task(run(SimSettings(url, (v,), "1", HOST, port, MISSION_DIR), stop))
    await asyncio.sleep(0.3)  # health server up
    try:
        yield port
    finally:
        stop.set()
        await task


async def run_sim_with(
    v: str, seconds: float, at: float, fault: tuple[str, dict[str, object]]
) -> list[str]:
    """In-process sim for `v`; POST `fault` at `at` s; the raw `data` of every tm:<v> entry after
    `seconds`."""
    async with running_sim(v) as port:
        await asyncio.sleep(at - 0.3)
        assert await asyncio.to_thread(post_fault, v, fault[0], port, **fault[1]) == 204
        await asyncio.sleep(seconds - at)
    entries: Any = client("admin").xrange(f"tm:{v}")
    return [str(f["data"]) for _, f in entries]


def tick_entries(
    n: int, act: Callable[[int, FaultBoard, float], None] = lambda *_: None
) -> list[str]:
    """Run tick_loop for `n` ticks on a fake clock; `act(k, board, mono)` runs just before tick k.
    The raw entries the loop appended."""
    clock, buf, stop, board, ticks = [0.0], FrameBuffer(), asyncio.Event(), FaultBoard(), [0]

    async def sleep(d: float) -> None:
        clock[0] += d

    def now() -> datetime:
        act(ticks[0], board, clock[0])
        ticks[0] += 1
        if ticks[0] == n:
            stop.set()
        return EPOCH + timedelta(seconds=clock[0])

    asyncio.run(
        tick_loop(
            "NYX-TA1", buf, random.Random("1:NYX-TA1"), stop, lambda: clock[0], sleep, now, board
        )
    )
    return [d for _, d in buf._q]


def at_tick(k: int, *reqs: FaultRequest) -> Callable[[int, FaultBoard, float], None]:
    def act(tick: int, board: FaultBoard, mono: float) -> None:
        if tick == k:
            for r in reqs:
                board.add(r, mono)

    return act


def fault_req(fault: str, **params: object) -> FaultRequest:
    return FaultRequest.model_validate({"vehicle": "NYX-TA1", "fault": fault, "params": params})


def seqs_of(raws: list[str]) -> list[int]:
    """Seqs of the entries that are valid JSON with an int seq."""
    out = []
    for raw in raws:
        try:
            seq = json.loads(raw)["seq"]
        except ValueError, KeyError:
            continue
        if isinstance(seq, int):
            out.append(seq)
    return out
