"""GS-EXT-04: committed JSON Schemas must equal what the pydantic models generate."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gs.common.interfaces.export import write_all
from tests.support.gen import REPO, stale_files


def test_committed_schemas_are_fresh(tmp_path: Path) -> None:
    write_all(tmp_path)
    assert stale_files(tmp_path) == []


def test_negative_control_model_edit_is_stale(tmp_path: Path) -> None:  # D-EXT-17
    shutil.copytree(REPO / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    f = tmp_path / "src/gs/common/interfaces/frame.py"
    text = f.read_text()
    line = "    last_cmd_id: UUID | None\n"
    assert text.count(line) == 1
    f.write_text(text.replace(line, line + "    negative_control: int = 0\n"))
    subprocess.run(
        [sys.executable, "-m", "gs.common.interfaces.export", "--out", str(tmp_path / "out")],
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
        check=True,
    )
    assert "schemas/frame.schema.json" in stale_files(tmp_path / "out")


def test_mission_edit_leaves_no_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # EX-01: nothing committed depends on mission/
    shutil.copytree(REPO / "mission", tmp_path / "mission")
    p = tmp_path / "mission/parameters.yaml"
    p.write_text(p.read_text() + "  cabin_humidity_pct: {unit: '%', type: float}\n")
    monkeypatch.setenv("MISSION_DB", str(tmp_path / "mission"))
    write_all(tmp_path / "out")
    assert stale_files(tmp_path / "out") == []
