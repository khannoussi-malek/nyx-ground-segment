"""Password hashing (stdlib scrypt, format shared with scripts/init_env.py), login and logout."""

import asyncio
import hashlib
import hmac
import secrets
from http import HTTPStatus
from pathlib import Path

import yaml
from fastapi import APIRouter, Request, Response

from gs.api.bodies import LoginRequest, Me
from gs.api.deps import ApiError, User, errors, sess_key

SESSION_TTL_S = 43200
_N, _R, _P, _MAXMEM = 2**17, 8, 1, 2**28
router = APIRouter()


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


@router.post("/api/login", status_code=204, responses=errors(400, 401, 403, 422, 429, 503))
async def login(body: LoginRequest, request: Request) -> Response:
    stored = request.app.state.users.get(body.user)
    # ponytail: no dummy hash for unknown users; three fixed users make the timing leak moot
    ok = stored is not None and await asyncio.to_thread(verify_password, body.password, stored)
    if not ok:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "LOGIN_FAILED", "wrong user or password")
    sid = secrets.token_urlsafe(32)
    await request.app.state.redis.set(sess_key(sid), body.user, ex=SESSION_TTL_S)
    old = request.session.get("sid")
    if isinstance(old, str):  # re-login must not leave the previous session alive
        await request.app.state.redis.delete(sess_key(old))
    request.session.clear()
    request.session["sid"] = sid
    return Response(status_code=204)


@router.post("/api/logout", status_code=204, responses=errors(401, 403, 503))
async def logout(request: Request, _user: User) -> Response:
    await request.app.state.redis.delete(sess_key(request.session["sid"]))
    request.session.clear()
    return Response(status_code=204)


@router.get("/api/me", responses=errors(401, 503))
async def me(user: User) -> Me:
    return Me(user=user)
