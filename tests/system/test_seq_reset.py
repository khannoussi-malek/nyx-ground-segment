"""ING-05: a simulator restart (seq back to 0) is a SEQ_RESET; ingest resumes and loses nothing."""

from datetime import datetime

import psycopg

from gs.ingest.link import STALE_S
from tests.support.compose import compose, wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import LIVE, wait_drained
from tests.support.pg import app_dsn, events_for, gapless, seqs_since
from tests.support.redisx import dlq_since

RESUME_S = STALE_S  # ING-05: stored again within the staleness bound after the sim is back
WAIT_S = 30.0


def first_new_epoch_latency(v: str, t0: datetime) -> float:
    """t_received - t_onboard (s) of the first stored frame of the new epoch (seq <= reset max)."""
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select extract(epoch from t_received - t_onboard) from telemetry"
            " where vehicle=%s and t_onboard >= %s and seq <= 50 order by t_onboard limit 1",
            (v, t0),
        ).fetchone()
    assert row is not None, f"{v}: nothing stored after the restart"
    return float(row[0])


def test_sim_restart_seq_reset() -> None:
    t0 = now()
    compose("restart", "simulator")
    wait_healthy("simulator", WAIT_S)
    for v in LIVE:
        wait_until(lambda v=v: len(events_for(v, "SEQ_RESET", since=t0)) == 1, WAIT_S)  # type: ignore[misc]
    wait_drained(LIVE, WAIT_S)
    for v in LIVE:
        latency = first_new_epoch_latency(v, t0)
        print(f"{v}: first new-epoch frame stored {latency:.3f} s after it was stamped")
        assert latency <= RESUME_S, v
        assert gapless(seqs_since(v, t0)) and dlq_since(f"tm:{v}", t0) == [], v
