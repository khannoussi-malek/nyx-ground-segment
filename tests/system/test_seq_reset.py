"""ING-05: a simulator restart (seq back to 0) is a SEQ_RESET; ingest resumes and loses nothing."""

from datetime import datetime

import psycopg

from gs.ingest.link import STALE_S
from tests.support.compose import compose, wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, wait_drained
from tests.support.pg import RESET_SEQ_MAX, app_dsn, events_for, gapless, seqs_since
from tests.support.redisx import dlq_since

RESUME_S = STALE_S  # ING-05: stored again within the staleness bound after the sim is back
WAIT_S = 30.0


def first_new_epoch(v: str, t0: datetime) -> tuple[int, float]:
    """(seq, t_received - t_onboard in s) of the first stored frame of the new epoch."""
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select seq, extract(epoch from t_received - t_onboard) from telemetry"
            " where vehicle=%s and t_onboard >= %s and seq <= %s order by t_onboard limit 1",
            (v, t0, RESET_SEQ_MAX),
        ).fetchone()
    assert row is not None, f"{v}: nothing stored after the restart"
    return int(row[0]), float(row[1])


def test_sim_restart_seq_reset() -> None:
    t0 = now()
    compose("restart", "simulator")
    wait_healthy("simulator", WAIT_S)
    for v in LIVE:
        wait_until(lambda v=v: bool(events_for(v, "SEQ_RESET", since=t0)), WAIT_S)  # type: ignore[misc]
    wait_drained(LIVE, WAIT_S)
    for v in LIVE:
        s0, latency = first_new_epoch(v, t0)
        print(f"{v}: first new-epoch seq s0={s0}, stored {latency:.3f} s after it was stamped")
        assert latency <= RESUME_S, v
        gaps = events_for(
            v, "GAP", since=t0
        )  # a reset at s0 > 0 means boot frames 0..s0-1 are gone
        if s0 > 0:
            assert [(g["details"]["from_seq"], g["details"]["lost"]) for g in gaps] == [(0, s0)], v
        else:
            assert gaps == [], v
        assert gapless(seqs_since(v, t0)) and dlq_since(f"tm:{v}", t0) == [], v
        assert len(events_for(v, "SEQ_RESET", since=t0)) == 1, v
