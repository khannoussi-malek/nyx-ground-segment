"""Password hashing (stdlib scrypt, format shared with scripts/init_env.py) and session lookup."""

import hashlib
import hmac
import secrets
from http import HTTPStatus
from pathlib import Path

import yaml
from fastapi import Request

SESSION_TTL_S = 43200
_N, _R, _P, _MAXMEM = 2**17, 8, 1, 2**28


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=32)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${_scrypt(password, salt, _N, _R, _P).hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except ValueError:  # wrong field count, bad hex/int, or scrypt parameter error
        return False


def load_users(path: Path) -> dict[str, str]:
    users = yaml.safe_load(path.read_text())["users"]
    if not isinstance(users, dict) or not all(isinstance(v, str) for v in users.values()):
        raise ValueError(f"{path}: 'users' must map user name to a scrypt hash")
    return {str(k): v for k, v in users.items()}


def sess_key(sid: str) -> str:
    return f"sess:{sid}"


async def current_user(request: Request) -> str:
    """The user behind the signed session cookie; revoked or unknown sessions are 401."""
    sid = request.session.get("sid")
    user = await request.app.state.redis.get(sess_key(sid)) if isinstance(sid, str) else None
    if not user:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "UNAUTHENTICATED", "login required")
    return str(user)
