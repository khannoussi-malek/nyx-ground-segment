"""Compose helpers: the resolved config, `docker compose ps`, waiting for health."""

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from typing import Any

from tests.support.env import REPO, dotenv

ONE_SHOTS = ("migrate", "minio-init")
# ingest-2 is opt-in until the ownership lock lands (G1); anything that starts it enables this.
PROFILE_ENV = {"COMPOSE_PROFILES": "ingest2"}


def compose_config() -> dict[str, Any]:
    out = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO,
        env={**os.environ, **PROFILE_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    cfg: dict[str, Any] = json.loads(out)
    return cfg


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO,
        env={**os.environ, **PROFILE_ENV},
        capture_output=True,
        text=True,
        check=check,
    )


def recreate(
    services: Sequence[str], env: Mapping[str, str] | None = None, wait: bool = True
) -> None:
    """`up -d --no-deps [--wait]` with `env` over os.environ (and the ingest-2 profile on)."""
    cmd = ["docker", "compose", "up", "-d", "--no-deps", *(["--wait"] if wait else []), *services]
    subprocess.run(
        cmd,
        cwd=REPO,
        env={**os.environ, **PROFILE_ENV, **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )


def restart_count(svc: str) -> int:
    cid = compose("ps", "-a", "-q", svc).stdout.strip()
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.RestartCount}}", cid],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out)


def compose_ps() -> dict[str, dict[str, Any]]:
    out = compose("ps", "-a", "--format", "json").stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {r["Service"]: r for r in rows}


def wait_healthy(svc: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while compose_ps().get(svc, {}).get("Health") != "healthy":
        assert time.monotonic() < deadline, f"{svc} not healthy within {timeout}s"
        time.sleep(0.5)


def container_exit_code(svc: str) -> int:
    cid = compose("ps", "-a", "-q", svc).stdout.strip()
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.ExitCode}}", cid],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out)


def api_url() -> str:
    return f"http://127.0.0.1:{dotenv()['API_HOST_PORT']}"


def throwaway_env_text(text: str) -> str:
    """.env for a throwaway project: a pinned COMPOSE_PROJECT_NAME would make it the real stack."""
    return "".join(
        ln for ln in text.splitlines(keepends=True) if not ln.startswith("COMPOSE_PROJECT_NAME=")
    )
