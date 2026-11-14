import pytest

from gs.common.config import ConfigError
from gs.ingest.main import IngestSettings

BASE = {
    "REDIS_URL": "redis://u:p@h:1/0",
    "DATABASE_URL": "postgresql://u:p@h:1/gs",
    "CONSUMER_NAME": "c",
    "MISSION_DB": "/m",
    "BIND_HOST": "h",
    "HEALTH_PORT": "1",
}


def _settings(monkeypatch: pytest.MonkeyPatch, vehicles: str) -> IngestSettings:
    for k, v in {**BASE, "VEHICLES": vehicles}.items():
        monkeypatch.setenv(k, v)
    return IngestSettings.from_env()


def test_vehicles_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, "NYX-A, NYX-B ,").vehicles == ("NYX-A", "NYX-B")


@pytest.mark.parametrize("raw", [",", " , "])
def test_vehicles_without_a_name_fail_fast(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    with pytest.raises(ConfigError, match="no vehicle"):
        _settings(monkeypatch, raw)


def test_empty_vehicles_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="VEHICLES"):
        _settings(monkeypatch, "")
