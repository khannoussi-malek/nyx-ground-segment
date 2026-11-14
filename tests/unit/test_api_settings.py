from pathlib import Path

import pytest

from gs.api.settings import Settings
from gs.common.config import ConfigError

ENV = {
    "REDIS_URL": "r", "DATABASE_URL": "d", "SESSION_SECRET": "s", "USERS_FILE": "/u.yaml",
    "MISSION_DB": "/m", "VEHICLES": " A , B ",
}  # fmt: skip


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.vehicles == ("A", "B") and s.users_file == Path("/u.yaml")


def test_missing_secret_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SESSION_SECRET")
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.from_env()
