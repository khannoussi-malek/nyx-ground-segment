"""check_args: one bounds check for the API (422) and the simulator (INVALID_ARGS) (D-ENG-17)."""

import pytest

from gs.commanding.args import check_args
from gs.common.missiondb import Command, load_dir
from tests.support.mission import MISSION_DIR

MDB = load_dir(MISSION_DIR)


@pytest.mark.parametrize(
    "name,args,ok",
    [
        ("isolate_valve", {"valve": "A"}, True),
        ("isolate_valve", {"valve": "C"}, False),
        ("isolate_valve", {}, False),
        ("isolate_valve", {"valve": "A", "x": 1}, False),
        ("isolate_valve", {"valve": 1}, False),
        ("vent_cabin", {"duration_s": 1}, True),
        ("vent_cabin", {"duration_s": 10.0}, True),
        ("vent_cabin", {"duration_s": 10.01}, False),
        ("vent_cabin", {"duration_s": 0.99}, False),
        ("vent_cabin", {"duration_s": "5"}, False),
        ("vent_cabin", {"duration_s": float("nan")}, False),
        ("vent_cabin", {"duration_s": float("inf")}, False),
        ("nope", {}, False),
    ],
)
def test_check_args(name: str, args: dict[str, object], ok: bool) -> None:
    assert (check_args(MDB.commands.get(name), args) is None) is ok


def test_messages_name_the_problem() -> None:
    vent, iso = MDB.commands["vent_cabin"], MDB.commands["isolate_valve"]
    assert check_args(None, {}) == "unknown command"
    assert "duration_s" in str(check_args(vent, {}))
    assert "x" in str(check_args(iso, {"valve": "A", "x": 1}))
    assert "[1.0, 10.0]" in str(check_args(vent, {"duration_s": 11}))
    assert "'C'" in str(check_args(iso, {"valve": "C"}))


CMD = Command.model_validate(
    {
        "description": "t",
        "hazardous": False,
        "args": {
            "n": {"type": "int", "min": 0, "max": 5},
            "f": {"type": "float", "min": 0, "max": 5},
        },
    }
)


@pytest.mark.parametrize(
    "args,ok",
    [
        ({}, True),  # neither required
        ({"n": 3}, True),
        ({"n": 3.0}, False),  # an int arg takes ints only
        ({"n": True}, False),  # bool is not a number here
        ({"f": False}, False),
        ({"n": 6}, False),
        ({"f": 0}, True),
    ],
)
def test_int_float_and_optional_args(args: dict[str, object], ok: bool) -> None:
    assert (check_args(CMD, args) is None) is ok
