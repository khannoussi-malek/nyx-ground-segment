"""ATP-04 backend half, ING-04: a simulated dropout yields one GAP, one LOS, one AOS (D-ING-21)."""

from gs.ingest.link import STALE_S
from gs.simulator.frames import PERIOD_S
from tests.support.env import now, wait_until
from tests.support.ingest import wait_drained
from tests.support.pg import events_for
from tests.support.sim import delete_fault, post_fault

V, OTHER = "NYX-TA1", "NYX-TA2"
DROPOUT_S = 10
SLACK_S = 10.0  # LOS detection, the first frame back, polling
LOST = round(DROPOUT_S / PERIOD_S)  # the sim advances seq without sending during a dropout
TOLERANCE = 1  # the tracker's seq count and the sim's tick grid may differ by one frame


def test_dropout_gap_los_aos() -> None:
    t0 = now()
    assert post_fault(V, "dropout", duration_s=DROPOUT_S) == 204
    try:
        wait_until(lambda: bool(events_for(V, "AOS", since=t0)), DROPOUT_S + STALE_S + SLACK_S)
    finally:
        delete_fault(V, "dropout")
    wait_drained((V, OTHER), 30)
    (gap,) = events_for(V, "GAP", since=t0)
    (los,) = events_for(V, "LOS", since=t0)
    (aos,) = events_for(V, "AOS", since=t0)
    assert los["id"] < aos["id"]  # LOS is raised before the AOS
    assert abs(gap["details"]["lost"] - LOST) <= TOLERANCE
    assert aos["details"]["lost_frames"] == gap["details"]["lost"]
    assert los["key"].removeprefix("LOS") == aos["key"].removeprefix("AOS")  # same epoch + last_seq
    assert events_for(OTHER, "GAP", since=t0) == []
    assert events_for(OTHER, "LOS", since=t0) == []
