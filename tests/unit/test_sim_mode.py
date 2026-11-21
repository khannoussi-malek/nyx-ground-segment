import asyncio
import json
from datetime import UTC, datetime

from gs.simulator.faults import FaultBoard, fault_routes
from gs.simulator.frames import build_frame

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def post(board: FaultBoard, body: dict[str, object]) -> int:
    routes = fault_routes(board, ["NYX-TA1"], lambda: 0.0)
    return asyncio.run(routes[("POST", "/sim/mode")](json.dumps(body).encode()))[0]


def test_set_mode_changes_frame_mode_and_defaults_to_test() -> None:
    b = FaultBoard()
    assert json.loads(build_frame("NYX-TA1", 0, T0, {}, mode=b.mode("NYX-TA1")))["mode"] == "TEST"
    assert post(b, {"vehicle": "NYX-TA1", "mode": "SAFE"}) == 204
    assert b.mode("NYX-TA1") == "SAFE" and b.mode("NYX-TA2") == "TEST"
    assert json.loads(build_frame("NYX-TA1", 1, T0, {}, mode=b.mode("NYX-TA1")))["mode"] == "SAFE"


def test_set_mode_is_validated() -> None:
    b = FaultBoard()
    bad_bodies: list[dict[str, object]] = [
        {"vehicle": "NYX-TA1", "mode": "safe"},
        {"vehicle": "NYX-TA1"},
        {"vehicle": "NYX-TA1", "mode": "SAFE", "x": 1},
        {"vehicle": "NOPE", "mode": "SAFE"},
    ]
    for bad in bad_bodies:
        assert post(b, bad) == 422
    assert b.mode("NYX-TA1") == "TEST"
