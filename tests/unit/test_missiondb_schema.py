import hashlib
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from gs.common.missiondb import (
    NAME_RE,
    RESERVED_NAMES,
    LimitCheck,
    LimitEntry,
    MissionDbError,
    NumArg,
    RocCheck,
    load_dir,
)
from tests.support.mission import MISSION_DIR, files, ld

PARAMS = (
    "cabin_pressure_kpa", "cabin_temp_c", "bus_voltage_v", "bus_current_a",
    "tank_pressure_bar", "rate_x_dps", "rate_y_dps", "rate_z_dps",
)  # fmt: skip
SHIPPED_P = files()["parameters"]
SHIPPED_L = files()["limits"]
SHIPPED_C = files()["commands"]


def rejected(file: str, **over: bytes) -> MissionDbError:
    with pytest.raises(MissionDbError) as e:
        ld(files(**over))
    assert e.value.file == file
    return e.value


def test_shipped_set_loads() -> None:
    db = load_dir(MISSION_DIR)
    assert db.param_names == PARAMS and set(db.commands) == {"isolate_valve", "vent_cabin"}
    vent = db.commands["vent_cabin"]
    duration = vent.args["duration_s"]
    assert vent.hazardous and isinstance(duration, NumArg) and duration.max == 10
    lim, roc = db.limits["cabin_pressure_kpa"].checks
    assert isinstance(lim, LimitCheck) and isinstance(roc, RocCheck)
    assert (lim.red_low, lim.yellow_low, lim.yellow_high, lim.red_high, lim.hysteresis_pct) == (
        95,
        98,
        104,
        106,
        15,
    )
    assert (roc.limit_per_s, roc.window_s, roc.direction, roc.severity) == (
        0.15,
        1.0,
        "falling",
        "YELLOW",
    )


def test_two_sided_inherits_default_hysteresis() -> None:
    (chk,) = load_dir(MISSION_DIR).limits["rate_x_dps"].checks
    assert isinstance(chk, LimitCheck)
    assert chk.hysteresis_pct == 2.0 and chk.persistence == 3


def test_hashes_match_sha256sum_formula() -> None:  # D-EXT-02
    lim, cmd, par = (
        hashlib.sha256((MISSION_DIR / f"{n}.yaml").read_bytes()).hexdigest()
        for n in ("limits", "commands", "parameters")
    )
    db = load_dir(MISSION_DIR)
    assert (db.limits_sha256, db.commands_sha256, db.parameters_sha256) == (lim, cmd, par)
    assert db.set_sha256 == hashlib.sha256(f"{lim}\n{cmd}\n{par}\n".encode()).hexdigest()


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(MissionDbError) as e:
        load_dir(tmp_path)
    assert e.value.file == "limits.yaml"


def test_duplicate_key_rejected() -> None:  # EX-05
    err = rejected(
        "parameters.yaml", parameters=SHIPPED_P + b"  cabin_temp_c:\n    unit: K\n    type: float\n"
    )
    assert "duplicate" in err.message


def test_alias_rejected() -> None:
    rejected("limits.yaml", limits=b"version: 1\nx: &a {k: 1}\nlimits: *a\n")


def test_anchor_without_alias_rejected() -> None:
    err = rejected(
        "commands.yaml", commands=SHIPPED_C.replace(b"hazardous: false", b"hazardous: &h false")
    )
    assert "anchor" in err.message


def test_file_over_256kb_rejected() -> None:  # EX-05
    rejected("commands.yaml", commands=SHIPPED_C + b"#" * 256 * 1024)


def test_malformed_yaml_and_non_mapping_rejected() -> None:
    rejected("limits.yaml", limits=b"a: [unclosed\n")
    rejected("limits.yaml", limits=b"- 1\n- 2\n")
    rejected("limits.yaml", limits=b"\xff\xfe\x00bad")


@pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
def test_reserved_name_rejected(name: str) -> None:  # EX-06
    err = rejected(
        "parameters.yaml", parameters=SHIPPED_P + f"  {name}: {{unit: x, type: float}}\n".encode()
    )
    assert "reserved" in err.message


def test_reserved_names_cover_every_basemodel_attribute() -> None:
    clashing = {n for n in dir(BaseModel) if re.fullmatch(NAME_RE, n)}
    assert {"model_dump", "json", "copy", "dict", "schema", "validate"} <= clashing
    assert clashing <= RESERVED_NAMES


@pytest.mark.parametrize("name", ["Cabin", "1x", "a" * 64, "x-y"])
def test_bad_name_rejected(name: str) -> None:
    rejected(
        "parameters.yaml", parameters=SHIPPED_P + f"  {name}: {{unit: x, type: float}}\n".encode()
    )


def test_bad_command_name_rejected() -> None:
    rejected(
        "commands.yaml",
        commands=SHIPPED_C + b"  Bad-Name:\n    description: d\n    hazardous: false\n",
    )


