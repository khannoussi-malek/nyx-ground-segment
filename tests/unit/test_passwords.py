import re
from pathlib import Path

import pytest

from gs.api.auth import hash_password, load_users, verify_password
from tests.support.env import parse_env, run_init_env


def test_roundtrip_and_format() -> None:
    h = hash_password("pw")
    assert re.fullmatch(r"scrypt\$131072\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}", h)
    assert verify_password("pw", h)
    assert not verify_password("px", h)


def test_same_password_gets_different_salts() -> None:
    assert hash_password("pw") != hash_password("pw")
    assert hash_password("pw", salt=b"\x01" * 16) == hash_password("pw", salt=b"\x01" * 16)


@pytest.mark.parametrize(
    "stored",
    [
        "garbage",
        "",
        "bcrypt$1$8$1$00$00",
        "scrypt$x$8$1$00$00",
        "scrypt$131072$8$1$zz$00",
        "scrypt$3$8$1$00$00",
    ],  # fmt: skip
)
def test_malformed_hash_is_false_not_error(stored: str) -> None:
    assert not verify_password("pw", stored)


def test_init_env_users_verify(tmp_path: Path) -> None:  # scripts/init_env.py <-> API contract
    for name in (".env.example", "users.example.yaml"):
        (tmp_path / name).write_text(Path(name).read_text())
    run_init_env(tmp_path)
    env, users = parse_env(tmp_path / ".env"), load_users(tmp_path / "users.yaml")
    assert verify_password(env["DEMO_PW_FC1"], users["fc1"])
    assert verify_password(env["DEMO_PW_CONDUCTOR"], users["conductor"])
    assert not verify_password(env["DEMO_PW_FC1"], users["fc2"])


def test_load_users_rejects_bad_shape(tmp_path: Path) -> None:
    (tmp_path / "u.yaml").write_text("users: [a, b]\n")
    with pytest.raises(ValueError, match="users"):
        load_users(tmp_path / "u.yaml")
