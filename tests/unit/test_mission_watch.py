from pathlib import Path

import pytest

from gs.common.missiondb import MissionDbError, load_dir
from gs.ingest.mission import Poll, observe, validate_set
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy

A = ("a1", "a2", "a3")
B = ("b1", "b2", "b3")
C = ("c1", "c2", "c3")


def test_applied_after_two_equal_polls() -> None:
    p, ready = observe(Poll(), B, A)
    assert not ready
    p, ready = observe(p, B, A)
    assert ready


def test_intermediate_set_never_ready() -> None:
    p, r1 = observe(Poll(), B, A)
    p, r2 = observe(p, C, A)
    p, r3 = observe(p, C, A)
    assert (r1, r2, r3) == (False, False, True) and p.last == C


def test_unchanged_never_ready() -> None:
    p, r1 = observe(Poll(), A, A)
    _, r2 = observe(p, A, A)
    assert not (r1 or r2)


def _raw(d: Path) -> tuple[bytes, bytes, bytes]:
    b = [(d / n).read_bytes() for n in ("limits.yaml", "commands.yaml", "parameters.yaml")]
    return b[0], b[1], b[2]


def test_hazardous_downgrade_is_not_an_ingest_rule(tmp_path: Path) -> None:  # G31
    # ingest validates one way at start and on reload (no active-set rule), so two workers
    # can't end up on different sets; the rule belongs to the API's own reload (day 3)
    m = mission_copy(tmp_path)
    edit(m / "commands.yaml", "hazardous: true", "hazardous: false")
    mdb = validate_set(_raw(m), load_dir(m).param_names)
    assert mdb.commands["vent_cabin"].hazardous is False


def test_unmigrated_param_rejected(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with (m / "parameters.yaml").open("a") as f:
        f.write("  cabin_humidity_pct: {unit: pct, type: float}\n")
    cols = load_dir(MISSION_DIR).param_names
    with pytest.raises(
        MissionDbError, match="cabin_humidity_pct has no telemetry column: run make migrate"
    ):
        validate_set(_raw(m), cols)
