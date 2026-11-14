"""Shared helpers for tests that touch .env, scripts and test vehicle ids."""

import secrets
import subprocess
import sys
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
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


def run_init_env(root: Path) -> subprocess.CompletedProcess[str]:
    script = REPO / "scripts" / "init_env.py"
    return sh(sys.executable, str(script), "--root", str(root), cwd=REPO)


def vid() -> str:
    """Test vehicle id; the Redis ACL scopes streams to NYX-*."""
    return "NYX-T" + secrets.token_hex(4).upper()
