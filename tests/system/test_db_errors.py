"""ING-07/08, R15 E1, A-R15-3: a persistent operational DB error restarts ingest; no frame lost."""

import time

import psycopg

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
from tests.support.pg import admin_dsn, events_for, gapless, seqs_since
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


def test_insert_error_loop_keeps_frames() -> None:  # G19: fails without the RETRYCOUNT 0 reset
    """Only frame inserts fail (53100 disk full via a temporary trigger): startup works, so each
    restart re-reads its PEL and raises times_delivered; without the reset on the controlled
    exit, in-flight frames would reach delivery 4 and the DLQ as POISON (R15 E1)."""
    wait_backoff_reset(running_ingest())
    t0, before = now(), restarts()
    try:
        fail_inserts(True)
        time.sleep(12)
    finally:
        fail_inserts(False)
    for s in before:
        wait_healthy(s, 60)
    wait_drained(LIVE, 30)
    after = restarts()
    assert all(after[s] - before[s] >= 3 for s in before), (before, after)  # each owner looped
    time.sleep(2)
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert len(seqs) > 100 and gapless(seqs), v
        assert dlq_since(f"tm:{v}", t0) == [], v
        assert events_for(v, "POISON", since=t0) == [], v


def fail_inserts(on: bool) -> None:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        if on:
            c.execute(
                "CREATE OR REPLACE FUNCTION test_disk_full() RETURNS trigger LANGUAGE plpgsql"
                " AS $$ BEGIN"
                " IF NEW.vehicle IN ('NYX-TA1', 'NYX-TA2') THEN"
                " RAISE EXCEPTION 'test disk full' USING ERRCODE = '53100'; END IF;"
                " RETURN NEW; END $$"
            )
            c.execute("DROP TRIGGER IF EXISTS test_disk_full ON telemetry")
            c.execute(
                "CREATE TRIGGER test_disk_full BEFORE INSERT ON telemetry"
                " FOR EACH ROW EXECUTE FUNCTION test_disk_full()"
            )
        else:
            c.execute("DROP TRIGGER IF EXISTS test_disk_full ON telemetry")
            c.execute("DROP FUNCTION IF EXISTS test_disk_full()")
