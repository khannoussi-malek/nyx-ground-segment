"""ING-07/08, R15 E1, A-R15-3: a persistent operational DB error restarts ingest; no frame lost."""

import time

from tests.support.compose import wait_healthy
from tests.support.env import now
from tests.support.ingest import (
    LIVE,
    restarts,
    running_ingest,
    set_read_only,
    wait_backoff_reset,
    wait_drained,
)
from tests.support.pg import events_for, gapless, seqs_since
from tests.support.redisx import dlq_since


def test_operational_error_loop_keeps_frames() -> None:
    wait_backoff_reset(running_ingest())
    t0, before = now(), restarts()
    assert before  # at least ingest-1 runs
    set_read_only(True)
    try:
        time.sleep(10)
    finally:
        set_read_only(False)
    for s in before:
        wait_healthy(s, 60)
    wait_drained(LIVE, 30)
    after = restarts()
    assert sum(after[s] - before[s] for s in before) >= 3
    time.sleep(2)  # frames sent around the drain check are stored too
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert len(seqs) > 100 and gapless(seqs), v
        assert dlq_since(f"tm:{v}", t0) == [], v
        assert events_for(v, "POISON", since=t0) == [], v
