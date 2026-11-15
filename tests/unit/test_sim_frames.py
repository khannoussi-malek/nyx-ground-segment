import json
import random
import statistics
from datetime import UTC, datetime
from uuid import UUID

import pytest

from gs.simulator.frames import NOMINALS, build_frame, make_frame, next_seq
from tests.support.sim import run_ticks_with_fake_clock

T = datetime(2026, 11, 14, 10, tzinfo=UTC)


def test_seq_wraps() -> None:
    assert next_seq(2**32 - 1) == 0
    assert next_seq(4) == 5


def test_frame_shape() -> None:
    d = json.loads(make_frame("NYX-TA1", 7, T, random.Random("1:NYX-TA1")))
    assert set(d) == {"vehicle", "seq", "t_onboard", "mode", "last_cmd_id", *NOMINALS}
    got = (d["seq"], d["t_onboard"], d["mode"], d["last_cmd_id"])
    assert got == (7, "2026-11-14T10:00:00.000000Z", "TEST", None)


def test_last_cmd_id_is_carried() -> None:
    cid = UUID("11111111-1111-4111-8111-111111111111")
    assert json.loads(build_frame("NYX-TA1", 1, T, {}, cid))["last_cmd_id"] == str(cid)
    assert json.loads(build_frame("NYX-TA1", 1, T, {}))["last_cmd_id"] is None


def test_same_seed_same_values() -> None:
    a = make_frame("NYX-TA1", 1, T, random.Random("1:NYX-TA1"))
    b = make_frame("NYX-TA1", 1, T, random.Random("1:NYX-TA1"))
    c = make_frame("NYX-TA1", 1, T, random.Random("2:NYX-TA1"))
    assert a == b and a != c


def test_noise_sigma() -> None:
    rng = random.Random("1:NYX-TA1")
    xs = [json.loads(make_frame("NYX-TA1", i, T, rng))["cabin_pressure_kpa"] for i in range(2000)]
    assert statistics.stdev(xs) == pytest.approx(0.02, abs=0.003)
    assert statistics.mean(xs) == pytest.approx(101.3, abs=0.005)


async def test_absolute_schedule_catches_up_after_stall() -> None:  # D-SIM-01
    stamps, sleeps, seqs = await run_ticks_with_fake_clock(n=10, stall_after_tick=3, stall_s=0.35)
    # ticks 4-6 were due at .4/.5/.6 but fire late (.65); tick 7 is back on the 0.1 s grid
    assert stamps == pytest.approx([0, 0.1, 0.2, 0.3, 0.65, 0.65, 0.65, 0.7, 0.8, 0.9])
    assert sleeps[4:7] == [0, 0, 0] and seqs == list(range(10))
