"""`make down` removes the opt-in ingest-2 container too (it is outside the default profile)."""

import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.compose import throwaway_env_text
from tests.support.env import REPO, sh


def test_down_removes_opt_in_ingest_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / f"nyxdown{secrets.token_hex(3)}"  # compose project name = directory name
    (root / "redis").mkdir(parents=True)
    for f in ("Makefile", "docker-compose.yml"):
        shutil.copy(REPO / f, root / f)
    (root / ".env").write_text(throwaway_env_text((REPO / ".env").read_text()))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)  # worktree shells pin it
    for f in ("users.yaml", "redis/users.acl"):
        (root / f).write_text("x=1\n")
    try:
        sh("docker", "compose", "--profile", "ingest2", "create", "ingest-2", cwd=root)
        sh("make", "down", cwd=root)
        left = sh("docker", "compose", "--profile", "ingest2", "ps", "-a", "-q", cwd=root).stdout
        assert left.strip() == ""
    finally:
        subprocess.run(
            ["docker", "compose", "--profile", "ingest2", "down", "-v"],
            cwd=root,
            capture_output=True,
        )
