import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import gs.alarms.checks as checks_pkg
from gs.alarms.checks import CHECKS, Reading, Sample, load_all, register
from gs.alarms.checks import limit as limit_mod
from gs.alarms.checks import roc as roc_mod
from gs.alarms.contract import Level
from gs.common import missiondb
from gs.common.missiondb import LimitEntry, LimitsFile

FLAT_CHECK_SOURCE = """
from dataclasses import dataclass
from typing import ClassVar, Literal

from gs.alarms.checks import CheckConfig, Reading, Sample, register
from gs.alarms.contract import Level


class FlatCheck(CheckConfig):
    type: Literal["flat"]
    level: int


@dataclass(frozen=True)
class FlatState:
    pass


@register
class Flat:
    kind: ClassVar[str] = "flat"
    Config: ClassVar[type[CheckConfig]] = FlatCheck
    State: ClassVar[type] = FlatState

    def __init__(self, cfg: FlatCheck) -> None:
        self.cfg = cfg

    def initial(self) -> object:
        return FlatState()

    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        return state, Reading(Level(self.cfg.level), None)
"""


def checks_union_kinds() -> set[str]:
    items = LimitsFile.model_json_schema()["$defs"]["LimitEntry"]["properties"]["checks"]["items"]
    return set(items["discriminator"]["mapping"])


def test_shipped_kinds() -> None:
    assert set(CHECKS) == {"limit", "roc"}
    assert checks_union_kinds() == {"limit", "roc"}


def test_moved_models_still_importable_from_missiondb() -> None:
    assert missiondb.LimitCheck is limit_mod.LimitCheck
    assert missiondb.RocCheck is roc_mod.RocCheck


def test_new_check_file_is_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "flat.py").write_text(FLAT_CHECK_SOURCE)  # ALM-07: one file, no other edit
    monkeypatch.setattr(checks_pkg, "__path__", [*checks_pkg.__path__, str(tmp_path)])
    try:
        load_all()
        assert "flat" in CHECKS
        entry = LimitEntry.model_validate({"checks": [{"type": "flat", "level": 2}]})
        assert type(entry.checks[0]) is CHECKS["flat"].Config
        chk = CHECKS["flat"](entry.checks[0])
        assert chk.step(chk.initial(), Sample(0, 0.0, 1.0, 0.1), Level.NOMINAL)[1] == Reading(
            Level.RED, None
        )
        assert checks_union_kinds() == {"flat", "limit", "roc"}
    finally:
        CHECKS.pop("flat", None)
        sys.modules.pop("gs.alarms.checks.flat", None)


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown check type 'nope'"):
        LimitEntry.model_validate({"checks": [{"type": "nope"}]})
    with pytest.raises(ValidationError, match="unknown check type None"):
        LimitEntry.model_validate({"checks": [{"limit_per_s": 1.0}]})


def test_item_errors_keep_their_location() -> None:
    with pytest.raises(ValidationError) as e:
        LimitEntry.model_validate({"checks": [{"type": "limit", "red_low": "95"}]})
    assert e.value.errors()[0]["loc"] == ("checks", 0, "red_low")


def test_duplicate_kind_rejected() -> None:
    class Shadow(limit_mod.Limit):
        pass

    with pytest.raises(ValueError, match="'limit' is already registered"):
        register(Shadow)
    assert CHECKS["limit"] is limit_mod.Limit
