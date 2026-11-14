"""ENG-02 backend: a 10 s Redis stop recovers by itself, loses no frame, raises no false LOS
(D-UI-04/D-UI-15) and makes the two ingest workers neither steal from nor flap with each other
(G23: the steal hold-off outlasts a healthy holder's own reconnect)."""

import time
import urllib.request
from datetime import datetime

import psycopg
import pytest

from gs.common.time import iso_z
from gs.ingest.link import STALE_S
from gs.simulator.frames import PERIOD_S
from tests.support.compose import api_url, compose, restart_count
from tests.support.env import now, wait_until
from tests.support.ingest import INGEST, LIVE, wait_drained
from tests.support.pg import app_dsn, events_for, gapless, lock_holder, seqs_since
from tests.support.redisx import dlq_since

OUTAGE_S = 10.0
HEALTHY_S = 60.0  # the api's /healthz (it includes Redis) is green again
DRAIN_S = 30.0  # the sim's buffer drains and ingest catches up
CAUGHT_UP_S = 1.0  # newest stored frame at most this old = the sim's buffer is flushed
HOME = {"NYX-TA1": "ingest-1", "NYX-TA2": "ingest-2"}  # compose INGEST_HOMES
APP = ("simulator", *INGEST, "api")


def logs_since(svc: str, t0: datetime) -> str:
    return compose("logs", "--since", iso_z(t0), svc).stdout


def api_ok() -> bool:
    try:
        return bool(urllib.request.urlopen(f"{api_url()}/healthz", timeout=3).status == 200)
    except OSError:  # URLError, HTTPError (503) and timeouts
        return False


def caught_up(v: str) -> bool:
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute("select max(t_onboard) from telemetry where vehicle=%s", (v,)).fetchone()
    newest: datetime | None = row[0] if row else None
    return newest is not None and (now() - newest).total_seconds() < CAUGHT_UP_S


def data_stale_gaps(v: str, t0: datetime) -> int:
    """Vehicle-time gaps > STALE_S between consecutive stored frames since t0: ingest must (and
    only must) report a LOS for each (D-ING-21). The sim stamps at its own tick, so a stall of
    the sim itself (seen once under host load) is a real silence in the data, not a Redis effect."""
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select count(*) from (select t_onboard - lag(t_onboard) over (order by seq) as d"
            " from telemetry where vehicle=%s and t_onboard >= %s) g"
            " where d > make_interval(secs => %s)",
            (v, t0, STALE_S),
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.usefixtures("fast_ingest")
def test_redis_stop_10s_recovers() -> None:
    wait_until(lambda: {v: lock_holder(v) for v in LIVE} == HOME, 30)
    restarts = {s: restart_count(s) for s in APP}
    t0 = now()
    try:
        compose("stop", "redis")
        time.sleep(OUTAGE_S)
    finally:
        compose("start", "redis")
    wait_until(api_ok, HEALTHY_S)
    for v in LIVE:  # "drained" alone is true while the sim is still reconnecting
        wait_until(lambda v=v: caught_up(v), DRAIN_S)  # type: ignore[misc]
    wait_drained(LIVE, DRAIN_S)
    wait_until(lambda: {v: lock_holder(v) for v in LIVE} == HOME, DRAIN_S)
    assert {s: restart_count(s) for s in APP} == restarts  # recovered, not crash-looped
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert len(seqs) >= OUTAGE_S / PERIOD_S and gapless(seqs), v  # the sim buffered the outage
        assert dlq_since(f"tm:{v}", t0) == [], v
        for kind in ("GAP", "SEQ_JUMP", "SEQ_RESET"):
            assert events_for(v, kind, since=t0) == [], f"{v}: unexpected {kind}"
        # no false LOS: only where the data itself has a vehicle-time hole (see data_stale_gaps)
        assert len(events_for(v, "LOS", since=t0)) == data_stale_gaps(v, t0), f"{v}: false LOS"
    for svc in INGEST:
        assert "stole" not in logs_since(svc, t0), f"{svc} stole a vehicle after the outage"