def test_derived_rejected() -> None:
    err = rejected(
        "parameters.yaml", parameters=SHIPPED_P + b"derived:\n  p: {unit: W, expr: a * b}\n"
    )
    assert "derived" in err.message


def test_calibration_rejected() -> None:
    bad = SHIPPED_P.replace(
        b"bus_current_a: {", b"bus_current_a: {calibration: {table: [[0, 0], [1, 1]]}, ", 1
    )
    assert "calibration" in rejected("parameters.yaml", parameters=bad).message


def test_allowed_modes_rejected() -> None:
    bad = SHIPPED_C.replace(
        b"    hazardous: false\n", b"    hazardous: false\n    allowed_modes: [TEST]\n"
    )
    assert "allowed_modes" in rejected("commands.yaml", commands=bad).message


def test_checks_by_mode_rejected() -> None:
    bad = SHIPPED_L.replace(
        b"  bus_current_a: {checks:",
        b"  bus_current_a: {checks_by_mode: {SAFE: []}, checks:",
    )
    assert "checks_by_mode" in rejected("limits.yaml", limits=bad).message


def test_dangling_limits_key_rejected() -> None:
    err = rejected(
        "limits.yaml",
        limits=SHIPPED_L
        + b"  nope: {checks: [{type: limit, yellow_high: 1.0, red_high: 2.0,"
        + b" hysteresis_abs: 0.1}]}\n",
    )
    assert "nope" in err.message


def test_section_must_be_last_key() -> None:
    rejected("limits.yaml", limits=SHIPPED_L + b"defaults: {persistence: 3}\n")


def test_band_order_rejected() -> None:  # D-ALM-01
    bad = SHIPPED_L.replace(
        b"yellow_low: 18.0, yellow_high: 27.0", b"yellow_low: 99.0, yellow_high: 27.0"
    )
    assert "bounds" in rejected("limits.yaml", limits=bad).message


def test_one_sided_requires_hysteresis_abs() -> None:
    bad = SHIPPED_L.replace(b"hysteresis_abs: 0.2", b"hysteresis_pct: 2")
    assert "hysteresis_abs" in rejected("limits.yaml", limits=bad).message


def test_both_hysteresis_rejected() -> None:
    bad = SHIPPED_L.replace(b"hysteresis_abs: 0.2", b"hysteresis_abs: 0.2, hysteresis_pct: 2")
    rejected("limits.yaml", limits=bad)


def test_one_check_per_type() -> None:  # D-EXT-03 slot key
    dup = b"{type: limit, yellow_high: 1.0, red_high: 2.0, hysteresis_abs: 0.1}"
    bad = SHIPPED_L.replace(
        b"  bus_current_a: {checks: [", b"  bus_current_a: {checks: [" + dup + b", "
    )
    assert "one check per type" in rejected("limits.yaml", limits=bad).message


def test_hazard_required_when_hazardous() -> None:
    bad = SHIPPED_C.replace(b"    hazard: Irreversible loss of cabin atmosphere\n", b"")
    assert "hazard" in rejected("commands.yaml", commands=bad).message


@pytest.mark.parametrize(("field", "value"), [("receive_timeout_s", 6), ("verify_timeout_s", 16)])
def test_timeout_longer_than_default_rejected(field: str, value: int) -> None:  # D-EXT-07
    bad = SHIPPED_C.replace(
        b"    hazardous: false\n", f"    hazardous: false\n    {field}: {value}\n".encode()
    )
    rejected("commands.yaml", commands=bad)


def test_shorter_timeout_override_accepted() -> None:
    ok = SHIPPED_C.replace(
        b"    hazardous: false\n", b"    hazardous: false\n    receive_timeout_s: 2\n"
    )
    assert ld(files(commands=ok)).commands["isolate_valve"].receive_timeout_s == 2


def test_unhashable_mapping_key_rejected() -> None:
    err = rejected("limits.yaml", limits=b"version: 1\n? [a]\n: 1\nlimits: {}\n")
    assert "unhashable" in err.message


def test_deeply_nested_yaml_rejected() -> None:
    rejected("limits.yaml", limits=b"version: 1\nlimits: " + b"[" * 5_000 + b"]" * 5_000)


def test_limit_entry_dump_round_trips() -> None:  # G21: a dump kept only the CheckConfig base
    for name, entry in load_dir(MISSION_DIR).limits.items():
        dumped = entry.model_dump()
        assert LimitEntry.model_validate(dumped) == entry, name
    lim, roc = load_dir(MISSION_DIR).limits["cabin_pressure_kpa"].model_dump()["checks"]
    assert (lim["red_low"], lim["red_high"], roc["limit_per_s"]) == (95.0, 106.0, 0.15)
