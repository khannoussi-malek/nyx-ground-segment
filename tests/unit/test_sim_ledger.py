"""The simulator ledger (D-EXT-15): every fault writes the right fate, one line per frame."""

import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gs.simulator.buffer import BUFFER_MAX, FrameBuffer
from gs.simulator.frames import tick_loop
from gs.simulator.ledger import Ledger
from gs.simulator.main import SimSettings, _ledger_path
from tests.support.mission import MISSION_DIR
from tests.support.sim import at_tick, fault_req, tick_pairs

N = 8
Line = tuple[int, str]  # (seq, fate)


def fates(n: int = N, *reqs: object) -> list[Line]:
    out: list[Line] = []

    def note(vehicle: str, seq: int, t: datetime, fate: str) -> None:
        assert vehicle == "NYX-TA1" and t.tzinfo is not None
        out.append((seq, fate))

    tick_pairs(n, at_tick(2, *reqs) if reqs else lambda *_: None, ledger=note)  # type: ignore[arg-type]
    return out


def test_clean_run_is_one_sent_line_per_frame() -> None:
    assert fates() == [(i, "sent") for i in range(N)]


def test_dropout_lines_keep_the_seq_running() -> None:
    got = fates(N, fault_req("dropout", duration_s=0.25))  # ticks 2, 3, 4
    assert got == [(i, "dropout" if 2 <= i <= 4 else "sent") for i in range(N)]


def test_duplicate_adds_a_line_per_copy() -> None:
    got = fates(N, fault_req("duplicate", count=2))
    assert got.count((2, "duplicate")) == 2 and got.count((2, "sent")) == 1
    assert [s for s, f in got if f == "sent"] == list(range(N))


@pytest.mark.parametrize("kind", ["json", "schema"])
def test_malformed_adds_one_line_and_the_frame_is_sent(kind: str) -> None:
    got = fates(N, fault_req("malformed", kind=kind))
    assert got.count((2, "malformed")) == 1 and (2, "sent") in got


def test_seq_jump_lines_carry_the_jumped_seq() -> None:
    got = fates(N, fault_req("seq_jump", by=1000))
    assert [s for s, _ in got] == [0, 1, *range(1002, 1008)]


def test_a_full_buffer_is_overflow() -> None:
    full = FrameBuffer()
    for _ in range(BUFFER_MAX):
        full.append("tm:x", "d")
    out: list[Line] = []
    stop, clock = asyncio.Event(), [0.0]

    async def sleep(d: float) -> None:
        clock[0] += d

    def now() -> datetime:
        stop.set()  # one tick
        return datetime(2026, 11, 14, tzinfo=UTC)

    asyncio.run(
        tick_loop(
            "NYX-TA1", full, random.Random("1"), stop, lambda: clock[0], sleep, now,
            ledger=lambda v, s, t, f: out.append((s, f)),
        )
    )  # fmt: skip
    assert out == [(0, "overflow")] and full.overflow == 1


def test_ledger_file_is_json_lines_and_appended_across_restarts(tmp_path: Path) -> None:
    path = tmp_path / "sim.jsonl"
    t = datetime.fromisoformat("2026-11-14T10:00:00.123456+00:00")
    for seq in (0, 1):  # two "runs" of the simulator
        led = Ledger(path)
        led("NYX-TA1", seq, t, "sent")
        led.close()
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines == [
        {"vehicle": "NYX-TA1", "seq": s, "t": "2026-11-14T10:00:00.123456Z", "fate": "sent"}
        for s in (0, 1)
    ]


def test_ledger_only_with_test_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_LEDGER", "/ledger/sim.jsonl")
    monkeypatch.setenv("GS_TEST_HOOKS", "0")
    assert _ledger_path() is None
    monkeypatch.setenv("GS_TEST_HOOKS", "1")
    assert _ledger_path() == Path("/ledger/sim.jsonl")
    monkeypatch.delenv("SIM_LEDGER")
    assert _ledger_path() is None
    assert SimSettings("r", ("NYX-TA1",), "1", "h", 1, MISSION_DIR).ledger is None
