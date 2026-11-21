"""Export names include `<name>_raw` (D-EXT-13, A-R23-1: raw is the repair path)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from gs.api.deps import ApiError
from gs.api.export import _names
from gs.common.missiondb import load_dir
from tests.support.missionfiles import edit, mission_copy

P = "bus_current_a"


def request(tmp_path: Path) -> Any:
    m = mission_copy(tmp_path)
    edit(m / "parameters.yaml", "description: Main bus current}",
         "description: Main bus current, calibration: {table: [[0, 0], [20, 40]]}}")  # fmt: skip
    mdb = load_dir(m)
    return cast(
        Any,
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(watcher=SimpleNamespace(current=mdb)))
        ),
    )


def test_default_has_each_raw_right_after_its_parameter(tmp_path: Path) -> None:
    names = _names(request(tmp_path), None)
    assert names[names.index(P) + 1] == f"{P}_raw"
    assert len(names) == len(set(names))


def test_raw_column_can_be_asked_for_and_unknown_still_400(tmp_path: Path) -> None:
    r = request(tmp_path)
    assert _names(r, f"{P}_raw,{P}") == [P, f"{P}_raw"]
    with pytest.raises(ApiError) as e:
        _names(r, "nope_raw")
    assert e.value.error == "UNKNOWN_PARAM"
