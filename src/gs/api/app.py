"""FastAPI app: every route lives on ROUTER with its full path (Task 15/16 add to it)."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated, Any, Literal

import redis.asyncio
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from gs.api import events
from gs.api.auth import (
    SESSION_TTL_S,
    ApiError,
    current_user,
    load_users,
    sess_key,
    verify_password,
)
from gs.api.openapi import build_openapi
from gs.api.settings import Settings
from gs.common.missiondb import load_dir

CLIENT_HEADER = "X-GS-Client"
SOCKET_TIMEOUT_S = 5
CHECK_TIMEOUT_S = 2
ROUTER = APIRouter()
log = logging.getLogger(__name__)
User = Annotated[str, Depends(current_user)]


class _Body(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class LoginRequest(_Body):
    user: str
    password: str


class Me(_Body):
    user: str


class ErrorBody(_Body):
    error: str
    message: str


class Health(_Body):
    status: Literal["ok", "down"]
    checks: dict[str, str]


def _errors(*codes: int) -> dict[int | str, dict[str, object]]:
    """OpenAPI: error statuses carry ErrorBody (422 replaces HTTPValidationError)."""
    return {c: {"model": ErrorBody} for c in codes}


def _error(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(ErrorBody(error=error, message=message).model_dump(), status_code=status)


@ROUTER.post("/api/login", status_code=204, responses=_errors(401, 403, 422, 503))
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


@ROUTER.post("/api/logout", status_code=204, responses=_errors(401, 403, 503))
async def logout(request: Request, _user: User) -> Response:
    await request.app.state.redis.delete(sess_key(request.session["sid"]))
    request.session.clear()
    return Response(status_code=204)


@ROUTER.get("/api/me", responses=_errors(401, 503))
async def me(user: User) -> Me:
    return Me(user=user)


async def _check_redis(request: Request) -> None:
    await request.app.state.redis.ping()


async def _check_db(request: Request) -> None:
    async with request.app.state.pool.connection(timeout=CHECK_TIMEOUT_S) as conn:
        await conn.execute("select 1")


@ROUTER.get("/healthz", responses={200: {"model": Health}, 503: {"model": Health}})
async def healthz(request: Request) -> Response:
    checks: dict[str, str] = {}
    for name, check in (("redis", _check_redis), ("db", _check_db)):
        try:
            await asyncio.wait_for(check(request), CHECK_TIMEOUT_S + 1)
            checks[name] = "ok"
        except Exception as e:  # any failure means that dependency is down
            log.warning("healthz %s check failed: %s: %s", name, type(e).__name__, e)
            checks[name] = "down"
    up = all(v == "ok" for v in checks.values())
    body = Health(status="ok" if up else "down", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if up else 503)


ROUTER.add_api_route(
    "/api/events",
    events.events,
    methods=["GET"],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, **_errors(401, 503)},
)


def _openapi(app: FastAPI) -> Callable[[], dict[str, Any]]:
    def openapi() -> dict[str, Any]:
        return build_openapi(app.routes)

    return openapi


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.users = load_users(settings.users_file)
        app.state.mission = load_dir(settings.mission_db)
        app.state.vehicles = settings.vehicles
        # redis-py 8.1: Redis(...) defaults to 10 retries, from_url(...) to 0; keep it 0 explicitly
        # no client-side retry: connection errors reach our own handling (R28)
        app.state.redis = redis.asyncio.Redis.from_url(
            settings.redis_url,
            socket_timeout=SOCKET_TIMEOUT_S,
            retry=Retry(NoBackoff(), 0),
            decode_responses=True,
        )
        pool = AsyncConnectionPool(settings.database_url, min_size=1, max_size=4, open=False)
        await pool.open(wait=False)
        app.state.pool = pool
        try:
            yield
        finally:
            app.state.shutdown.set()
            await pool.close()
            await app.state.redis.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.shutdown = asyncio.Event()  # set by GracefulServer.handle_exit and on lifespan exit
    app.state.sse_open = 0
    # ponytail: https_only left off, the deployment is loopback-only plain http
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="strict",
        max_age=SESSION_TTL_S,
    )
    app.include_router(ROUTER)
    # last: a "/" mount would shadow routes registered after it
    app.mount("/", StaticFiles(directory=settings.ui_dist, html=True), name="ui")
    app.openapi = _openapi(app)  # type: ignore[method-assign]

    @app.middleware("http")
    async def require_client_header(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        unsafe = request.method not in ("GET", "HEAD", "OPTIONS")
        if (
            unsafe
            and request.url.path.startswith("/api/")
            and (request.headers.get(CLIENT_HEADER) != "console")
        ):
            return _error(403, "CLIENT_HEADER_REQUIRED", f"{CLIENT_HEADER}: console is required")
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError) -> JSONResponse:
        return _error(e.status, e.error, e.message)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, e: HTTPException) -> JSONResponse:
        return _error(e.status_code, HTTPStatus(e.status_code).name, str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        return _error(422, "VALIDATION_ERROR", f"{loc}: {first['msg']}")

    @app.exception_handler(redis.RedisError)
    async def _redis_down(request: Request, e: redis.RedisError) -> JSONResponse:
        log.warning("session store error on %s: %s: %s", request.url.path, type(e).__name__, e)
        return _error(503, "UNAVAILABLE", "session store unreachable")

    return app
