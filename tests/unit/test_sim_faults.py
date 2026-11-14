import json

import pytest
from pydantic import ValidationError

from gs.simulator.faults import Active, FaultBoard, FaultRequest, apply

V = "NYX-TA1"
P = {"cabin_pressure_kpa": 101.3, "cabin_temp_c": 22.0}


def req(fault: str, **params: object) -> FaultRequest:
    return FaultRequest.model_validate({"vehicle": V, "fault": fault, "params": params})


def test_leak_rate() -> None:
    out = apply(P, [Active("leak", 0.0, None, {})], 10.0, None)
    assert out["cabin_pressure_kpa"] == pytest.approx(97.3)
    assert out["cabin_temp_c"] == 22.0


def test_overheat_rate() -> None:
    out = apply(P, [Active("overheat", 2.0, None, {})], 12.0, None)
    assert out["cabin_temp_c"] == pytest.approx(27.0)


def test_step_forces_exact_value_then_ends() -> None:
    b = FaultBoard()
    b.add(req("step", param="cabin_temp_c", value=40, duration_s=2), 100.0)
    assert apply(P, b.active(V, 101.0), 101.0, None)["cabin_temp_c"] == 40.0
    assert apply(P, b.active(V, 102.5), 102.5, None) == P  # expired


def test_freeze_repeats_previous_values() -> None:
    old = {"cabin_pressure_kpa": 1.0, "cabin_temp_c": 2.0}
    assert apply(P, [Active("freeze", 0.0, None, {})], 1.0, old) == old


def test_clear_is_idempotent_and_drops_offset() -> None:
    b = FaultBoard()
    b.add(req("leak"), 0.0)
    b.clear(V, "leak")
    b.clear(V, "leak")
    assert b.active(V, 5.0) == ()


def test_one_shots_are_taken_once_with_defaults() -> None:
    b = FaultBoard()
    b.add(req("seq_jump"), 0.0)
    b.add(req("duplicate"), 0.0)
    b.add(req("malformed"), 0.0)
    assert b.active(V, 0.0) == ()  # not time-based
    assert (j := b.take_once(V, "seq_jump")) and j.params == {"by": 1000}
    assert b.take_once(V, "seq_jump") is None
    assert (d := b.take_once(V, "duplicate")) and d.params == {"count": 1}
    assert (m := b.take_once(V, "malformed")) and m.params == {"kind": "json"}


def test_dropout_expires() -> None:
    b = FaultBoard()
    b.add(req("dropout", duration_s=1), 0.0)
    assert [a.name for a in b.active(V, 0.5)] == ["dropout"]
    assert b.active(V, 1.0) == ()


def test_request_is_json_roundtrippable() -> None:
    r = FaultRequest.model_validate_json(json.dumps({"vehicle": V, "fault": "freeze"}))
    assert r.params == {}


@pytest.mark.parametrize(
    "body",
    [
        {"vehicle": V, "fault": "dropout"},
        {"vehicle": V, "fault": "dropout", "params": {"duration_s": 0}},
        {"vehicle": V, "fault": "nope"},
        {"vehicle": "bad id", "fault": "freeze"},
        {"vehicle": V, "fault": "freeze", "extra": 1},
        {"vehicle": V, "fault": "leak", "params": {"bogus": 1}},
        {"vehicle": V, "fault": "seq_jump", "params": {"by": 0}},
        {"vehicle": V, "fault": "duplicate", "params": {"count": "2"}},
        {"vehicle": V, "fault": "malformed", "params": {"kind": "xml"}},
        {"vehicle": V, "fault": "step", "params": {"param": "x", "value": 1, "duration_s": 1}},
        {"vehicle": V, "fault": "step", "params": {"param": "cabin_temp_c", "value": 1}},
    ],
)
def test_invalid_requests(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FaultRequest.model_validate(body)


@pytest.mark.parametrize("name", ["pause", "hold_exec"])
def test_pause_and_hold_exec_not_yet_accepted(name: str) -> None:  # day 3 (D-SIM-06)
    with pytest.raises(ValidationError):
        FaultRequest.model_validate({"vehicle": V, "fault": name, "params": {"n": 1}})
