"""Shared helpers for tests that touch .env, scripts and test vehicle ids."""

import asyncio
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def parse_env(path: Path) -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    return {k: v for k, v in pairs}


def users(root: Path) -> dict[str, str]:
    result: dict[str, str] = yaml.safe_load((root / "users.yaml").read_text())["users"]
    return result


def sh(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(
            f"{args} exited {r.returncode}\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
    return r


def run_init_env(root: Path) -> subprocess.CompletedProcess[str]:
    script = REPO / "scripts" / "init_env.py"
    return sh(sys.executable, str(script), "--root", str(root), cwd=REPO)


def vid() -> str:
    """Test vehicle id; the Redis ACL scopes streams to NYX-*."""
    return "NYX-T" + secrets.token_hex(4).upper()


def dotenv() -> dict[str, str]:
    return parse_env(REPO / ".env")


async def until(cond: Callable[[], bool], timeout: float) -> None:
    """Poll `cond` every 50 ms; AssertionError when it is still false after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(0.05)


def wait_until(cond: Callable[[], bool], timeout: float) -> None:
    """Sync twin of `until`."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        time.sleep(0.05)


def now() -> datetime:
    return datetime.now(UTC)
