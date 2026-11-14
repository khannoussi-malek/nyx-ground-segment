"""tick_loop feeds the active faults into the emitted values (fake clock, 10 Hz grid)."""

import json

import pytest

from gs.simulator.faults import FaultBoard
from tests.support.sim import at_tick, fault_req, tick_entries

N = 11


def values(raws: list[str], key: str) -> list[float]:
    return [json.loads(r)[key] for r in raws]


def baseline(key: str) -> list[float]:
    return values(tick_entries(N), key)


def test_leak_lowers_pressure_at_0_4_per_s() -> None:
    got = values(
        tick_entries(N, at_tick(0, fault_req("leak"))),
        "cabin_pressure_kpa",
    )
    for k, (g, base) in enumerate(zip(got, baseline("cabin_pressure_kpa"), strict=True)):
        assert g == pytest.approx(base - 0.4 * 0.1 * k)


def test_overheat_raises_temp_at_0_5_per_s() -> None:
    got = values(tick_entries(N, at_tick(0, fault_req("overheat"))), "cabin_temp_c")
    for k, (g, base) in enumerate(zip(got, baseline("cabin_temp_c"), strict=True)):
        assert g == pytest.approx(base + 0.5 * 0.1 * k)


def test_step_forces_exact_value_for_its_duration_only() -> None:
    step = fault_req("step", param="bus_voltage_v", value=31.5, duration_s=0.55)
    got = values(tick_entries(N, at_tick(0, step)), "bus_voltage_v")
    assert got[:6] == [31.5] * 6  # ticks 0..5, no noise
    assert got[6:] == baseline("bus_voltage_v")[6:]  # expired, nominal noise again


def test_freeze_repeats_the_frame_before_it_started() -> None:
    raws = tick_entries(N, at_tick(5, fault_req("freeze")))
    frames = [json.loads(r) for r in raws]
    keys = {k for k in frames[0] if k.endswith(("_kpa", "_c", "_v", "_a", "_bar", "_dps"))}
    assert {k: frames[5][k] for k in keys} == {k: frames[4][k] for k in keys}
    for f in frames[5:]:
        assert {k: f[k] for k in keys} == {k: frames[4][k] for k in keys}
    assert [f["seq"] for f in frames] == list(range(N))  # seq and time keep advancing
    assert len({f["t_onboard"] for f in frames}) == N
    assert frames[4]["cabin_temp_c"] != frames[3]["cabin_temp_c"]  # not frozen before


def test_freeze_wins_over_leak_and_step() -> None:
    def act(k: int, b: FaultBoard, m: float) -> None:
        if k == 0:
            b.add(fault_req("leak"), m)
        if k == 5:
            b.add(fault_req("freeze"), m)
            b.add(fault_req("step", param="bus_voltage_v", value=99, duration_s=5), m)

    frames = [json.loads(r) for r in tick_entries(N, act)]
    for f in frames[5:]:  # leak offset of frame 4 stays, the step never shows
        assert f["cabin_pressure_kpa"] == frames[4]["cabin_pressure_kpa"]
        assert f["bus_voltage_v"] == frames[4]["bus_voltage_v"] != 99


def test_freeze_before_the_first_tick_freezes_from_the_first_frame() -> None:
    frames = [json.loads(r) for r in tick_entries(5, at_tick(0, fault_req("freeze")))]
    assert len({f["cabin_temp_c"] for f in frames}) == 1
    assert frames[0]["cabin_temp_c"] == baseline("cabin_temp_c")[0]  # the first frame's own values
    assert [f["seq"] for f in frames] == list(range(5))


def test_one_shots_during_dropout_are_consumed_and_send_nothing() -> None:
    def act(k: int, b: FaultBoard, m: float) -> None:
        if k == 2:
            b.add(fault_req("dropout", duration_s=0.35), m)  # ticks 2..5
        if k == 3:
            for f in ("duplicate", "malformed", "seq_jump"):
                b.add(fault_req(f), m)

    raws = tick_entries(8, act)
    frames = [json.loads(r) for r in raws]  # all valid JSON: no malformed entry leaked
    assert [f["seq"] for f in frames] == [0, 1, 1006, 1007]  # jump consumed, nothing duplicated
