"""D-ING-05/P-pf9: an owner that dies after committing a RED step but before publishing it
leaves the console without that alarm only until the next owner's resync: its fresh snapshot
carries the alarm with alert true (CRASH_AT=after_commit_before_publish, D-ENG-16)."""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import pytest

from tests.support.compose import compose, recreate, restart_count
from tests.support.env import now, wait_until
from tests.support.ingest import FAST_ENV, INGEST, LIVE, wait_drained
from tests.support.pg import lock_holder
from tests.support.redisx import client
from tests.support.sim import delete_fault, post_fault
from tests.support.sse import collecting, live_stack, wait_seen

V, PARAM, RED = "NYX-TA1", "bus_voltage_v", 33.0  # red_high 32; nominal 28
# held red past the crash (a 2 s step would RAISE and RTN in the same backlog batch, and the
# snapshot's last transition would be the RTN); DELETEd at the end. The backlog (RED_QUEUED red
# frames, then whatever the recreate adds) stays inside the first batch's 100 frames.
STEP_S, RED_QUEUED = 60.0, 10


def red_queued(since_ms: int) -> bool:
    """RED_QUEUED frames with the step's value are in tm:V since `since_ms` (nobody reads it)."""
    entries: Any = client("admin").xrange(f"tm:{V}", min=str(since_ms))
    reds = [f for _, f in entries if json.loads(f["data"])["values"].get(PARAM) == RED]
    return len(reds) >= RED_QUEUED


def crash_on_the_step() -> None:
    """Both workers stopped, the RED step queued in tm:NYX-TA1, then ingest-1 back with the hook:
    its first commit (the backlog, step included) is followed by exit 99 before the publish."""
    wait_until(lambda: lock_holder(V) == "ingest-1", 30)
    compose("stop", *INGEST)
    since_ms = int(time.time() * 1000)
    assert post_fault(V, "step", param=PARAM, value=RED, duration_s=STEP_S) == 204
    wait_until(lambda: red_queued(since_ms), 10)
    recreate(["ingest-1"], {**FAST_ENV, "CRASH_AT": "after_commit_before_publish"}, wait=False)
    wait_until(lambda: restart_count("ingest-1") >= 1, 30)


def has_red(data: dict[str, Any], since: datetime) -> bool:
    alarms = data.get("alarms") or []
    return any(
        a["vehicle"] == V and a["param"] == PARAM and a["last"]["alert"] is True
        and datetime.fromisoformat(a["last"]["t"]) >= since
        for a in alarms
    )  # fmt: skip


@pytest.mark.usefixtures("fast_ingest")
async def test_resync_after_crash() -> None:
    t0 = now()
    try:
        async with collecting(live_stack()) as s:
            await wait_seen(s, lambda k, d: k == "snapshot", 5)
            await asyncio.to_thread(crash_on_the_step)
            mark = len(s.seen)
            _, snap = await wait_seen(s, lambda k, d: k == "snapshot" and has_red(d, t0), 60, mark)
        assert snap["vehicles"][V]["vehicle"] == V
    finally:
        await asyncio.to_thread(delete_fault, V, "step")
        await asyncio.to_thread(recreate, list(INGEST), FAST_ENV)
    await asyncio.to_thread(wait_drained, LIVE, 30)
