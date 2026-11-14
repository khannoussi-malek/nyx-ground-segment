"""FastAPI app: every route lives on ROUTER with its full path (module routers included)."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

import redis.asyncio
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import AsyncConnectionPool
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from gs.api import alarms, auth, commands, deps, events, sessions, telemetry
from gs.api.auth import SESSION_TTL_S, load_users
from gs.api.bodies import ErrorBody, Health
from gs.api.deps import ApiError, User, errors
from gs.api.openapi import build_openapi
from gs.api.settings import Settings
from gs.common.interfaces.missiondb import MissionDbView, Vehicles
from gs.common.missiondb import load_dir

CLIENT_HEADER = "X-GS-Client"
SOCKET_TIMEOUT_S = 5
CHECK_TIMEOUT_S = 2
ROUTER = APIRouter()
log = logging.getLogger(__name__)


def _error(
    status: int, error: str, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    body = ErrorBody(error=error, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


@ROUTER.get("/api/vehicles", responses=errors(401, 503))
async def vehicles(request: Request, _user: User) -> Vehicles:
    return Vehicles(
        vehicles=list(deps.vehicles(request)), parameters=deps.mission(request).parameters
    )


@ROUTER.get("/api/mission-db", responses=errors(401, 503))
async def mission_db(request: Request, _user: User) -> MissionDbView:
    return MissionDbView.of(deps.mission(request))


@ROUTER.get("/api/mission-db/frame-schema", responses=errors(401, 503))
async def frame_schema(request: Request, user: User) -> dict[str, Any]:  # D-EXT-19, Task 12
    raise ApiError(
        HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "GET /api/mission-db/frame-schema"
    )


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
        except Exception as e:  # noqa: BLE001  any failure means that dependency is down
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
    responses={200: {"content": {"text/event-stream": {}}}, **errors(401, 503)},
)
for _r in (auth.router, commands.router, alarms.router, sessions.router, telemetry.router):
    ROUTER.include_router(_r)


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
        if e.body is not None:
            return JSONResponse(e.body.model_dump(mode="json"), status_code=e.status)
        return _error(e.status, e.error, e.message)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, e: HTTPException) -> JSONResponse:
        # keep e.headers: a 405 must carry Allow (RFC 9110 15.5.6, R19)
        return _error(e.status_code, HTTPStatus(e.status_code).name, str(e.detail), e.headers)

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
