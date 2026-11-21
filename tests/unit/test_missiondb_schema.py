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
    set_hashes,
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


def test_file_defaults_resolved_into_each_command() -> None:  # P-pf3
    shorter = SHIPPED_C.replace(
        b"defaults: {receive_timeout_s: 5, verify_timeout_s: 15}",
        b"defaults: {receive_timeout_s: 3, verify_timeout_s: 9}",
    ).replace(b"    hazardous: false\n", b"    hazardous: false\n    receive_timeout_s: 2\n")
    cmds = ld(files(commands=shorter)).commands
    assert (cmds["isolate_valve"].receive_timeout_s, cmds["isolate_valve"].verify_timeout_s) == (
        2, 9,
    )  # fmt: skip
    assert (cmds["vent_cabin"].receive_timeout_s, cmds["vent_cabin"].verify_timeout_s) == (3, 9)
    shipped = load_dir(MISSION_DIR).commands["vent_cabin"]
    assert (shipped.receive_timeout_s, shipped.verify_timeout_s) == (5, 15)


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


def test_set_hashes_pinned() -> None:  # the set_sha in LIMITS_REJECTED events must not change
    assert set_hashes(b"L", b"C", b"P") == (
        (
            "72dfcfb0c470ac255cde83fb8fe38de8a128188e03ea5ba5b2a93adbea1062fa",
            "6b23c0d5f35d1b11f9b683f0b0a617355deb11277d91ae091d399c655b87940d",
            "5c62e091b8c0565f1bafad0dad5934276143ae2ccef7a5381e8ada5b1a8d26d2",
        ),
        "3dc9202a2a3e33e639f9f5b1cd0b63a4cf7e1937bbdab0c393e423a05491b45b",
    )


# ---- EXT-01 Task 4: calibration, derived, checks_by_mode, names
RAW_LINE = b"  cabin_temp_c: {unit: degC, type: float, description: Cabin temperature}\n"


def _p(derived: str = "", cal: str = "") -> dict[str, bytes]:
    text = SHIPPED_P
    if cal:
        text = text.replace(RAW_LINE, RAW_LINE.replace(b"}\n", b", " + cal.encode() + b"}\n"))
    if derived:
        text += b"derived:\n" + derived.encode()
    return {"parameters": text}


def test_calibration_table_loads() -> None:
    db = ld(files(**_p(cal="calibration: {table: [[0, 0], [10, 100]]}")))
    assert db.calibrations["cabin_temp_c"].points == ((0.0, 0.0), (10.0, 100.0))
    assert db.raw_columns == ("cabin_temp_c_raw",)
    assert db.raw_names == db.param_names and db.derived_names == ()


@pytest.mark.parametrize(
    "key", ["poly: {coefficients: [0, 1], raw_range: [0, 1]}", "frozen_check: 1"]
)
def test_unknown_calibration_key_rejected(key: str) -> None:
    rejected("parameters.yaml", **_p(cal=f"calibration: {{table: [[0, 0], [1, 1]], {key}}}"))
    rejected("parameters.yaml", **_p(cal=key))  # not a parameter key either


def test_derived_loads_in_order() -> None:
    db = ld(
        files(
            **_p(
                "  power_w: {unit: W, expr: bus_voltage_v * bus_current_a}\n"
                "  power_rate: {unit: W/s, rate_of: power_w, window_s: 5}\n"
                "  r2: {unit: x, expr: abs(power_rate) + 1.5}\n"
            )
        )
    )
    assert db.derived_names == ("power_w", "power_rate", "r2")
    assert db.param_names == db.raw_names + db.derived_names
    s = db.derived["power_rate"]
    assert (s.rate_of, s.window_s, s.expr) == ("power_w", 5.0, None)
    assert db.derived["power_w"].expr is not None and db.derived["power_w"].rate_of is None


def test_derived_forward_reference_rejected() -> None:
    err = rejected(
        "parameters.yaml",
        **_p("  a: {unit: x, expr: b + 1}\n  b: {unit: x, expr: bus_voltage_v}\n"),
    )
    assert "b" in err.message


def test_derived_cycle_rejected() -> None:
    rejected(
        "parameters.yaml",
        **_p("  a: {unit: x, rate_of: b, window_s: 1}\n  b: {unit: x, expr: a}\n"),
    )
    rejected("parameters.yaml", **_p("  a: {unit: x, rate_of: a, window_s: 1}\n"))


