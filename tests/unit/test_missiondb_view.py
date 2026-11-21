"""The /api/mission-db view lists derived parameters so the console can label them (EXT-01 I1)."""

from gs.common.interfaces.missiondb import MissionDbView
from gs.common.missiondb import load_dir
from tests.support.mission import MISSION_DIR, files, ld


def test_view_lists_power_w_with_its_unit() -> None:
    v = MissionDbView.of(load_dir(MISSION_DIR))
    assert v.derived["power_w"].unit == "W"
    assert "power_w" not in v.parameters  # raw parameters stay raw


def test_view_keeps_a_derived_description() -> None:
    f = files()
    f["parameters"] += (
        b"derived:\n  p: {unit: W, description: Power, expr: bus_voltage_v * bus_current_a}\n"
    )
    assert MissionDbView.of(ld(f)).derived["p"].description == "Power"


def test_view_carries_the_mode_sets() -> None:
    lim = MissionDbView.of(load_dir(MISSION_DIR)).limits["cabin_pressure_kpa"]
    assert [c.type for c in lim.checks_by_mode["SAFE"]] == ["limit"]
    assert lim.checks_by_mode["SAFE"][0].model_dump()["red_low"] == 90
