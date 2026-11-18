import random
from datetime import timedelta

from gs.simulator.frames import make_frame
from tests.integration.test_frame_txn import put
from tests.support.env import now, until, vid
from tests.support.ingest import worker
from tests.support.pg import events_for, stored
from tests.support.redisx import xadd_raw


def put_bad(v: str, seqs: range) -> None:  # t_onboard 10 min ahead: DLQ t_onboard_range
    for q in seqs:
        xadd_raw(v, make_frame(v, q, now() + timedelta(minutes=10), random.Random(q)))


async def test_los_names_rejected_frames() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: stored(v) == 10, 5)
        put_bad(v, range(10, 13))
        await until(lambda: len(events_for(v, "LOS")) == 1, 6)
    (los,) = events_for(v, "LOS")
    assert los["details"]["rejected"] == {"count": 3, "reason": "t_onboard_range"}


async def test_plain_dropout_has_no_rejected() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: len(events_for(v, "LOS")) == 1, 6)
    assert "rejected" not in events_for(v, "LOS")[0]["details"]


async def test_rejected_count_resets_on_a_new_frame() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    put(v, range(10), base)
    async with worker([v]):
        await until(lambda: stored(v) == 10, 5)
        put_bad(v, range(10, 12))
        await until(lambda: len(events_for(v, "LOS")) == 1, 6)
        put(v, range(10, 20), base)  # AOS: new frames, the count restarts
        await until(lambda: len(events_for(v, "AOS")) == 1, 5)
        await until(lambda: len(events_for(v, "LOS")) == 2, 6)
    assert "rejected" not in events_for(v, "LOS")[1]["details"]
