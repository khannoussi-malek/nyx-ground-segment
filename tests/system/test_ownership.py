"""ING-07: ownership and failover of the ingest workers (D-ING-10..12, D-ING-24)."""

import time

import pytest

from tests.support.compose import compose, recreate, restart_count, wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import (
    FAST_ENV,
    LIVE,
    lock_holder_pid,
    restarts,
    terminate_backends,
    wait_drained,
)
from tests.support.pg import gapless, lock_holder, seqs_since
from tests.support.redisx import dlq_since


def test_each_worker_holds_its_home_vehicle() -> None:  # D-ING-10, G1/G5
    wait_until(lambda: [lock_holder(v) for v in LIVE] == ["ingest-1", "ingest-2"], 30)


def test_db_error_exits_without_ack() -> None:  # connection-class error → exit, restart, 0 lost
    t0, before = now(), restarts()
    owner = lock_holder_pid("NYX-TA1")
    assert owner is not None
    pid, svc = owner
    terminate_backends([pid])  # the lock session: 57P01 on its next statement (the fence)
    wait_until(lambda: restarts()[svc] > before[svc], 20)
    wait_healthy(svc, 60)
    wait_drained(LIVE, 30)
    time.sleep(3)
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert len(seqs) > 30 and gapless(seqs), v
        assert dlq_since(f"tm:{v}", t0) == [], v
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 30)  # handed back home


KILL_CASES = [
    "before_commit",
    "after_commit_before_publish",
    "after_publish_before_ack",
    "docker_kill",
    "crash_at_self_restart",
]


@pytest.mark.usefixtures("fast_ingest")
@pytest.mark.parametrize("case", KILL_CASES)
def test_kill_owner_mid_stream(case: str) -> None:  # ING-07: 0 lost by seq count
    t0 = now()
    if case == "docker_kill":
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 30)
        compose("kill", "ingest-1")
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", 15)  # the peer, not a restart
    else:
        point = "after_xack" if case == "crash_at_self_restart" else case
        recreate(["ingest-1"], {**FAST_ENV, "CRASH_AT": point}, wait=False)
        # the hook fires once the new ingest-1 owns NYX-TA1 again (after a hand-back), exits 99
        # and comes back through `restart: unless-stopped`; meanwhile ingest-2 holds NYX-TA1
        wait_until(lambda: restart_count("ingest-1") >= 1, 40)
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", 15)
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 30)  # hand-back, no 2nd crash
        assert restart_count("ingest-1") == 1  # the marker kept the hook from firing again
    time.sleep(3)
    recreate(["ingest-1"], FAST_ENV)  # restore, no hook
    wait_drained(LIVE, 30)
    seqs = seqs_since("NYX-TA1", t0)
    assert len(seqs) > 50 and gapless(seqs) and dlq_since("tm:NYX-TA1", t0) == []
