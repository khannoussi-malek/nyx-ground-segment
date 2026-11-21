"""R23 M1 (A-R23-1): a calibration or unit change changes parameters_sha256 and is named by
changed_parameters; a limits-only edit changes neither."""

from gs.common.missiondb import changed_parameters
from tests.support.mission import calibrated, files, ld

BASE = ld(files())
CAL = calibrated()


def test_calibration_change_changes_hash_and_is_named() -> None:
    other = calibrated("[[0, 0], [10, 90]]")
    assert other.parameters_sha256 != CAL.parameters_sha256
    assert changed_parameters(CAL, other) == {"bus_current_a": ["calibration"]}
    assert changed_parameters(BASE, CAL) == {"bus_current_a": ["calibration"]}  # added table


def test_unit_change_changes_hash_and_is_named() -> None:
    p = (
        files()["parameters"]
        .decode()
        .replace("bus_current_a: {unit: A,", "bus_current_a: {unit: mA,")
    )
    new = ld(files(parameters=p.encode()))
    assert new.parameters_sha256 != BASE.parameters_sha256
    assert changed_parameters(BASE, new) == {"bus_current_a": ["unit"]}


def test_both_and_limits_only() -> None:
    p = (
        files()["parameters"]
        .decode()
        .replace("bus_current_a: {unit: A,", "bus_current_a: {unit: mA,")
    )
    both = ld(files(parameters=p.encode()))
    assert changed_parameters(CAL, both) == {"bus_current_a": ["calibration", "unit"]}
    lim = files()["limits"].decode().replace("yellow_high: 27.0", "yellow_high: 26.0")
    assert changed_parameters(BASE, ld(files(limits=lim.encode()))) == {}
