"""POST/GET /api/commands: confirm token, 2 s interlock, QUEUED → SENT → XADD (D-CMD-06/07/11).

Safety rule (P-pf12/16/38): SEND_ERROR ("not sent, safe to retry") only for an error that proves
the XADD was not applied; any other XADD error leaves the row SENT for the tracker to judge.
"""

import errno
import hashlib
import json
import logging
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any
from uuid import UUID, uuid4

import psycopg
import redis.asyncio
import redis.exceptions
from fastapi import APIRouter, Request

from gs.api.bodies import CommandAccepted, CommandConflict, CommandRequest
from gs.api.deps import ApiError, User, errors, mission, vehicles
from gs.commanding.args import check_args
from gs.common.audit import audit
from gs.common.interfaces.base import UtcTime, VehicleId
from gs.common.interfaces.sse import CommandView
from gs.common.interfaces.streams import Tc
from gs.common.views import command_views

CONFIRM_TTL_MS = 60_000
LOCK_TTL_MS = 2_000
REASON_MIN = 10
LIST_LIMIT = 200
DB_TIMEOUT_S = 5.0
log = logging.getLogger(__name__)
router = APIRouter()


def args_hash(vehicle: str, name: str, args: Mapping[str, object]) -> str:
    canon = json.dumps(
        {"vehicle": vehicle, "name": name, "args": args}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def lock_key(vehicle: str, name: str, hazardous: bool, ah: str) -> str:
    """Hazardous: one per name whatever the args; others per name and args (D-CMD-07)."""
    return f"cmdlock:{vehicle}:{name}" if hazardous else f"cmdlock:{vehicle}:{name}:{ah}"


async def release_lock(r: redis.asyncio.Redis, key: str, owner: str) -> None:
    """Compare-and-delete: only our own `cmd_id|user` (WATCH, GET, MULTI DEL EXEC)."""
    async with r.pipeline(transaction=True) as pipe:
        await pipe.watch(key)
        if await pipe.get(key) == owner:
            pipe.multi()  # type: ignore[no-untyped-call]
            pipe.delete(key)
            try:
                await pipe.execute()
            except redis.exceptions.WatchError:
                pass  # expired and taken by someone else: theirs now


class PreSendError(Exception):
    """A Redis error before the MULTI that carries the XADD: the command was never sent."""


def not_sent(e: BaseException) -> bool:
    """True only when the error proves the command was not applied (P-pf16, P-pf38): any error
    REPLY (the server refused the transaction atomically: NOPERM, OOM, LOADING, READONLY,
    MISCONF, WRONGTYPE, ERR, EXECABORT), auth at connect, or connection refused. Timeouts,
    closed connections and write errors got no reply: ambiguous, the row stays SENT."""
    if isinstance(e, PreSendError):
        return True
    if isinstance(e, redis.exceptions.ResponseError) and "Wrong number of response items" in str(e):
        return False  # redis-py's own check after EXEC replied: the transaction ran
    # BusyLoadingError and ExternalAuthProviderError subclass ConnectionError, not ResponseError
    if isinstance(
        e,
        redis.exceptions.ResponseError
        | redis.exceptions.BusyLoadingError
        | redis.exceptions.AuthenticationError
        | redis.exceptions.AuthorizationError
        | redis.exceptions.ExternalAuthProviderError,
    ):
        return True
    cause = e.__context__
    return (
        isinstance(e, redis.exceptions.ConnectionError)
        and isinstance(cause, OSError)
        and cause.errno == errno.ECONNREFUSED
    )


async def send_locked(r: redis.asyncio.Redis, key: str, owner: str, stream: str, data: str) -> bool:
    """XADD only while the interlock is still ours (P-pf39): WATCH key, GET == owner, then
    MULTI XADD EXEC. False: the lock expired or changed hands, nothing was sent. A Redis error
    before the MULTI is a PreSendError; one from EXEC is classified by not_sent()."""
    async with r.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(key)
            mine = await pipe.get(key) == owner
        except redis.exceptions.RedisError as e:
            raise PreSendError(f"{type(e).__name__}: {e}") from e
        if not mine:
            return False
        pipe.multi()  # type: ignore[no-untyped-call]
        pipe.xadd(stream, {"data": data})
        try:
            await pipe.execute()
        except redis.exceptions.WatchError as e:
            # redis-py 8.1 turns a connection error or timeout on the EXEC read into a
            # WatchError while WATCHing: no EXEC reply, the XADD may have landed (G30)
            if isinstance(e.__context__, redis.exceptions.RedisError):
                raise e.__context__ from e
            return False  # EXEC returned nil: the lock changed after our GET, nothing applied
    return True


def _broker_down(e: Exception) -> ApiError:
    log.warning("command broker error: %s: %s", type(e).__name__, e)
    return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "BROKER_DOWN", "message broker unreachable")


def _db_down(e: Exception) -> ApiError:
    log.warning("command database error: %s: %s", type(e).__name__, e)
    return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "database unreachable")


