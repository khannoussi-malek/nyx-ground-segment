"""perf.soak_count against the real DB and Redis (ING-01): frames go through a real ingest worker,
the HTTP and the simulator edges are fakes."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
import redis

from perf.soak_count import main, sent_frames, stored_frames
from tests.support.env import until, vid
from tests.support.ingest import worker
from tests.support.pg import admin_dsn, app_dsn, stored
from tests.support.redisx import client, drained, frame, time_base, xadd_raw
from tests.unit.test_soak_count import FakeApi


class SoakApi(FakeApi):
    def __init__(self, window: tuple[str, str]) -> None:
        super().__init__()
        self.window = window

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> bytes:
        if path.endswith("/stop"):
            return json.dumps({"started_at": self.window[0], "ended_at": self.window[1]}).encode()
        return super().call(method, path, body)

    def csv_when_frozen(self, session_id: int) -> str:
        return "section,key\n"


async def _seed(v: str, restart: bool = False) -> tuple[datetime, datetime, datetime]:
    """seq 0..29, then ten seconds later seq 30..49 (restart: a reset to 0..19); all ingested."""
    b1 = time_base()
    b2 = b1 + timedelta(seconds=10)
    for seq in range(30):
        xadd_raw(v, frame(v, seq, b1))
    for seq in range(0 if restart else 30, 20 if restart else 50):
        xadd_raw(v, frame(v, seq, b2))
    async with worker([v]):
        await until(lambda: stored(v) == 50 and drained(v), 10)
    return b1, b2, b2 + timedelta(seconds=30)


def _ingest_redis() -> redis.Redis:
    return client("admin")


async def test_window_bounds_and_the_two_sides_agree() -> None:
    v = vid()
    b1, _, end = await _seed(v)
    with psycopg.connect(app_dsn("gs")) as db:
        start = b1 - timedelta(seconds=5)
        sent, held = sent_frames(_ingest_redis(), v, start, end), stored_frames(db, v, start, end)
        assert len(sent) == 50 and set(sent) == held
        # [start, end) is half open: seq 0..9 are before b1 + 1 s, seq 10 (exactly 1 s) is not
        cut = b1 + timedelta(seconds=1)
        assert {s for _, s in sent_frames(_ingest_redis(), v, start, cut)} == set(range(10))
        assert {s for _, s in stored_frames(db, v, start, cut)} == set(range(10))


def _run(v: str, b1: datetime, end: datetime, out: Path, overflow: list[int] | None = None) -> None:
    api = SoakApi(((b1 - timedelta(seconds=5)).isoformat(), end.isoformat()))
    counter = iter(overflow or [0, 0])  # read at session start and at stop
    main(0, api=api, vehicles=(v,), out=out, wait=lambda: None, overflow=lambda: next(counter))


async def test_an_xdeleted_stream_entry_fails_the_count(tmp_path: Path) -> None:  # G4-21
    v = vid()
    r = _ingest_redis()
    old_ms = int((time_base() - timedelta(minutes=10)).timestamp() * 1000)
    # an older head: the stream is not "trimmed"
    r.xadd(f"tm:{v}", {"data": "old"}, id=f"{old_ms}-0")
    b1, _, end = await _seed(v)
    second = cast(list[Any], r.xrange(f"tm:{v}", count=3))[1]
    r.xdel(f"tm:{v}", second[0])
    with pytest.raises(RuntimeError, match=r"1 stored frames are missing from tm:"):
        _run(v, b1, end, tmp_path)


async def test_main_passes_on_a_complete_run(tmp_path: Path) -> None:
    v = vid()
    b1, _, end = await _seed(v)
    _run(v, b1, end, tmp_path)
    text = (tmp_path / "summary.md").read_text()
    assert "**PASS**" in text and f"| {v} | 2 | 50 | 0 |" in text
    assert (tmp_path / f"report-{v}.csv").read_text() == "section,key\n"


async def test_main_fails_on_head_tail_and_reset_loss(tmp_path: Path) -> None:
    v = vid()
    b1, b2, end = await _seed(v, restart=True)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as db:
        for lo, hi, base in ((0, 3, b1), (27, 30, b1), (0, 5, b2)):  # head, tail, new boot's head
            for seq in range(lo, hi):
                db.execute(
                    "DELETE FROM telemetry WHERE vehicle = %s AND t_onboard = %s",
                    (v, base + timedelta(milliseconds=100 * seq)),
                )
    with pytest.raises(SystemExit):
        _run(v, b1, end, tmp_path)
    text = (tmp_path / "summary.md").read_text()
    # a restart outranks the loss: the run is INCOMPLETE, and the epoch / SEQ_RESET columns show it
    assert "**INCOMPLETE**" in text and f"| {v} | 2 | 39 | 11 | 0 | 2 | 1 |" in text


async def test_overflow_is_gated_on_the_delta_over_the_window(tmp_path: Path) -> None:
    v = vid()
    b1, _, end = await _seed(v)
    _run(v, b1, end, tmp_path, overflow=[7, 7])  # the simulator dropped 7 before the soak began
    assert "**PASS**" in (tmp_path / "summary.md").read_text()
    with pytest.raises(SystemExit):
        _run(v, b1, end, tmp_path, overflow=[7, 9])  # two dropped during the soak
    assert "**FAIL**" in (tmp_path / "summary.md").read_text()


async def test_a_trimmed_stream_head_fails_loudly_not_as_a_pass(tmp_path: Path) -> None:
    v = vid()
    b1, _, end = await _seed(v)
    client("admin").xtrim(f"tm:{v}", maxlen=10)  # the stream's head is gone
    with pytest.raises(RuntimeError, match="trimmed"):
        _run(v, b1, end, tmp_path)
