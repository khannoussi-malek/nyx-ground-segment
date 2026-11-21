"""P1, P2 (design 11.5, D-EXT-14): the sequence tracker against the generator's true answer."""

from collections import defaultdict
from datetime import datetime

from hypothesis import given

from gs.ingest.events import GroundEvent
from gs.ingest.seq import Klass, SeqState, step
from tests.property import profiles
from tests.property.strategies import Delivery, Scenario, scenarios

V = "NYX-TA1"


def _run(sc: Scenario) -> list[tuple[Klass, tuple[GroundEvent, ...], Delivery]]:
    s: SeqState | None = None
    out: list[tuple[Klass, tuple[GroundEvent, ...], Delivery]] = []
    for d in sc.deliveries:
        s, klass, events = step(s, V, d.seq, d.t)
        out.append((klass, events, d))
    return out


@profiles.PROFILE
@given(scenarios())
def test_p1_each_frame_new_once(sc: Scenario) -> None:
    store: set[tuple[str, datetime, int]] = set()  # the (vehicle, t_onboard, seq) storage key
    new = 0
    for klass, _, d in _run(sc):
        key = (V, d.t, d.seq)
        fresh = key not in store
        store.add(key)
        assert (klass != "DUP_OR_LATE") == fresh, (klass, d)
        new += klass != "DUP_OR_LATE"
    assert new == len(store) == sc.distinct


@profiles.PROFILE
@given(scenarios())
def test_p2_gap_counts_equal_dropped(sc: Scenario) -> None:
    lost: dict[datetime, int] = defaultdict(int)
    kinds: dict[str, int] = defaultdict(int)
    for _, events, _ in _run(sc):
        for e in events:
            kinds[e.kind] += 1
            if e.kind == "GAP":
                lost[e.details["epoch"]] += e.details["lost"]
    for start, dropped in zip(sc.epoch_starts, sc.dropped, strict=True):
        assert lost.get(start, 0) == dropped
    assert sum(lost.values()) == sum(sc.dropped)
    assert kinds["SEQ_JUMP"] == sc.jumps
    assert kinds["SEQ_RESET"] == sc.restarts
    assert kinds["SEQ_ANOMALY"] == 0
