from pathlib import Path

import pytest

from gs.common.config import ConfigError
from gs.simulator.main import SimSettings

BASE = {
    "REDIS_URL": "redis://u:p@h:1/0",
    "SIM_SEED": "1",
    "BIND_HOST": "h",
    "HEALTH_PORT": "1",
    "MISSION_DB": "/mission",
}


def _settings(monkeypatch: pytest.MonkeyPatch, vehicles: str, **over: str) -> SimSettings:
    for k, v in {**BASE, "SIM_VEHICLES": vehicles, **over}.items():
        monkeypatch.setenv(k, v)
    return SimSettings.from_env()


def test_vehicles_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, "NYX-TA1, NYX-TA2 ,").vehicles == ("NYX-TA1", "NYX-TA2")


@pytest.mark.parametrize("raw", [",", " , "])
def test_vehicles_without_a_name_fail_fast(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    with pytest.raises(ConfigError, match="no vehicle"):
        _settings(monkeypatch, raw)


def test_bad_health_port_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="HEALTH_PORT"):
        _settings(monkeypatch, "NYX-TA1", HEALTH_PORT="x")


def test_lowercase_vehicle_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="SIM_VEHICLES"):
        _settings(monkeypatch, "nyx-ta1")


def test_mission_db_is_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, "NYX-TA1").mission_db == Path("/mission")


def test_mission_db_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="MISSION_DB"):
        _settings(monkeypatch, "NYX-TA1", MISSION_DB="")