@pytest.mark.parametrize("body", [
    "{unit: x}", "{unit: x, expr: bus_voltage_v, rate_of: bus_current_a, window_s: 1}",
    "{unit: x, rate_of: bus_current_a}", "{unit: x, expr: bus_voltage_v, window_s: 1}",
    "{unit: x, rate_of: bus_current_a, window_s: 0}", "{expr: bus_voltage_v}",
    "{unit: x, expr: 'bus_voltage_v +'}", "{unit: x, expr: nope + 1}",
    "{unit: x, rate_of: nope, window_s: 1}", "{unit: x, expr: 'ａ + 1'}",
])  # fmt: skip
def test_derived_needs_exactly_one_of_expr_rate_of(body: str) -> None:
    rejected("parameters.yaml", **_p(f"  d: {body}\n"))


def test_derived_expr_unicode_name_rejected_even_when_folded() -> None:
    p = _p("  a: {unit: x, expr: bus_voltage_v}\n  b: {unit: x, expr: 'ａ + 1'}\n")
    assert "ASCII" in rejected("parameters.yaml", **p).message


@pytest.mark.parametrize("name", ["abs", "min", "max", "sqrt", "mode", "Bad"])
def test_derived_reserved_or_bad_name_rejected(name: str) -> None:
    rejected("parameters.yaml", **_p(f"  {name}: {{unit: x, expr: bus_voltage_v}}\n"))


def test_raw_name_collision_rejected() -> None:
    p = SHIPPED_P.replace(RAW_LINE, RAW_LINE + b"  cabin_temp_c_raw: {unit: x, type: float}\n")
    cal = RAW_LINE.replace(b"}\n", b", calibration: {table: [[0, 0], [1, 1]]}}\n")
    err = rejected("parameters.yaml", parameters=p.replace(RAW_LINE, cal))
    assert "cabin_temp_c_raw" in err.message
    rejected("parameters.yaml", **_p("  bus_voltage_v: {unit: x, expr: bus_current_a}\n"))
    rejected(
        "parameters.yaml",
        **_p(
            "  cabin_temp_c_raw: {unit: x, expr: bus_current_a}\n",
            cal="calibration: {table: [[0, 0], [1, 1]]}",
        ),
    )


def test_no_raw_parameter_rejected() -> None:
    rejected("parameters.yaml", parameters=b"version: 1\nparameters: {}\n")


def test_limits_on_derived_parameter_load() -> None:
    lim = SHIPPED_L + b"  pw:\n    checks: [{type: limit, red_high: 100.0, hysteresis_abs: 2}]\n"
    db = ld(files(limits=lim, **_p("  pw: {unit: W, expr: bus_voltage_v * bus_current_a}\n")))
    assert "pw" in db.limits
    rejected("limits.yaml", limits=lim)


def test_checks_by_mode_empty_list_loads() -> None:
    lim = SHIPPED_L.replace(
        b"  cabin_temp_c: {checks: [", b"  cabin_temp_c: {checks_by_mode: {SAFE: []}, checks: ["
    )
    db = ld(files(limits=lim))
    assert db.limits["cabin_temp_c"].checks_by_mode["SAFE"] == []


def test_checks_by_mode_resolves_persistence_and_rejects_bad_mode_and_duplicates() -> None:
    one = "{type: limit, red_high: 40.0, hysteresis_abs: 1}"
    lim = SHIPPED_L.replace(
        b"  cabin_temp_c: {checks: [",
        f"  cabin_temp_c: {{checks_by_mode: {{TEST: [{one}]}}, checks: [".encode(),
    )
    c = ld(files(limits=lim)).limits["cabin_temp_c"].checks_by_mode["TEST"][0]
    assert isinstance(c, LimitCheck) and c.persistence == 3 and c.hysteresis_abs == 1
    for bad in (f"{{BOGUS: [{one}]}}", f"{{TEST: [{one}, {one}]}}"):
        rejected(
            "limits.yaml",
            limits=SHIPPED_L.replace(
                b"  cabin_temp_c: {checks: [",
                f"  cabin_temp_c: {{checks_by_mode: {bad}, checks: [".encode(),
            ),
        )


def test_checks_by_mode_floor_checked() -> None:
    lim = SHIPPED_L.replace(
        b"  cabin_temp_c: {checks: [",
        b"  cabin_temp_c: {checks_by_mode: {FAULT: [{type: limit, red_high: 40.0,"
        b" hysteresis_abs: 0.001}]}, checks: [",
    )
    assert "floor" in rejected("limits.yaml", limits=lim).message
