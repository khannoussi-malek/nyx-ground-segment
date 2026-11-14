"""ING-08: a frame that kills its worker on every delivery reaches the DLQ on delivery 4."""

import json
import time

import pytest

from gs.common.interfaces import DlqRecord
from tests.support.compose import recreate
from tests.support.env import now, wait_until
from tests.support.ingest import FAST_ENV, latest_seq, wait_drained
from tests.support.pg import gapless, seqs_since, stored
from tests.support.redisx import dlq_since

V, OTHER = "NYX-TA1", "NYX-TA2"
SAMPLE_S = 3.0  # > one 10 Hz batch period and the BLOCK: a flowing vehicle always grows


def poison_record(since: object, seq: int) -> DlqRecord | None:
    for raw in dlq_since(f"tm:{V}", since):  # type: ignore[arg-type]
        rec = DlqRecord.model_validate_json(raw)
        if json.loads(rec.raw).get("seq") == seq:
            return rec
    return None


@pytest.mark.usefixtures("fast_ingest")
def test_crash_every_time_frame_to_dlq() -> None:  # CRASH_AT=frame_seq:<v>:<n>, D-ENG-16
    t0 = now()
    n = latest_seq(V) + 100  # ~10 s ahead at 10 Hz: both workers are recreated by then
    recreate(["ingest-1", "ingest-2"], {**FAST_ENV, "CRASH_AT": f"frame_seq:{V}:{n}"}, wait=False)
    a2 = stored(OTHER)
    try:
        samples = [a2]
        while poison_record(t0, n) is None:  # NYX-TA2 must keep flowing during the crash loop
            assert len(samples) < 120 // SAMPLE_S, "frame never reached the DLQ"
            time.sleep(SAMPLE_S)
            samples.append(stored(OTHER))
        rec = poison_record(t0, n)
        assert rec is not None and (rec.reason, rec.deliveries) == ("poison", 4)
        assert len(samples) >= 3 and all(b > a for a, b in zip(samples, samples[1:], strict=False))
        wait_until(lambda: stored(OTHER) > a2 + 100, 30)
    finally:
        recreate(["ingest-1", "ingest-2"], FAST_ENV)
    wait_drained((V,), 30)
    stored_seqs = set(seqs_since(V, t0))
    around = [s for s in range(n - 30, n + 31) if s not in stored_seqs]
    assert around == [n]  # only the poison frame is missing
    assert len(dlq_since(f"tm:{V}", t0)) == 1  # no healthy neighbour DLQ'd
    wait_drained((OTHER,), 30)
    assert gapless(seqs_since(OTHER, t0)) and dlq_since(f"tm:{OTHER}", t0) == []
