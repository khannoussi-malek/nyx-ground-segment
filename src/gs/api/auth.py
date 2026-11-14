"""Password hashing (stdlib scrypt, format shared with scripts/init_env.py), login and logout."""

import asyncio
import hashlib
import hmac
import logging
import secrets
from http import HTTPStatus
from pathlib import Path

import psycopg
import yaml
from fastapi import APIRouter, Request, Response
from psycopg_pool import PoolTimeout

from gs.api.bodies import LoginRequest, Me
from gs.api.deps import ApiError, User, errors, sess_key
from gs.common.audit import Action, audit

SESSION_TTL_S = 43200
LOGIN_LIMIT, LOGIN_WINDOW_S = 5, 60
ACTOR_MAX = 64
DB_TIMEOUT_S = 3
_N, _R, _P, _MAXMEM = 2**17, 8, 1, 2**28
router = APIRouter()
log = logging.getLogger("gs.api")


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


async def failed_logins(conn: psycopg.AsyncConnection, user: str) -> int:
    """Failures in the window that count; rate-limited attempts are audited but not counted."""
    cur = await conn.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'LOGIN_FAILED' AND actor = %s"
        " AND at > now() - make_interval(secs => %s) AND details->>'rate_limited' IS NULL",
        (user, LOGIN_WINDOW_S),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _audit(
    request: Request, actor: str, action: Action, details: dict[str, bool] | None = None
) -> None:
    """One autocommit audit row; a database error is 503 (DB-503 stays in this module)."""
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            await conn.set_autocommit(True)
            await audit(conn, actor, action, details=details)
    except (psycopg.Error, PoolTimeout) as e:
        raise _db_down(e) from e


def _db_down(e: Exception) -> ApiError:
    log.warning("database error in auth: %s: %s", type(e).__name__, e)
    return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "database unreachable")


@router.post("/api/login", status_code=204, responses=errors(400, 401, 403, 422, 429, 503))
async def login(body: LoginRequest, request: Request) -> Response:
    actor = body.user[:ACTOR_MAX]
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            limited = await failed_logins(conn, actor) >= LOGIN_LIMIT
    except (psycopg.Error, PoolTimeout) as e:
        raise _db_down(e) from e
    if limited:
        await _audit(request, actor, "LOGIN_FAILED", {"rate_limited": True})
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "LOGIN_RATE_LIMITED", "too many failed logins")
    stored = request.app.state.users.get(body.user)
    # ponytail: no dummy hash for unknown users; three fixed users make the timing leak moot
    ok = stored is not None and await asyncio.to_thread(verify_password, body.password, stored)
    if not ok:
        await _audit(request, actor, "LOGIN_FAILED")
        raise ApiError(HTTPStatus.UNAUTHORIZED, "LOGIN_FAILED", "wrong user or password")
    sid = secrets.token_urlsafe(32)
    await request.app.state.redis.set(sess_key(sid), body.user, ex=SESSION_TTL_S)
    old = request.session.get("sid")
    if isinstance(old, str):  # re-login must not leave the previous session alive
        await request.app.state.redis.delete(sess_key(old))
    request.session.clear()
    request.session["sid"] = sid
    await _audit(request, actor, "LOGIN")
    return Response(status_code=204)


@router.post("/api/logout", status_code=204, responses=errors(401, 403, 503))
async def logout(request: Request, user: User) -> Response:
    await request.app.state.redis.delete(sess_key(request.session["sid"]))
    request.session.clear()
    await _audit(request, user[:ACTOR_MAX], "LOGOUT")
    return Response(status_code=204)


@router.get("/api/me", responses=errors(401, 503))
async def me(user: User) -> Me:
    return Me(user=user)
