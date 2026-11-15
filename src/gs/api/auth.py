"""Password hashing (stdlib scrypt, format shared with scripts/init_env.py), login and logout."""

import asyncio
import hashlib
import hmac
import logging
import secrets
from functools import cache
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
ACTOR_MAX, USER_MAX = 64, 256
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


def _db_down(e: Exception) -> ApiError:
    log.warning("database error in auth: %s: %s", type(e).__name__, e)
    return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "database unreachable")


async def _audit(
    request: Request,
    actor: str,
    action: Action,
    reason: str | None = None,
    details: dict[str, bool] | None = None,
) -> None:
    """One autocommit audit row; a database error is 503 (DB-503 stays in this module)."""
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            await conn.set_autocommit(True)
            await audit(conn, actor, action, reason, details)
    except (psycopg.Error, PoolTimeout) as e:
        raise _db_down(e) from e


@cache
def _dummy_hash() -> str:
    return hash_password("dummy", b"\x00" * 16)


async def _attempt(request: Request, body: LoginRequest, actor: str) -> ApiError | None:
    """Check, verify and audit under a per-actor advisory lock so concurrent attempts cannot
    overshoot the limit; the audit row commits before any refusal is raised."""
    async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"login:{actor}",))
        if await failed_logins(conn, actor) >= LOGIN_LIMIT:
            await audit(conn, actor, "LOGIN_FAILED", "rate limited", {"rate_limited": True})
            return ApiError(
                HTTPStatus.TOO_MANY_REQUESTS, "LOGIN_RATE_LIMITED", "too many failed logins"
            )
        stored = request.app.state.users.get(body.user)
        # unknown users hash against a constant so response time does not reveal the name
        ok = await asyncio.to_thread(verify_password, body.password, stored or _dummy_hash())
        if not (ok and stored):
            await audit(conn, actor, "LOGIN_FAILED", "bad credentials")
            return ApiError(HTTPStatus.UNAUTHORIZED, "LOGIN_FAILED", "wrong user or password")
        await audit(conn, actor, "LOGIN")  # before the session: none without its audit row
    return None


@router.post("/api/login", status_code=204, responses=errors(400, 401, 403, 422, 429, 503))
async def login(body: LoginRequest, request: Request) -> Response:
    if "\x00" in body.user or len(body.user) > USER_MAX:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "user: invalid name")
    try:
        refused = await _attempt(request, body, body.user[:ACTOR_MAX])
    except (psycopg.Error, PoolTimeout) as e:
        raise _db_down(e) from e
    if refused:
        raise refused
    sid = secrets.token_urlsafe(32)
    await request.app.state.redis.set(sess_key(sid), body.user, ex=SESSION_TTL_S)
    old = request.session.get("sid")
    if isinstance(old, str):  # re-login must not leave the previous session alive
        await request.app.state.redis.delete(sess_key(old))
    request.session.clear()
    request.session["sid"] = sid
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
