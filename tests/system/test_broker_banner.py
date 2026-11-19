"""UI-07 backend, D-UI-04: a 10 s broker outage shows as SSE ground down, then up with a fresh
snapshot, and is never mistaken for a loss of signal (no LOS row for either vehicle)."""

import asyncio
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime

import redis.exceptions

from gs.ingest.link import STALE_S
from gs.ingest.worker import RESUME_GRACE_S
from tests.support.compose import api_url, compose
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, wait_drained
from tests.support.pg import events_for
from tests.support.redisx import client
from tests.support.sse import collecting, live_stack, wait_seen

OUTAGE_S = 10.0
LATE_S = 3.5  # after ingest's reconnect: more than los_s (2 s), less than RESUME_GRACE_S (5 s)
MARGIN_S = 3.0  # one 1 s blocked read, the event write, polling
LOS_BOUND_S = RESUME_GRACE_S + STALE_S + MARGIN_S  # from ingest's first pass back to the LOS row


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


def ingest_back() -> bool:
    """hb:veh:<v> is set by every successful ingest pass and expired during the 10 s outage."""
    try:
        return all(client("admin").exists(f"hb:veh:{v}") for v in LIVE)
    except redis.exceptions.RedisError:
        return False


def outage_source_paused(then: Callable[[], None]) -> None:
    """Stop the broker, pause the frame source, start the broker, wait for ingest's first
    successful pass, run `then` (source still paused), then resume the source."""
    try:
        compose("stop", "redis")
        time.sleep(OUTAGE_S)
        compose("pause", "simulator")
        compose("start", "redis")
        wait_until(ingest_back, 60)
        then()
    finally:
        compose("unpause", "simulator", check=False)


def users(r: redis.Redis, names: tuple[str, ...], on: bool) -> None:
    """Switch Redis users on or off; off also drops their open connections."""
    for u in names:
        r.acl_setuser(u, enabled=on)  # only on/off: no reset, the rules stay
        if not on:
            r.client_kill_filter(user=u)


def outage_source_late(late_s: float) -> None:
    """Ingest and the simulator lose the broker for OUTAGE_S; ingest gets it back first, the
    simulator `late_s` after ingest's first successful pass. Unlike `docker pause`, the simulator
    keeps generating on its grid and buffers, so there is no vehicle-time gap: only the delivery
    is late, as in CI run 44423135993 (NCR-04). Redis keeps running, so its ACL change sticks."""
    r = client("admin")
    try:
        users(r, ("ingest", "sim"), on=False)
        time.sleep(OUTAGE_S)
        users(r, ("ingest",), on=True)
        wait_until(ingest_back, 60)
        time.sleep(late_s)
    finally:
        users(r, ("ingest", "sim"), on=True)


def acl_log_only_our_auth_failures(r: redis.Redis, was_empty: bool) -> None:
    """CI's check_acl_log wants an empty ACL LOG. Reset it only when it was empty before this
    test and every new entry is an auth failure of the users this test switched off."""
    entries = r.acl_log()
    assert all(e["reason"] == "auth" and e["username"] in ("ingest", "sim") for e in entries), (
        entries
    )
    if was_empty:
        r.acl_log_reset()


def assert_no_los(t0: datetime) -> None:
    """No LOS since t0. On a failure, print the ingest and simulator logs of the window first:
    later system tests recreate the containers, so CI's compose.log never holds them (NCR-04)."""
    found = {v: events_for(v, "LOS", since=t0) for v in LIVE}
    if any(found.values()):
        since = t0.isoformat()
        logs = compose("logs", "--no-color", "--timestamps", "--since", since,
                       "ingest-1", "ingest-2", "simulator", check=False)  # fmt: skip
        print(logs.stdout[-300_000:])
    for v in LIVE:
        assert found[v] == [], v


def los_for_all(t0: datetime) -> bool:
    return all(events_for(v, "LOS", since=t0) for v in LIVE)


async def test_source_resumes_late_after_broker_no_los() -> None:
    t0 = now()  # NCR-04: the first frame comes LATE_S after ingest's reconnect (> los_s)
    admin = client("admin")
    was_empty = not admin.acl_log()
    await asyncio.to_thread(outage_source_late, LATE_S)
    await asyncio.to_thread(wait_drained, LIVE, 30)
    try:
        assert_no_los(t0)
    finally:
        acl_log_only_our_auth_failures(admin, was_empty)


async def test_source_silent_after_broker_still_los() -> None:
    t0 = now()  # the grace delays the LOS clock, it never suppresses a real loss

    def los_in_time() -> None:
        wait_until(lambda: los_for_all(t0), LOS_BOUND_S)  # while the source is still paused

    await asyncio.to_thread(outage_source_paused, los_in_time)
    await asyncio.to_thread(wait_until, api_ok, 60)
    await asyncio.to_thread(wait_drained, LIVE, 30)


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
    assert_no_los(t0)
