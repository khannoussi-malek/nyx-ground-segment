import pytest

from gs.common.config import ConfigError, require, require_int, vehicle_list


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


def test_require_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_N", "8080")
    assert require_int("GS_N") == 8080


@pytest.mark.parametrize("raw", ["abc", "80.5", "0x10"])
def test_require_int_names_the_variable(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("GS_N", raw)
    with pytest.raises(ConfigError, match="GS_N"):
        require_int("GS_N")


@pytest.mark.parametrize("raw", ["nyx-1", "NYX TA1", "NYX_TA1", "A" * 33])
def test_vehicle_list_rejects_invalid_names(raw: str) -> None:
    with pytest.raises(ConfigError, match=r"VEHICLES.*invalid"):
        vehicle_list(raw)


def test_vehicle_list_strips_and_accepts_valid() -> None:
    assert vehicle_list(" NYX-TA1 , NYX-TA2,") == ("NYX-TA1", "NYX-TA2")
