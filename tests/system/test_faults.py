"""ATP-05 backend: the sim fault API against the live stack (non-alarm cases)."""

import json
import time
from collections import Counter
from datetime import datetime
from typing import Any

import psycopg

from gs.common.interfaces import DlqRecord
from gs.ingest.link import FROZEN_S
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, wait_drained
from tests.support.pg import app_dsn, events_for, stored
from tests.support.redisx import client, dlq_since
from tests.support.sim import delete_fault, post_fault

V = "NYX-TA1"
COPIES = 2  # extra copies the fault appends to one frame
JUMP = 1000
WAIT_S = 20.0
FREEZE_S = FROZEN_S + 1.0  # long enough to cross the detector's threshold once


def stream_seqs(since: datetime) -> Counter[int]:
    """Occurrences of each valid-JSON seq in tm:<V> since `since` (by entry id)."""
    entries: Any = client("admin").xrange(f"tm:{V}", min=str(int(since.timestamp() * 1000)))
    out: Counter[int] = Counter()
    for _, f in entries:
        try:
            out[json.loads(f["data"])["seq"]] += 1
        except ValueError, KeyError, TypeError:
            continue
    return out


def test_duplicate_stored_once() -> None:
    t0 = now()
    assert post_fault(V, "duplicate", count=COPIES) == 204
    wait_until(lambda: 1 + COPIES in stream_seqs(t0).values(), WAIT_S)  # all copies are in
    wait_drained(LIVE, WAIT_S)
    (seq,) = [s for s, n in stream_seqs(t0).items() if n > 1]
    assert stream_seqs(t0)[seq] == 1 + COPIES  # and no more
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select count(*) from telemetry where vehicle=%s and seq=%s and t_onboard >= %s",
            (V, seq, t0),
        )
        assert row.fetchone() == (1,)
    assert events_for(V, "GAP", since=t0) == []
    assert dlq_since(f"tm:{V}", t0) == []


def test_malformed_to_dlq_and_ingest_continues() -> None:
    t0 = now()
    assert post_fault(V, "malformed", kind="json") == 204
    wait_until(lambda: bool(dlq_since(f"tm:{V}", t0)), WAIT_S)
    (raw,) = dlq_since(f"tm:{V}", t0)
    assert DlqRecord.model_validate_json(raw).reason == "json_invalid"
    before = stored(V)  # taken after the DLQ record: growth from here is ingest continuing
    wait_until(lambda: stored(V) > before, WAIT_S)  # ingest kept going
    wait_drained(LIVE, WAIT_S)
    assert events_for(V, "POISON", since=t0) == []


def test_seq_jump_gives_seq_jump_not_gap() -> None:
    t0 = now()
    assert post_fault(V, "seq_jump", by=JUMP) == 204
    wait_until(lambda: bool(events_for(V, "SEQ_JUMP", since=t0)), WAIT_S)
    (ev,) = events_for(V, "SEQ_JUMP", since=t0)
    assert ev["details"]["lost"] == JUMP
    assert events_for(V, "GAP", since=t0) == []


def test_freeze_gives_frozen_event() -> None:
    t0 = now()
    assert post_fault(V, "freeze", duration_s=FREEZE_S) == 204
    try:
        wait_until(lambda: bool(events_for(V, "FROZEN", since=t0)), FREEZE_S + WAIT_S)
        time.sleep(FREEZE_S)  # the freeze is over: a late duplicate FROZEN would be in by now
    finally:
        delete_fault(V, "freeze")
    wait_drained(LIVE, WAIT_S)
    assert len(events_for(V, "FROZEN", since=t0)) == 1


# Integration agent: add the live-leak case here (ATP-05 / ALM-04, ALM-06):
# test_leak_roc_then_red_one_alarm, which needs the alarm engine wired into ingest.
