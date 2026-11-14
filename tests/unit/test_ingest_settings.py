import pytest

from gs.common.config import ConfigError
from gs.ingest.main import IngestSettings
from gs.ingest.owner import parse_homes

BASE = {
    "REDIS_URL": "redis://u:p@h:1/0",
    "DATABASE_URL": "postgresql://u:p@h:1/gs",
    "CONSUMER_NAME": "c",
    "MISSION_DB": "/m",
    "BIND_HOST": "h",
    "HEALTH_PORT": "1",
    "INGEST_HOMES": "i1=NYX-A",
}


def _settings(
    monkeypatch: pytest.MonkeyPatch, vehicles: str, homes: str = "i1=NYX-A"
) -> IngestSettings:
    for k, v in {**BASE, "VEHICLES": vehicles, "INGEST_HOMES": homes}.items():
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


def test_parse_homes() -> None:
    got = parse_homes(" ingest-1=NYX-TA1 , ingest-2=NYX-TA2", ("NYX-TA1", "NYX-TA2", "NYX-TA9"))
    assert got == {"NYX-TA1": "ingest-1", "NYX-TA2": "ingest-2"}


@pytest.mark.parametrize(
    "raw",
    ["ingest-1", "=NYX-TA1", "a=", "a=nyx-ta1", "a=NYX-TA1,", "a=NYX-TA1,b=NYX-TA1", "a=NYX-ZZ"],
)
def test_bad_homes_fail_fast(raw: str) -> None:
    with pytest.raises(ConfigError, match="INGEST_HOMES"):
        parse_homes(raw, ("NYX-TA1", "NYX-TA2"))


def test_settings_carry_homes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, "NYX-A,NYX-B", "i2=NYX-B").homes == {"NYX-B": "i2"}


def test_missing_homes_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="INGEST_HOMES"):
        _settings(monkeypatch, "NYX-A", "")
