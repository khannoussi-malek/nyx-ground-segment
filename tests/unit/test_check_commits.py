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


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *messages: str) -> None:
    def run(*a: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    run("init", "-q", "-b", "main")
    run("commit", "-q", "--allow-empty", "-m", "feat: base")
    for m in messages:
        run("commit", "-q", "--allow-empty", "-m", m)
    monkeypatch.chdir(tmp_path)


def test_a_coauthor_trailer_fails_in_a_real_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path, monkeypatch, "feat: ok\n\nCo-Authored-By: Someone <a@b.c>")
    assert c.main("HEAD~1") == 1


def test_ai_attribution_fails_in_a_real_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path, monkeypatch, "feat: ok\n\nGenerated with a tool")
    assert c.main("HEAD~1") == 1


def test_mentioning_claude_fails_and_a_clean_body_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path, monkeypatch, "fix: handle the case\n\nA normal body line.")
    assert c.main("HEAD~1") == 0
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "-m", "docs: x\n\nwritten by Claude"],
        cwd=tmp_path, check=True, capture_output=True,
    )  # fmt: skip
    assert c.main("HEAD~2") == 1


@pytest.mark.parametrize(
    "body",
    [
        "Written by ChatGPT",
        "authored by AI",
        "Authored by an AI assistant",
        "Created with Copilot",
        "drafted by GPT-4",
        "Co-written with an LLM",
    ],
)
def test_explicit_ai_authorship_phrases_are_attributed(body: str) -> None:
    assert c.attributed([f"docs: x\n\n{body}"]) == ["docs: x"]


@pytest.mark.parametrize("body", ["Written by hand.", "Authored by the team.", "Created with make"])
def test_human_authorship_phrases_pass(body: str) -> None:
    assert c.attributed([f"docs: x\n\n{body}"]) == []
