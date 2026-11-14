"""ING-07: ownership and failover of the ingest workers (D-ING-10..12, D-ING-24)."""

import time

from tests.support.compose import wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, lock_holder_pid, restarts, terminate_backends, wait_drained
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
