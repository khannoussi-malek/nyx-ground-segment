"""Editable copies of the shipped mission db."""

import hashlib
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


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def set_formula(m: Path) -> str:
    """D-EXT-02: sha256 of '<limits>\\n<commands>\\n<parameters>\\n' file hashes."""
    names = ("limits.yaml", "commands.yaml", "parameters.yaml")
    return hashlib.sha256("".join(f"{sha256_file(m / n)}\n" for n in names).encode()).hexdigest()
