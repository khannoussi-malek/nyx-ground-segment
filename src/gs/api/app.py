"""FastAPI app: every route lives on ROUTER with its full path (module routers included)."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any, Literal

import psycopg
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
from gs.api.hub import Hub, format_sse
from gs.api.openapi import build_openapi
from gs.api.settings import Settings
from gs.common.audit import audit
from gs.common.interfaces import build_frame_model
from gs.common.interfaces.missiondb import MissionDbView, Vehicles
from gs.common.missiondb import MissionDb, MissionDbError, load_dir
from gs.ingest.events import GroundEvent, insert_events, to_link
from gs.ingest.mission import MissionWatcher, start_set

CLIENT_HEADER = "X-GS-Client"
SOCKET_TIMEOUT_S = 5
CHECK_TIMEOUT_S = 2
DB_CONNECT_S = 5  # the start-set connection; then the load_dir fallback
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
async def frame_schema(request: Request, user: User) -> dict[str, Any]:  # D-EXT-19
    return build_frame_model(deps.mission(request).param_names).model_json_schema()


def no_hazardous_downgrade(old: MissionDb, new: MissionDb) -> None:
    """The API's reload never drops a command's hazardous flag (D-CMD-09, G31): the confirm
    step stays until the API restarts with the new file. Ingest applies the same set."""
    was = {n: c.hazardous for n, c in old.commands.items()}
    names = sorted(n for n, c in new.commands.items() if was.get(n) and not c.hazardous)
    if names:
        raise MissionDbError(
            "commands.yaml",
            f"hazardous true->false for {', '.join(names)}: commands not updated until API restart",
        )


async def _reloaded(conn: psycopg.AsyncConnection, old: MissionDb, new: MissionDb) -> None:
    """LIMITS_RELOADED with every command whose hazardous flag changed (D-EXT-02, EX-25)."""
    was = {n: c.hazardous for n, c in old.commands.items()}
    changed = sorted(n for n, c in new.commands.items() if was.get(n, c.hazardous) != c.hazardous)
    details = {
        "set_sha256": new.set_sha256,
        "limits_sha256": new.limits_sha256,
        "commands_sha256": new.commands_sha256,
        "parameters_sha256": new.parameters_sha256,
        "hazardous_changed": changed,
    }
    await audit(conn, "system", "LIMITS_RELOADED", None, details)


async def _start_set(settings: Settings) -> MissionDb:
    """The set at start, stored like ingest does; the files as they are when the database is
    unreachable (the watcher stores the set on its first tick, P-pf5)."""
    try:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url, autocommit=True, connect_timeout=DB_CONNECT_S
        ) as conn:
            mdb, rejected = await start_set(conn, settings.mission_db)
            await insert_events(conn, rejected)
            return mdb
    except psycopg.OperationalError as e:
        log.warning("mission db at start without the database (%s); using the files", e)
        return load_dir(settings.mission_db)


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
    status: Literal["ok", "degraded", "down"] = "ok" if up else "down"
    hub = request.app.state.hub
    if up and hub.degraded:  # a ui:events entry was rejected (D-ING-27)
        status, checks["ui_events"] = "degraded", "degraded"
    elif up and not hub.up():  # the reader failed or hangs: no deltas reach any console
        status, checks["ui_events"] = "degraded", "stalled"
    body = Health(status=status, checks=checks)
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
        mdb = await _start_set(settings)
        app.state.vehicles = settings.vehicles
        # redis-py 8.1: Redis(...) defaults to 10 retries, from_url(...) to 0; keep it 0 explicitly
        # no client-side retry: connection errors reach our own handling (R28)
        app.state.redis = redis.asyncio.Redis.from_url(
            settings.redis_url,
            socket_timeout=SOCKET_TIMEOUT_S,
            retry=Retry(NoBackoff(), 0),
            decode_responses=True,
        )
        pool = AsyncConnectionPool(settings.database_url, min_size=1, max_size=8, open=False)
        await pool.open(wait=False)
        app.state.pool = pool
        hub = Hub(app.state.redis, pool, settings.vehicles, lambda: watcher.current.param_names)

        async def to_clients(evs: Sequence[GroundEvent]) -> None:  # the API can't XADD ui:events
            for e in evs:
                hub.broadcast(format_sse("link", to_link(e)))

        watcher = MissionWatcher(
            settings.mission_db, settings.database_url, "api", mdb, on_apply=_reloaded,
            on_events=to_clients, check=no_hazardous_downgrade, by="api",
        )  # fmt: skip
        app.state.hub, app.state.watcher = hub, watcher
        tasks = [
            asyncio.create_task(hub.run(app.state.shutdown)),  # the one ui:events reader
            asyncio.create_task(watcher.run(app.state.shutdown)),
        ]
        try:
            yield
        finally:
            app.state.shutdown.set()
            try:
                await asyncio.gather(*tasks)
            finally:  # a task error at shutdown must not leak the pool or the client
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