def _conflict(error: str, message: str, **fields: Any) -> ApiError:
    body = CommandConflict(error=error, message=message, **fields)
    return ApiError(HTTPStatus.CONFLICT, error, message, body=body)


async def _audit(
    request: Request, user: str, action: Any, reason: str | None, details: dict[str, Any]
) -> None:
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            await audit(conn, user, action, reason, details)
    except psycopg.Error as e:
        raise _db_down(e) from e


async def mark_sent(conn: psycopg.AsyncConnection, cmd_id: UUID, user: str, vehicle: str) -> bool:
    """QUEUED → SENT (monotonic guard); audits CMD_STATE only when the row moved."""
    cur = await conn.execute(
        "UPDATE commands SET state='SENT', sent_at=clock_timestamp()"
        " WHERE cmd_id=%s AND state='QUEUED'",
        (cmd_id,),
    )
    if cur.rowcount != 1:
        return False
    await audit(
        conn, user, "CMD_STATE", None, {"vehicle": vehicle, "cmd_id": cmd_id, "state": "SENT"}
    )
    return True


async def _confirm(
    request: Request, body: CommandRequest, user: str, ah: str, hazard: str | None
) -> str:
    """Hazardous gate (D-CMD-06): a single-use token bound to user and args, plus a reason."""
    r: redis.asyncio.Redis = request.app.state.redis
    details = {"vehicle": body.vehicle, "name": body.name, "args": body.args}
    if body.confirm_token is None:
        token = secrets.token_urlsafe(32)
        try:
            await r.set(f"confirm:{token}", f"{user}|{ah}", nx=True, px=CONFIRM_TTL_MS)
        except redis.exceptions.RedisError as e:
            raise _broker_down(e) from e
        await _audit(request, user, "CMD_CONFIRM_REQUIRED", None, details)
        raise _conflict(
            "HAZARDOUS_CONFIRMATION_REQUIRED",
            f"{body.name} is hazardous: {hazard}. Confirm with a reason.",
            hazard=hazard,
            confirm_token=token,
            expires_in_s=CONFIRM_TTL_MS // 1000,
        )
    reason = (body.reason or "").strip()
    if len(reason) < REASON_MIN:  # checked before GETDEL: the token stays usable (ruling R-k)
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "REASON_REQUIRED",
            f"a hazardous command needs a reason of at least {REASON_MIN} characters",
        )
    try:
        held = await r.getdel(f"confirm:{body.confirm_token}")
    except redis.exceptions.RedisError as e:
        raise _broker_down(e) from e
    if held != f"{user}|{ah}":
        await _audit(request, user, "CMD_REJECTED", "CONFIRMATION_INVALID", details)
        raise _conflict(
            "CONFIRMATION_INVALID",
            "confirmation expired, already used, or for another user or other arguments",
        )
    return reason


async def _take_lock(
    request: Request, body: CommandRequest, user: str, key: str, owner: str
) -> None:
    """The 2 s interlock (D-CMD-07): SET NX PX; a holder gets a DUPLICATE_COMMAND 409."""
    r: redis.asyncio.Redis = request.app.state.redis
    try:
        if await r.set(key, owner, nx=True, px=LOCK_TTL_MS):
            return
        holder = await r.get(key)
    except redis.exceptions.RedisError as e:
        raise _broker_down(e) from e
    held_id, _, by = str(holder or "").partition("|")  # decode_responses=True: a str
    seconds_ago, cmd_id = 0.0, None
    try:
        cmd_id = UUID(held_id)
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            cur = await conn.execute(
                "SELECT extract(epoch FROM clock_timestamp() - queued_at)::float8"
                " FROM commands WHERE cmd_id=%s",
                (cmd_id,),
            )
            row = await cur.fetchone()
            seconds_ago = float(row[0]) if row else 0.0  # not visible yet: just queued
    except ValueError:
        pass  # the holder expired between SET and GET
    except psycopg.Error as e:
        raise _db_down(e) from e
    details = {"vehicle": body.vehicle, "name": body.name, "args": body.args, "cmd_id": cmd_id}
    await _audit(request, user, "CMD_REJECTED", "DUPLICATE_COMMAND", details)
    raise _conflict(
        "DUPLICATE_COMMAND",
        f"the same command was sent {seconds_ago:.1f} s ago by {by or 'another operator'}",
        by=by or None,
        seconds_ago=seconds_ago,
        cmd_id=cmd_id,
    )


