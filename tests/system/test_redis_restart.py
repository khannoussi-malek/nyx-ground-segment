"""ENG-02 backend: a 10 s Redis stop recovers by itself, loses no frame, raises no false LOS
(D-UI-04/D-UI-15) and makes the two ingest workers neither steal from nor flap with each other
(G23: the steal hold-off outlasts a healthy holder's own reconnect)."""

import time
from datetime import datetime

import pytest

from gs.common.time import iso_z
from gs.simulator.frames import PERIOD_S
from tests.support.compose import compose, wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import INGEST, LIVE, wait_drained
from tests.support.pg import events_for, gapless, lock_holder, seqs_since
from tests.support.redisx import dlq_since

OUTAGE_S = 10.0
HEALTHY_S = 60.0  # container healthchecks catch up after the broker is back
DRAIN_S = 30.0  # the sim's buffer drains and ingest catches up
HOME = {"NYX-TA1": "ingest-1", "NYX-TA2": "ingest-2"}  # compose INGEST_HOMES


def logs_since(svc: str, t0: datetime) -> str:
    return compose("logs", "--since", iso_z(t0), svc).stdout


@pytest.mark.usefixtures("fast_ingest")
def test_redis_stop_10s_recovers() -> None:
    wait_until(lambda: {v: lock_holder(v) for v in LIVE} == HOME, 30)
    t0 = now()
    compose("stop", "redis")
    try:
        time.sleep(OUTAGE_S)
    finally:
        compose("start", "redis")
    for svc in ("simulator", *INGEST, "api"):
        wait_healthy(svc, HEALTHY_S)
    frames = OUTAGE_S / PERIOD_S  # the sim buffered at least the outage's worth
    for v in LIVE:  # "drained" alone is true while the sim is still reconnecting
        wait_until(lambda v=v: len(seqs_since(v, t0)) >= frames, DRAIN_S)  # type: ignore[misc]
    wait_drained(LIVE, DRAIN_S)
    wait_until(lambda: {v: lock_holder(v) for v in LIVE} == HOME, DRAIN_S)
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert gapless(seqs), v  # the sim buffered the outage
        assert dlq_since(f"tm:{v}", t0) == [], v
        for kind in ("GAP", "LOS"):
            assert events_for(v, kind, since=t0) == [], f"{v}: false {kind}"
    for svc in INGEST:
        assert "stole" not in logs_since(svc, t0), f"{svc} stole a vehicle after the outage"
