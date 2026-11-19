from pathlib import Path

import pytest

from gs.api.settings import Settings
from gs.archiver.s3 import S3Settings
from gs.common.config import ConfigError

ENV = {
    "REDIS_URL": "r", "DATABASE_URL": "d", "SESSION_SECRET": "s", "USERS_FILE": "/u.yaml",
    "MISSION_DB": "/m", "VEHICLES": " A , B ", "UI_DIST": "/ui", "S3_ENDPOINT": "minio:9000",
    "S3_ACCESS_KEY": "replay", "S3_SECRET_KEY": "k", "S3_BUCKET": "archive",
}  # fmt: skip


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.vehicles == ("A", "B") and s.users_file == Path("/u.yaml")
    assert s.ui_dist == Path("/ui")
    assert s.s3 == S3Settings("minio:9000", "replay", "k", "archive")


def test_missing_s3_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("S3_SECRET_KEY")
    with pytest.raises(ConfigError, match="S3_SECRET_KEY"):
        Settings.from_env()


def test_missing_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SESSION_SECRET")
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.from_env()


def test_missing_ui_dist_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("UI_DIST")
    with pytest.raises(ConfigError, match="UI_DIST"):
        Settings.from_env()


def test_test_controls_default_off_and_sim_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GS_TEST_CONTROLS", raising=False)
    assert Settings.from_env().test_controls is False
    monkeypatch.setenv("GS_TEST_CONTROLS", "1")
    monkeypatch.setenv("SIM_URL", "http://simulator:8081")
    s = Settings.from_env()
    assert (s.test_controls, s.sim_url) == (True, "http://simulator:8081")
    monkeypatch.setenv("GS_TEST_CONTROLS", "0")
    assert Settings.from_env().test_controls is False