async def _queue_and_mark_sent(
    request: Request, tc: Tc, vehicle: str, ah: str, hazardous: bool
) -> bool:
    """INSERT QUEUED + CMD_REQUESTED, commit; then SENT + CMD_STATE, commit (before the XADD).
    False when the row had already left QUEUED (the tracker's timeout won): do not send."""
    pool = request.app.state.pool
    async with pool.connection(timeout=DB_TIMEOUT_S) as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO commands (cmd_id, vehicle, name, args, args_hash, hazardous, reason,"
                " issued_by, state, queued_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'QUEUED', clock_timestamp())",
                (
                    tc.cmd_id,
                    vehicle,
                    tc.name,
                    psycopg.types.json.Jsonb(tc.args),
                    ah,
                    hazardous,
                    tc.reason,
                    tc.issued_by,
                ),
            )
            details = {"vehicle": vehicle, "cmd_id": tc.cmd_id, "name": tc.name, "args": tc.args}
            await audit(conn, tc.issued_by, "CMD_REQUESTED", tc.reason, details)
        async with conn.transaction():
            return await mark_sent(conn, tc.cmd_id, tc.issued_by, vehicle)


async def _send_error(request: Request, tc: Tc, vehicle: str) -> None:
    """FAILED SEND_ERROR (guarded) + audit; a DB failure here leaves SENT for the tracker."""
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            cur = await conn.execute(
                "UPDATE commands SET state='FAILED', failure_reason='SEND_ERROR',"
                " failed_at=clock_timestamp() WHERE cmd_id=%s AND state IN ('QUEUED','SENT')",
                (tc.cmd_id,),
            )
            if cur.rowcount == 1:
                details = {
                    "vehicle": vehicle,
                    "cmd_id": tc.cmd_id,
                    "state": "FAILED",
                    "failure_reason": "SEND_ERROR",
                }
                await audit(conn, tc.issued_by, "CMD_STATE", "SEND_ERROR", details)
    except psycopg.Error:
        log.exception("could not record SEND_ERROR for %s", tc.cmd_id, extra={"vehicle": vehicle})


async def _release(request: Request, key: str, owner: str) -> None:
    try:
        await release_lock(request.app.state.redis, key, owner)
    except redis.exceptions.RedisError as e:  # it expires in LOCK_TTL_MS anyway
        log.warning("could not release %s: %s", key, e)


@router.post(
    "/api/commands",
    status_code=202,
    responses={**errors(400, 401, 403, 422, 503), 409: {"model": CommandConflict}},
)
async def post_command(body: CommandRequest, request: Request, user: User) -> CommandAccepted:
    cmd = mission(request).commands.get(body.name)
    err = (
        f"unknown vehicle {body.vehicle}"
        if body.vehicle not in vehicles(request)
        else check_args(cmd, body.args)
    )
    if err is not None or cmd is None:
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "INVALID_ARGS", err or "unknown command")
    ah = args_hash(body.vehicle, body.name, body.args)
    reason = body.reason.strip() if body.reason and body.reason.strip() else None
    if cmd.hazardous:
        reason = await _confirm(request, body, user, ah, cmd.hazard)
    cmd_id = uuid4()
    key, owner = lock_key(body.vehicle, body.name, cmd.hazardous, ah), f"{cmd_id}|{user}"
    await _take_lock(request, body, user, key, owner)
    tc = Tc(
        cmd_id=cmd_id,
        name=body.name,
        args=body.args,
        issued_by=user,
        issued_at=datetime.now(UTC),
        reason=reason,
    )
    try:
        sent = await _queue_and_mark_sent(request, tc, body.vehicle, ah, cmd.hazardous)
    except psycopg.Error as e:  # nothing sent: give the lock back so a retry is not a duplicate
        await _release(request, key, owner)
        raise _db_down(e) from e
    if not sent:
        await _release(request, key, owner)
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "database too slow: command not sent"
        )
    try:
        ours = await send_locked(
            request.app.state.redis, key, owner, f"tc:{body.vehicle}", tc.model_dump_json()
        )
    except (redis.exceptions.RedisError, PreSendError) as e:
        log.warning("XADD tc:%s failed for %s: %s: %s", body.vehicle, cmd_id, type(e).__name__, e)
        if not_sent(e):
            await _send_error(request, tc, body.vehicle)
            await _release(request, key, owner)
            raise ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "BROKER_DOWN",
                "command not sent (message broker unreachable): safe to retry",
            ) from e
        raise ApiError(  # it may have landed (G30): the tracker decides from evidence
            HTTPStatus.SERVICE_UNAVAILABLE,
            "BROKER_DOWN",
            "the broker did not confirm the send: the command may have been sent,"
            " check the command list before sending again",
        ) from e
    if not ours:  # the 2 s interlock ran out before the send: someone else may hold it now
        await _send_error(request, tc, body.vehicle)
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "UNAVAILABLE",
            "command not sent (the send took longer than the 2 s interlock): safe to retry",
        )
    return CommandAccepted(cmd_id=cmd_id)


@router.get("/api/commands", responses=errors(401, 422, 503))
async def list_commands(
    request: Request, user: User, vehicle: VehicleId | None = None, since: UtcTime | None = None
) -> list[CommandView]:
    # read-only: timeouts are the owner's tracker's job, never a side effect of a GET (a
    # comment, not a docstring: FastAPI would put a docstring into the frozen openapi.json)
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            return await command_views(conn, vehicle=vehicle, since=since, limit=LIST_LIMIT)
    except psycopg.Error as e:
        raise _db_down(e) from e
