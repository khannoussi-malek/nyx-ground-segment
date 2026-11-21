"""Backfill past hours of one vehicle through the normal stream path (D-ENG-16, T4-s).

A virtual clock drives the unchanged tick_loop: tick n is stamped t_from + n x 100 ms and costs no
wall time, except that the loop waits while the send buffer is half full (back-pressure instead of
a wall rate), so ingest sees an ordinary stream, just early."""

import argparse
import asyncio
import json
import math
import random
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import redis.asyncio
import redis.exceptions
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from gs.common.config import ConfigError, require
from gs.simulator.buffer import BUFFER_MAX, FrameBuffer, send_loop
from gs.simulator.faults import FaultBoard, FaultRequest
from gs.simulator.frames import PERIOD_S, tick_loop
from gs.simulator.main import xadd_tm

BACKFILL_VEHICLE = "NYX-TA9"
HIGH_WATER = BUFFER_MAX // 2
VALIDITY_MARGIN = timedelta(hours=1)  # ingest accepts t_onboard up to 7 days old
VALIDITY = timedelta(days=7)
PERIOD_US = round(PERIOD_S * 1_000_000)
_POLL_S = 0.001
# XADD ... MAXLEN never trims entries nobody acknowledged, and Redis (noeviction, 512 MB) is shared
# with the live vehicles: stop producing while the ingest group is more than an hour behind (G4-9)
CONSUMER_GROUP = "ingest"
LAG_MAX = 36_000
LAG_CHECK_TICKS = 1_000  # look every 100 s of virtual time
LAG_POLL_S = 0.2


@dataclass(frozen=True)
class FaultAt:
    offset_s: float  # virtual seconds after t_from
    request: FaultRequest


def parse_fault(spec: str, vehicle: str) -> FaultAt:
    """`name@offset_s[:<json params>]`; NaN/Infinity in a parameter is rejected by the model."""
    head, _, raw = spec.partition(":")
    name, at, off = head.partition("@")
    if not at:
        raise ValueError(f"fault {spec!r} is not name@offset_s[:json]")
    offset = float(off)
    if not math.isfinite(offset) or offset < 0:
        raise ValueError(f"fault offset {off!r} must be a finite number >= 0")
    params = json.loads(raw) if raw else {}
    return FaultAt(
        offset, FaultRequest.model_validate({"vehicle": vehicle, "fault": name, "params": params})
    )


def validate_window(t_from: datetime, t_to: datetime, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    for t in (t_from, t_to):
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError(f"naive datetime {t!r}")
    if not now - VALIDITY + VALIDITY_MARGIN <= t_from < t_to <= now:
        raise ValueError(
            f"window [{t_from}, {t_to}) must lie inside now - 7 d + 1 h .. now and not be empty"
        )


class _WindowDone(Exception):
    pass


async def _lag(r: redis.asyncio.Redis, stream: str) -> int:
    """Entries the ingest group has not been delivered yet (0 when unknown or no group)."""
    try:
        groups = await r.execute_command("XINFO", "GROUPS", stream)  # type: ignore[no-untyped-call]
    except redis.exceptions.ResponseError as e:
        if "no such key" in str(e):  # nothing sent yet: no stream, no lag
            return 0
        raise
    for g in groups or []:
        if g.get("name") in (CONSUMER_GROUP, CONSUMER_GROUP.encode()):
            return int(g.get("lag") or 0)
    return 0


async def backfill(
    r: redis.asyncio.Redis,
    vehicle: str,
    t_from: datetime,
    t_to: datetime,
    seed: str,
    faults: Sequence[FaultAt] = (),
    pause_at: Sequence[int] = (),
    resume: Callable[[int], Awaitable[None]] | None = None,
) -> int:
    """Frames [t_from, t_to) at 10 Hz, seq 0.., into tm:<vehicle>; returns how many were sent.
    Before tick n in `pause_at` the buffer drains, then `resume(n)` is awaited."""
    validate_window(t_from, t_to)
    total_us = (t_to - t_from) // timedelta(microseconds=1)
    buf, board, stop = FrameBuffer(), FaultBoard(), asyncio.Event()
    due = sorted(faults, key=lambda f: f.offset_s)
    pauses, sent, tick = set(pause_at), 0, -1  # the tick the loop is about to run
    tick_us = 0

    async def xadd(stream: str, data: str) -> None:
        nonlocal sent
        await xadd_tm(r, stream, data)
        sent += 1

    async def drained() -> None:
        while len(buf):
            await asyncio.sleep(_POLL_S)

    async def sleep(_: float) -> None:
        """Called once before each tick: advance the virtual clock instead of waiting."""
        nonlocal tick, tick_us
        tick += 1
        tick_us = tick * PERIOD_US
        if tick in pauses and resume is not None:
            await drained()
            await resume(tick)
        while len(buf) >= HIGH_WATER:
            await asyncio.sleep(_POLL_S)
        if tick % LAG_CHECK_TICKS == 0:
            while await _lag(r, f"tm:{vehicle}") > LAG_MAX:
                await asyncio.sleep(LAG_POLL_S)
        await asyncio.sleep(0)  # let the sender run
        if tick_us >= total_us:
            raise _WindowDone
        while due and due[0].offset_s <= tick_us / 1e6:
            board.add(due.pop(0).request, tick_us / 1e6)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(send_loop(buf, xadd, stop))
        try:
            await tick_loop(
                vehicle, buf, random.Random(f"{seed}:{vehicle}"), asyncio.Event(),  # noqa: S311 simulated telemetry, not crypto
                monotonic=lambda: tick_us / 1e6,
                sleep=sleep,
                now=lambda: t_from + timedelta(microseconds=tick_us),
                faults=board,
            )  # fmt: skip
        except _WindowDone:
            pass
        await drained()
        stop.set()
    return sent


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def _pause(n: int) -> None:
    print(f"paused at seq {n}: press Enter to continue", flush=True)
    await asyncio.to_thread(sys.stdin.readline)


def main(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(prog="gs-sim backfill")
    p.add_argument("--vehicle", required=True)
    p.add_argument("--from", dest="t_from", required=True)
    p.add_argument("--to", dest="t_to", required=True)
    p.add_argument("--fault", action="append", default=[])
    p.add_argument("--pause-at-seq", action="append", type=int, default=[])
    a = p.parse_args(argv)
    if a.vehicle != BACKFILL_VEHICLE:
        print("backfill writes only NYX-TA9 (D-ENG-16)", file=sys.stderr)
        return 2
    try:
        t_from, t_to = _utc(a.t_from), _utc(a.t_to)
        validate_window(t_from, t_to)
        faults = [parse_fault(f, a.vehicle) for f in a.fault]
        url, seed = require("REDIS_URL"), require("SIM_SEED")
    except (ValueError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    r = redis.asyncio.Redis.from_url(url, socket_timeout=2, retry=Retry(NoBackoff(), 0))

    async def go() -> int:
        try:
            return await backfill(
                r, a.vehicle, t_from, t_to, seed, faults, a.pause_at_seq,
                _pause if a.pause_at_seq else None,
            )  # fmt: skip
        finally:
            await r.aclose()

    print(f"sent {asyncio.run(go())} frames to tm:{a.vehicle}")
    return 0
