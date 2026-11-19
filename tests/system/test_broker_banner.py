"""UI-07 backend, D-UI-04: a 10 s broker outage shows as SSE ground down, then up with a fresh
snapshot, and is never mistaken for a loss of signal (no LOS row for either vehicle)."""

import asyncio
import time
import urllib.request

from tests.support.compose import api_url, compose
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, wait_drained
from tests.support.pg import events_for
from tests.support.sse import collecting, live_stack, wait_seen

OUTAGE_S = 10.0
LATE_S = 3.0  # the simulator's worst resume after the broker: 1 s send backoff + 2 s socket timeout
SILENT_S = 20.0  # well past the resume grace + los_s: a vehicle this silent is really lost


def api_ok() -> bool:
    try:
        return bool(urllib.request.urlopen(f"{api_url()}/healthz", timeout=3).status == 200)
    except OSError:
        return False


def outage() -> None:
    try:
        compose("stop", "redis")
        time.sleep(OUTAGE_S)
    finally:
        compose("start", "redis")


def outage_source_paused(paused_s: float) -> None:
    """The broker is back but the frame source (simulator) stays paused for `paused_s`."""
    try:
        compose("stop", "redis")
        time.sleep(OUTAGE_S)
        compose("pause", "simulator")
        compose("start", "redis")
        time.sleep(paused_s)
    finally:
        compose("unpause", "simulator", check=False)


async def test_source_resumes_late_after_broker_no_los() -> None:
    t0 = now()  # NCR-04: ingest reconnects first, the first frame comes LATE_S later
    await asyncio.to_thread(outage_source_paused, LATE_S)
    await asyncio.to_thread(wait_until, api_ok, 60)
    await asyncio.to_thread(wait_drained, LIVE, 30)
    for v in LIVE:
        assert events_for(v, "LOS", since=t0) == [], v


async def test_source_silent_after_broker_still_los() -> None:
    t0 = now()  # the grace delays the LOS clock, it never suppresses a real loss
    await asyncio.to_thread(outage_source_paused, SILENT_S)
    await asyncio.to_thread(wait_until, api_ok, 60)
    await asyncio.to_thread(wait_drained, LIVE, 30)
    for v in LIVE:
        assert events_for(v, "LOS", since=t0), v


async def test_redis_stop_ground_down_no_los_event() -> None:
    t0 = now()
    async with collecting(live_stack()) as s:
        await wait_seen(s, lambda k, d: k == "ground", 5)  # every connect: snapshot, then ground
        mark = len(s.seen)
        await asyncio.to_thread(outage)
        down = await wait_seen(s, lambda k, d: k == "ground" and d["broker"] == "down", 5, mark)
        after = s.seen.index(down)
        up = await wait_seen(s, lambda k, d: k == "ground" and d["broker"] == "up", 60, after)
        await wait_seen(s, lambda k, d: k == "snapshot", 10, s.seen.index(up))
    await asyncio.to_thread(wait_until, api_ok, 60)
    await asyncio.to_thread(wait_drained, LIVE, 30)
    for v in LIVE:
        assert events_for(v, "LOS", since=t0) == [], v
