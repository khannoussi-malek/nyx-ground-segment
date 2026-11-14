import hashlib
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests.support.env import REPO, parse_env, run_init_env, sh, users


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for name in (".env.example", "users.example.yaml", "Makefile"):
        shutil.copy(REPO / name, tmp_path / name)
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts/init_env.py", tmp_path / "scripts/init_env.py")
    return tmp_path


def test_creates_env_with_example_keys(root: Path) -> None:
    run_init_env(root)
    env = parse_env(root / ".env")
    assert list(env) == list(parse_env(root / ".env.example"))
    example = parse_env(root / ".env.example")
    secret = {k: v for k, v in env.items() if example[k] == ""}
    assert all(re.fullmatch(r"[0-9a-f]{48}", v) for v in secret.values())
    assert len(set(secret.values())) == len(secret)
    assert env["DB_HOST_PORT"] == "5432"  # non-secret defaults are copied verbatim


def test_prints_demo_passwords_once(root: Path) -> None:
    first, second = run_init_env(root).stdout, run_init_env(root).stdout
    env = parse_env(root / ".env")
    assert all(env[k] in first for k in ("DEMO_PW_CONDUCTOR", "DEMO_PW_FC1", "DEMO_PW_FC2"))
    assert second == ""


def test_never_rewrites_env_or_users(root: Path) -> None:
    run_init_env(root)
    files = (".env", "users.yaml")
    before = [(root / f).read_bytes() for f in files]
    run_init_env(root)
    assert [(root / f).read_bytes() for f in files] == before


def test_acl_file_matches_design(root: Path) -> None:
    run_init_env(root)
    env = parse_env(root / ".env")
    lines = (root / "redis/users.acl").read_text().splitlines()
    assert lines[0] == "user default off resetkeys resetchannels -@all"
    assert len(lines) == 6
    sim = next(ln for ln in lines if ln.startswith("user sim "))
    assert "#" + hashlib.sha256(env["REDIS_SIM_PW"].encode()).hexdigest() in sim
    assert "(~tm:NYX-* ~tcack:NYX-* +xadd)" in sim


def test_users_yaml_scrypt_hashes_verify(root: Path) -> None:
    run_init_env(root)
    env = parse_env(root / ".env")
    for name, key in (
        ("conductor", "DEMO_PW_CONDUCTOR"),
        ("fc1", "DEMO_PW_FC1"),
        ("fc2", "DEMO_PW_FC2"),
    ):
        _, n, r, p, salt, h = users(root)[name].split("$")
        derived = hashlib.scrypt(
            env[key].encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=2**28,
            dklen=32,
        )
        assert derived.hex() == h


def test_acl_regenerated_from_existing_env(root: Path) -> None:
    run_init_env(root)
    acl = (root / "redis/users.acl").read_bytes()
    (root / "redis/users.acl").unlink()
    run_init_env(root)
    assert (root / "redis/users.acl").read_bytes() == acl


def test_make_env_runs_init_and_is_quiet_when_repeated(root: Path) -> None:
    init = f"{sys.executable} scripts/init_env.py"
    first = sh("make", "env", f"ENV_INIT={init}", cwd=root)
    assert "demo users (shown once)" in first.stdout
    assert (root / ".env").exists() and (root / "redis/users.acl").exists()
    assert sh("make", "env", f"ENV_INIT={init}", cwd=root).stdout == ""


def test_make_up_down_reset_commands(root: Path) -> None:
    up = sh("make", "-n", "up", "ENV_INIT=true", cwd=root).stdout
    assert "docker compose up -d --build --wait" in up
    assert "docker compose up -d --build --wait " not in up
    assert "docker compose --profile ingest2 down" in sh("make", "-n", "down", cwd=root).stdout
    reset = sh("make", "-n", "reset", cwd=root).stdout
    assert "docker compose --profile ingest2 down -v" in reset
    assert "rm -f .env users.yaml redis/users.acl" in reset


def test_env_is_private_to_the_owner(root: Path) -> None:
    run_init_env(root)
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
