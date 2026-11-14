"""D-ENG-21 part 1: lint on from day 2."""

import json
import shutil
import subprocess
from pathlib import Path

from tests.support.env import REPO

BAD = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n# TODO later\n"


def ruff_codes(path: Path) -> set[str]:
    # per-file-ignores globs resolve against cwd: lint from the tmp root with a config copy
    root = path.parent.parent
    shutil.copy(REPO / "pyproject.toml", root / "pyproject.toml")
    cmd = ["uv", "run", "--project", str(REPO), "ruff", "check", "--output-format", "json"]
    out = subprocess.run(
        [*cmd, "--config", "pyproject.toml", str(path.relative_to(root))],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return {d["code"] for d in json.loads(out.stdout)}


def test_ruff_flags_blind_except_pass_and_todo(tmp_path: Path) -> None:
    p = tmp_path / "src" / "bad.py"
    p.parent.mkdir()
    p.write_text(BAD)
    codes = ruff_codes(p)
    assert {"BLE001", "S110"} <= codes and any(c.startswith("FIX") for c in codes)


def test_tests_are_exempt_from_fix(tmp_path: Path) -> None:  # FIX applies to src/ only
    p = tmp_path / "tests" / "t.py"
    p.parent.mkdir()
    p.write_text("# TODO x\n")
    assert ruff_codes(p) == set()
