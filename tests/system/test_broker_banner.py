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
