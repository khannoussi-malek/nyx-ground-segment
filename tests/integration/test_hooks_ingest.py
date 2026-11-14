"""D-ENG-16 hook call sites in the ingest worker."""

import asyncio
import signal
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from gs.common.hooks import Hooks
from gs.ingest.errors import OperationalDbError
from gs.ingest.worker import Timing
from tests.support.env import until, vid, wait_until
from tests.support.ingest import lock_holder_pid, start_ingest, terminate_backends, worker
from tests.support.pg import lock_holder, stored
from tests.support.redisx import (
    drained,
    pending,
    pending_of,
    produce,
    ui_tm_count,
)

N = 5


class Crash(BaseException):
    pass


def crash(_: int) -> NoReturn:
    raise Crash


def stop(p: subprocess.Popen[bytes]) -> None:
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=5) == 0


def test_crash_before_commit_loses_nothing(tmp_path: Path) -> None:  # subprocess, marker in TMPDIR
    v = vid()
    produce(v, 20)
    env = {"VEHICLES": v, "INGEST_HOMES": f"ingest-main-test={v}", "GS_TEST_HOOKS": "1"}
    p, _ = start_ingest(CRASH_AT="before_commit", TMPDIR=str(tmp_path), **env)
    try:
        assert p.wait(timeout=10) == 99
        assert stored(v) == 0 and pending(v) > 0 and (tmp_path / "gs-hook-before_commit").exists()
        p, _ = start_ingest(CRASH_AT="before_commit", TMPDIR=str(tmp_path), **env)  # restart
        wait_until(lambda: stored(v) == 20 and drained(v), 10)
        stop(p)
    finally:
        if p.poll() is None:
            p.kill()


# (point, stored, published to ui:events, still pending) when the hook crashes the first batch
PLACES = [
    ("before_commit", 0, 0, N),
    ("after_commit_before_publish", N, 0, N),
    ("after_publish_before_ack", N, N, N),
    ("after_xack", N, N, 0),
]


@pytest.mark.parametrize(("point", "rows", "published", "left"), PLACES)
async def test_named_point_placement(
    tmp_path: Path, point: str, rows: int, published: int, left: int
) -> None:
    v = vid()
    produce(v, N)
    hooks = Hooks(crash_at=point, marker_dir=tmp_path, exit=crash)
    with pytest.raises(Crash):
        async with worker([v], hooks=hooks) as w:
            await asyncio.wait_for(w.tasks[0], 5)
    assert (stored(v), ui_tm_count(v), pending(v)) == (rows, published, left)


async def test_after_fence_before_read_is_before_the_read(tmp_path: Path) -> None:
    v = vid()
    produce(v, N)
    hooks = Hooks(crash_at="after_fence_before_read", marker_dir=tmp_path, exit=crash)
    with pytest.raises(Crash):
        async with worker([v], hooks=hooks) as w:
            await asyncio.wait_for(w.tasks[0], 5)
    assert stored(v) == 0 and pending(v) == 0 and not drained(v)  # nothing was read


async def test_frame_seq_crashes_on_every_delivery(tmp_path: Path) -> None:
    v = vid()
    produce(v, N)
    hooks = Hooks(crash_at=f"frame_seq:{v}:3", marker_dir=tmp_path, exit=crash)
    rows = []
    for _ in range(2):  # the same frame kills every delivery: no marker
        with pytest.raises(Crash):
            async with worker([v], hooks=hooks) as w:
                await asyncio.wait_for(w.tasks[0], 5)
        rows.append(stored(v))
    # 1st: one `>` batch of 5, the hook fires before its transaction; 2nd: the redelivery
    # drain is one entry at a time, so 0..2 commit and seq 3 crashes again
    assert rows == [0, 3]


async def test_hang_in_loop_stops_the_loop() -> None:
    v = vid()
    produce(v, N)
    with pytest.raises(asyncio.CancelledError):  # a hung loop ignores stop; cancel it
        async with worker([v], hooks=Hooks(hang_at="in_loop"), timing=Timing()) as w:
            await until(lambda: lock_holder(v) == "ingest-test", 5)
            seen = w.ctx.loop_seen[v]
            await asyncio.sleep(1.0)
            assert w.ctx.loop_seen[v] == seen and stored(v) == 0 and not w.done()
            w.tasks[0].cancel()


async def test_after_fence_hook_sits_between_fence_and_read(tmp_path: Path) -> None:
    """R14 E-5's window is the `>` read: the hook loses the lock right after the fence passed and
    the worker still reads new entries into its PEL once, before its commit fails. The takeover
    drain pass (nothing pending here) must not consume the once-per-container hook."""
    v = vid()
    produce(v, N)

    def lose_lock(_: int, __: int) -> None:
        owner = lock_holder_pid(v)
        assert owner is not None
        terminate_backends([owner[0]])

    hooks = Hooks(stop_at="after_fence_before_read", marker_dir=tmp_path, kill=lose_lock)
    with pytest.raises(OperationalDbError):
        async with worker([v], "w1", hooks=hooks) as w:
            await asyncio.wait_for(w.tasks[0], 5)
    # a stale `>` read took all N new entries into w1's PEL; with the hook above the fence, the
    # fence fails first and nothing is read
    assert (pending_of(v, "w1"), stored(v)) == (N, 0)
