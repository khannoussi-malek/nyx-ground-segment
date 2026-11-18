"""make up && make down && make up keeps .env and users.yaml and the stack comes back healthy."""

import hashlib
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import httpx

from tests.support.compose import ONE_SHOTS, api_url, compose_ps
from tests.support.env import REPO, dotenv, sh
from tests.support.http import CLIENT_HEADERS


def _sha(name: str) -> str:
    return hashlib.sha256((REPO / name).read_bytes()).hexdigest()


def test_up_down_up_keeps_env_and_logins() -> None:
    before = {f: _sha(f) for f in (".env", "users.yaml")}
    sh("make", "down")
    sh("make", "up")  # --wait returns only when every long-running service is healthy
    assert {f: _sha(f) for f in before} == before
    r = httpx.post(
        f"{api_url()}/api/login",
        json={"user": "fc1", "password": dotenv()["DEMO_PW_FC1"]},
        headers=CLIENT_HEADERS,
    )
    assert r.status_code == 204
    states = compose_ps()
    unhealthy = {n for n, s in states.items() if n not in ONE_SHOTS and s["Health"] != "healthy"}
    assert not unhealthy
    assert {n: states[n]["ExitCode"] for n in ONE_SHOTS} == {"migrate": 0, "minio-init": 0}


def test_make_reset(tmp_path: Path) -> None:
    """R16: reset drops volumes and generated files; runs in a copy so the real stack stays.
    R3-02: with no stack up the guard cannot count the archived hours, so a bare `make reset`
    refuses and deletes nothing; FORCE=1 goes ahead."""
    root = tmp_path / f"nyxreset{secrets.token_hex(3)}"  # compose project name = directory name
    (root / "redis").mkdir(parents=True)
    (root / "scripts").mkdir()
    for f in ("Makefile", "docker-compose.yml", "scripts/reset_guard.py"):
        shutil.copy(REPO / f, root / f)
    for f in (".env", "users.yaml", "redis/users.acl"):
        (root / f).write_text("x=1\n")
    volume = f"{root.name}_pgdata"
    sh(
        "docker", "volume", "create", "--label", f"com.docker.compose.project={root.name}",
        "--label", "com.docker.compose.volume=pgdata", volume,
    )  # fmt: skip
    try:
        refused = subprocess.run(["make", "reset"], cwd=root, capture_output=True, text=True)
        assert refused.returncode != 0 and "could not count the archived hours" in refused.stdout
        assert (root / ".env").exists() and subprocess.run(
            ["docker", "volume", "inspect", volume], capture_output=True
        ).returncode == 0, "a refused reset deleted something"
        forced = subprocess.run(
            ["make", "reset"], cwd=root, capture_output=True, text=True,
            env={**os.environ, "FORCE": "1"},
        )  # fmt: skip
        assert forced.returncode == 0, forced.stderr
        assert not [f for f in (".env", "users.yaml", "redis/users.acl") if (root / f).exists()]
        gone = subprocess.run(["docker", "volume", "inspect", volume], capture_output=True)
        assert gone.returncode != 0, "make reset left the compose volume behind"
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)


def test_minio_init_survives_minio_setup_restart() -> None:
    """CI bug: Bitnami MinIO runs a temporary server, stops it, then starts the real one;
    the health check passed on the first and minio-init hit the gap (connection refused)."""
    sh("docker", "compose", "restart", "minio")
    # --no-deps: start while MinIO is still in its setup phase, not after it reports healthy
    sh("docker", "compose", "run", "--rm", "--no-deps", "minio-init")
    sh("docker", "compose", "up", "-d", "--wait", "minio")
