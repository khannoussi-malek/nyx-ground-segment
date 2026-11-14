"""ING-07: ownership and failover of the ingest workers (D-ING-10..12, D-ING-24)."""

import time

from tests.support.compose import wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, ingest_backends, restarts, terminate_backends, wait_drained
from tests.support.pg import gapless, seqs_since
from tests.support.redisx import dlq_since


def test_db_error_exits_without_ack() -> None:  # connection-class error → exit, restart, 0 lost
    t0, before = now(), restarts()
    pids = ingest_backends("ingest-1")
    assert pids  # one session per vehicle task
    terminate_backends(pids)  # 57P01 on the next statement
    wait_until(lambda: restarts()["ingest-1"] > before["ingest-1"], 20)
    wait_healthy("ingest-1", 60)
    wait_drained(LIVE, 30)
    time.sleep(3)
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert len(seqs) > 30 and gapless(seqs), v
        assert dlq_since(f"tm:{v}", t0) == [], v
