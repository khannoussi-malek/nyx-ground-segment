import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests.support.env import REPO

_spec = importlib.util.spec_from_file_location("check_commits", REPO / "scripts/check_commits.py")
assert _spec and _spec.loader
c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c)


def test_bad_subjects() -> None:
    ok = [
        "feat: x",
        "merge: Track C into Gate 3b",
        "Merge pull request #2",
        "fix(api)!: y",
        "ci: z",
    ]
    bad = ["update stuff", "feat:x", "Feat: x", "feat: ", "wip"]
    assert c.bad_subjects(ok) == []
    assert c.bad_subjects([*ok, *bad]) == bad


def test_main_reports_bad_subject_in_a_real_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*a: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    run("init", "-q", "-b", "main")
    run("commit", "-q", "--allow-empty", "-m", "feat: base")
    run("commit", "-q", "--allow-empty", "-m", "feat: fine")
    monkeypatch.chdir(tmp_path)
    assert c.main("HEAD~1") == 0
    run("commit", "-q", "--allow-empty", "-m", "update stuff")
    assert c.main("HEAD~2") == 1
