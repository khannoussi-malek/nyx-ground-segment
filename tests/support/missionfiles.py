"""Editable copies of the shipped mission db."""

import shutil
from pathlib import Path

from tests.support.mission import MISSION_DIR


def mission_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "mission"
    shutil.copytree(MISSION_DIR, dst)
    return dst


def edit(path: Path, old: str, new: str) -> None:
    """Replace `old` in the file, which must contain it."""
    text = path.read_text()
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1))
