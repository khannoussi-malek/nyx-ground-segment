import pytest

from gs.common.config import ConfigError, require


def test_require_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_X", "v")
    assert require("GS_X") == "v"


@pytest.mark.parametrize("value", [None, ""])
def test_require_fails_fast(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.delenv("GS_X", raising=False)
    if value is not None:
        monkeypatch.setenv("GS_X", value)
    with pytest.raises(ConfigError, match="GS_X"):
        require("GS_X")
