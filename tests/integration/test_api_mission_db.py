"""GET /api/vehicles and GET /api/mission-db (design.md API table, D-EXT-01; ruling G20)."""

import httpx
import pytest

from gs.common.interfaces.missiondb import MissionDbView, Vehicles
from gs.common.missiondb import load_dir
from tests.support.env import REPO
from tests.support.http import FC1_PW, login

MISSION = load_dir(REPO / "mission")


@pytest.mark.parametrize("path", ["/api/vehicles", "/api/mission-db"])
async def test_requires_a_session(api_client: httpx.AsyncClient, path: str) -> None:
    r = await api_client.get(path)
    assert r.status_code == 401 and r.json()["error"] == "UNAUTHENTICATED"


async def test_vehicles_lists_vehicles_and_parameter_units(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    body = (await api_client.get("/api/vehicles")).json()
    assert body["vehicles"] == ["NYX-TA1"]  # api_settings configures one vehicle
    assert list(body["parameters"]) == list(
        MISSION.parameters
    )  # raw parameters, file order kept (derived are in /api/mission-db)
    assert body["parameters"]["cabin_pressure_kpa"] == {
        "unit": "kPa",
        "type": "float",
        "description": "Cabin total pressure",
        "calibration": None,
    }
    assert Vehicles.model_validate(body, strict=False)


async def test_mission_db_returns_the_loaded_set(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    body = (await api_client.get("/api/mission-db")).json()
    assert body["set_sha256"] == MISSION.set_sha256
    assert body["limits_sha256"] == MISSION.limits_sha256
    assert body["commands_sha256"] == MISSION.commands_sha256
    assert body["parameters_sha256"] == MISSION.parameters_sha256
    assert set(body["parameters"]) == set(MISSION.parameters)
    assert body["commands"]["vent_cabin"]["hazardous"] is True
    assert body["derived"]["power_w"]["unit"] == "W"  # derived values are labelled too (I1)


async def test_mission_db_keeps_every_check_field(api_client: httpx.AsyncClient) -> None:
    # a check dumped as its CheckConfig base would drop red_low etc. (G21)
    await login(api_client, "fc1", FC1_PW)
    body = (await api_client.get("/api/mission-db")).json()
    limit, roc = body["limits"]["cabin_pressure_kpa"]["checks"]
    assert limit["type"] == "limit"
    assert (limit["red_low"], limit["yellow_low"], limit["yellow_high"], limit["red_high"]) == (
        95.0,
        98.0,
        104.0,
        106.0,
    )
    assert roc["type"] == "roc" and roc["limit_per_s"] == 0.15 and roc["direction"] == "falling"
    # resolved defaults (G20): hysteresis, persistence, the ROC window
    assert (limit["hysteresis_pct"], limit["persistence"]) == (15.0, 3)
    assert (roc["window_s"], roc["persistence"]) == (1.0, 3)
    vent = body["commands"]["vent_cabin"]
    assert vent["hazard"] == "Irreversible loss of cabin atmosphere"
    assert vent["args"]["duration_s"] == {
        "type": "float", "min": 1.0, "max": 10.0, "unit": "s", "required": True
    }  # fmt: skip
    assert MissionDbView.model_validate(body, strict=False).set_sha256 == MISSION.set_sha256
