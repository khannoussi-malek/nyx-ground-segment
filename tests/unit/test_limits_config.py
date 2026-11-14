import math

import pytest

from gs.alarms.checks.limit import LimitCheck, deadband, floor, meets_floor
from gs.common.missiondb import MissionDb, MissionDbError, load_dir
from tests.support.alarms import shipped
from tests.support.mission import MISSION_DIR, files, ld


def limits_edit(old: str, new: str) -> bytes:
    lim = files()["limits"]
    assert old.encode() in lim
    return lim.replace(old.encode(), new.encode(), 1)


def load_with(limits: bytes) -> MissionDb:
    return ld(files(limits=limits))


def shipped_limit(param: str) -> LimitCheck:
    cfg = shipped(param, "limit")
    assert isinstance(cfg, LimitCheck)
    return cfg


def test_deadband_below_floor_rejected() -> None:  # cabin pressure at 10 %: 1.1 < 1.5 x 1.06
    with pytest.raises(MissionDbError, match="cabin_pressure_kpa: deadband 1.1 below floor 1.59"):
        load_with(limits_edit("hysteresis_pct: 15", "hysteresis_pct: 10"))


def test_one_sided_below_floor_rejected() -> None:  # 0.14 < 1.5 x 1 % of 10 A
    with pytest.raises(MissionDbError, match="bus_current_a: deadband 0.14 below floor 0.15") as e:
        load_with(limits_edit("hysteresis_abs: 0.2", "hysteresis_abs: 0.14"))
    assert e.value.file == "limits.yaml"


def test_inherited_default_below_floor_rejected() -> None:  # 2 % of 11 = 0.22 < 1.59
    with pytest.raises(MissionDbError, match="cabin_pressure_kpa: deadband"):
        load_with(limits_edit(", hysteresis_pct: 15", ""))


def test_shipped_set_meets_floor_exactly() -> None:  # Review Focus 1
    db = load_dir(MISSION_DIR)
    for p in ("cabin_temp_c", "bus_voltage_v"):
        cfg = db.limits[p].checks[0]
        assert isinstance(cfg, LimitCheck)
        assert math.isclose(deadband(cfg), floor(cfg), rel_tol=1e-9)


def test_floor_values() -> None:
    assert floor(shipped_limit("cabin_pressure_kpa")) == pytest.approx(1.59)
    assert floor(shipped_limit("bus_current_a")) == pytest.approx(0.15)  # one-sided: |limit| only
    assert floor(shipped_limit("rate_x_dps")) == pytest.approx(0.15)  # 1 % of span 10 > 1 % of 5
    # one-sided bounds crossing zero: span 15 is ignored (0.225 if it counted), |limit| 10 rules
    crossing = LimitCheck(type="limit", yellow_high=-10.0, red_high=5.0, hysteresis_abs=1.0)
    assert floor(crossing) == pytest.approx(0.15)


def test_span_of_two_sided_check_missing_a_red_bound() -> None:
    # extends D-ALM-02's span (red_high - red_low) to the outermost bounds (ruling G10)
    cfg = LimitCheck(
        type="limit", red_low=95.0, yellow_low=98.0, yellow_high=104.0, hysteresis_pct=10.0
    )
    assert deadband(cfg) == pytest.approx(0.9)  # 10 % of 104 - 95
    assert floor(cfg) == pytest.approx(1.56)  # 1.5 x 1 % of |104|
    with pytest.raises(MissionDbError, match="cabin_pressure_kpa: deadband 0.9 below floor 1.56"):
        load_with(limits_edit("red_high: 106.0, hysteresis_pct: 15", "hysteresis_pct: 10"))


def test_deadband_values() -> None:
    assert deadband(shipped_limit("cabin_pressure_kpa")) == pytest.approx(1.65)
    assert deadband(shipped_limit("bus_current_a")) == 0.2
    unresolved = LimitCheck(type="limit", red_low=1.0, red_high=2.0)
    with pytest.raises(ValueError, match="hysteresis not resolved"):
        deadband(unresolved)


def test_floor_tolerance_is_last_bits_only() -> None:  # Review Focus 1, independent of formula
    f = floor(shipped_limit("bus_current_a"))

    def cfg(d: float) -> LimitCheck:
        return LimitCheck(type="limit", yellow_high=8.0, red_high=10.0, hysteresis_abs=d)

    assert meets_floor(cfg(math.nextafter(f, 0.0)))  # one ulp below: float noise, accepted
    assert not meets_floor(cfg(f * (1 - 1e-6)))
