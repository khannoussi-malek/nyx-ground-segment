import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis.exceptions

from gs.simulator import backfill as bf
from gs.simulator.backfill import BACKFILL_VEHICLE, FaultAt, backfill, parse_fault
from gs.simulator.buffer import BUFFER_MAX, FrameBuffer

NOW = datetime.now(UTC)
F = (NOW - timedelta(days=2)).replace(microsecond=0)
Z = "%Y-%m-%dT%H:%M:%SZ"
HOUR1 = timedelta(hours=1)


class FakeRedis:
    """What xadd_tm needs: execute_command("XADD", stream, ..., "data", payload)."""

    def __init__(self, delay: float = 0.0) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []
        self.delay = delay
        self.stalled = False  # a consumer that reads nothing: the lag is every entry
        self.max_lag = 0

    def lag(self) -> int:
        lag = len(self.entries) if self.stalled else 0
        self.max_lag = max(self.max_lag, lag)
        return lag

    async def execute_command(self, *args: Any) -> Any:
        if args[0] == "XINFO":  # the ingest group's lag on the stream
            if not self.entries:
                raise redis.exceptions.ResponseError("no such key")  # as before the first XADD
            return [{"name": "ingest", "lag": self.lag()}]
        if self.delay:
            await asyncio.sleep(self.delay)
        self.entries.append((str(args[1]), json.loads(args[-1])))
        return "0-1"


def test_parse_fault() -> None:
    a = parse_fault("leak@600", "NYX-TA9")
    assert a == FaultAt(600.0, a.request) and a.request.fault == "leak"
    assert a.request.vehicle == "NYX-TA9" and a.request.params == {}
    b = parse_fault('dropout@1200.5:{"duration_s":10}', "NYX-TA9")
    assert b.offset_s == 1200.5 and b.request.params == {"duration_s": 10.0}
    for bad in (
        "leak", "leak@x", "leak@-1", "leak@nan", "nope@1", 'dropout@1:{"duration_s":', "dropout@1",
        'step@1:{"param":"cabin_pressure_kpa","value":NaN,"duration_s":5}',  # NaN never enters
        'leak@1:{"duration_s":Infinity}',
    ):  # fmt: skip
        with pytest.raises(ValueError):
            parse_fault(bad, "NYX-TA9")


def test_cli_rejects_other_vehicles(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--vehicle", "NYX-TA1", "--from", F.strftime(Z), "--to", (F + HOUR1).strftime(Z)]
    assert bf.main(argv) == 2
    assert "backfill writes only NYX-TA9 (D-ENG-16)" in capsys.readouterr().err
    assert BACKFILL_VEHICLE == "NYX-TA9"


def test_cli_rejects_a_bad_window(capsys: pytest.CaptureFixture[str]) -> None:
    old = (NOW - timedelta(days=8)).strftime(Z)
    assert bf.main(["--vehicle", "NYX-TA9", "--from", old, "--to", F.strftime(Z)]) == 2
    assert bf.main(["--vehicle", "NYX-TA9", "--from", "2026-01-01T00:00:00", "--to", "x"]) == 2
    assert "error" in capsys.readouterr().err


async def test_window_outside_validity_rejected() -> None:
    r = FakeRedis()
    ok_from = NOW - timedelta(days=7) + bf.VALIDITY_MARGIN
    for lo, hi in [
        (ok_from - timedelta(seconds=1), ok_from + HOUR1),  # older than now - 7 d + 1 h
        (datetime.now(UTC) - HOUR1, datetime.now(UTC) + timedelta(minutes=5)),  # in the future
        (F, F),  # empty
        (F + HOUR1, F),  # reversed
        (datetime(2026, 1, 1), F),  # naive
    ]:
        with pytest.raises(ValueError):
            await backfill(r, "NYX-TA9", lo, hi, "s")  # type: ignore[arg-type]
    assert r.entries == []


async def test_virtual_clock_grid_and_seed() -> None:
    async def run(seed: str) -> list[tuple[str, dict[str, Any]]]:
        r = FakeRedis()
        n = await backfill(r, "NYX-TA9", F, F + timedelta(seconds=30), seed)  # type: ignore[arg-type]
        assert n == len(r.entries) == 300
        return r.entries

    a, b, c = await run("s1"), await run("s1"), await run("s2")
    assert a == b and a != c  # same seed, same frames
    assert {s for s, _ in a} == {"tm:NYX-TA9"}
    frames = [f for _, f in a]
    assert [f["seq"] for f in frames] == list(range(300))
    want = [
        (F + timedelta(milliseconds=100 * i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ") for i in range(300)
    ]
    assert [f["t_onboard"] for f in frames] == want  # the 10 Hz grid from t_from exactly
    assert all(f["vehicle"] == "NYX-TA9" for f in frames)


async def test_fault_starts_at_its_offset() -> None:
    async def pressures(faults: list[FaultAt]) -> list[float]:
        r = FakeRedis()
        await backfill(r, "NYX-TA9", F, F + timedelta(seconds=30), "s", faults=faults)  # type: ignore[arg-type]
        return [f["cabin_pressure_kpa"] for _, f in r.entries]

    clean = await pressures([])
    leak = await pressures([parse_fault("leak@10", "NYX-TA9")])  # same seed: same noise
    diff = [a - b for a, b in zip(leak, clean, strict=True)]
    assert diff[:100] == [0.0] * 100  # nothing before the offset
    assert diff[299] == pytest.approx(-0.4 * (29.9 - 10), abs=1e-6)  # drift measured from it


async def test_pause_waits_for_resume() -> None:
    r = FakeRedis()
    seen: list[int] = []
    sent_at_resume: list[int] = []

    async def resume(n: int) -> None:
        seen.append(n)
        sent_at_resume.append(len(r.entries))
        await asyncio.sleep(0.05)
        assert len(r.entries) == 50  # nothing was sent while paused

    n = await backfill(
        r,  # type: ignore[arg-type]
        "NYX-TA9", F, F + timedelta(seconds=10), "s", pause_at=[50], resume=resume,
    )  # fmt: skip
    assert seen == [50] and sent_at_resume == [50] and n == 100


async def test_back_pressure_never_overflows(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[FrameBuffer] = []

    class Spy(FrameBuffer):
        def __init__(self) -> None:
            super().__init__()
            made.append(self)

    monkeypatch.setattr(bf, "FrameBuffer", Spy)
    r = FakeRedis(delay=0.0002)  # slower than the virtual clock
    frames = BUFFER_MAX + 1500  # more than the buffer holds
    n = await backfill(r, "NYX-TA9", F, F + timedelta(seconds=frames / 10), "s")  # type: ignore[arg-type]
    assert n == frames and made[0].overflow == 0
    assert [f["seq"] for _, f in r.entries] == list(range(frames))


async def test_backfill_pauses_while_the_consumer_lags_then_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bf, "LAG_MAX", 100)
    monkeypatch.setattr(bf, "LAG_CHECK_TICKS", 20)
    monkeypatch.setattr(bf, "LAG_POLL_S", 0.01)
    r = FakeRedis()
    r.stalled = True
    task = asyncio.create_task(
        backfill(r, "NYX-TA9", F, F + timedelta(seconds=100), "s")  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.5)
    plateau = len(r.entries)
    assert not task.done() and 100 <= plateau < 1000  # held back near LAG_MAX, not 1000 frames
    await asyncio.sleep(0.3)
    assert len(r.entries) == plateau  # still paused: nothing more is sent
    r.stalled = False  # the consumer catches up
    assert await asyncio.wait_for(task, 10) == 1000
    assert [f["seq"] for _, f in r.entries] == list(range(1000))
