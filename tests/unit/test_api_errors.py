"""ApiError → response body: ErrorBody, or the route's own conflict model when one is given."""

import inspect
import json
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI

from gs.api.app import create_app
from gs.api.bodies import CommandConflict, ReportConflict
from gs.api.deps import ApiError
from gs.api.settings import Settings


def _app(tmp_path: Path) -> FastAPI:
    return create_app(
        Settings(
            redis_url="redis://x", database_url="postgresql://x", session_secret="s",
            users_file=tmp_path / "u.yaml", mission_db=tmp_path, vehicles=(), ui_dist=tmp_path,
        )
    )  # fmt: skip


async def _body(app: FastAPI, e: ApiError) -> tuple[int, dict[str, object]]:
    handler = app.exception_handlers[ApiError]
    resp = handler(None, e)  # type: ignore[arg-type]  # the handler ignores the request
    assert inspect.isawaitable(resp)
    resp = await resp
    return resp.status_code, json.loads(bytes(resp.body))


async def test_plain_api_error_is_error_body(tmp_path: Path) -> None:
    status, body = await _body(_app(tmp_path), ApiError(404, "ALARM_NOT_FOUND", "no such alarm"))
    assert (status, body) == (404, {"error": "ALARM_NOT_FOUND", "message": "no such alarm"})


async def test_conflict_bodies_keep_their_fields(tmp_path: Path) -> None:
    app, cid = _app(tmp_path), UUID(int=7)
    confirm = CommandConflict(
        error="HAZARDOUS_CONFIRMATION_REQUIRED", message="confirm", hazard="h",
        confirm_token="tok", expires_in_s=60,
    )  # fmt: skip
    status, body = await _body(app, ApiError(409, confirm.error, confirm.message, body=confirm))
    assert status == 409
    assert (body["hazard"], body["confirm_token"], body["expires_in_s"]) == ("h", "tok", 60)
    dup = CommandConflict(
        error="DUPLICATE_COMMAND", message="dup", by="fc2", seconds_ago=1.5, cmd_id=cid
    )
    _, body = await _body(app, ApiError(409, dup.error, dup.message, body=dup))
    assert (body["by"], body["seconds_ago"], body["cmd_id"]) == ("fc2", 1.5, str(cid))
    pending = ReportConflict(error="REPORT_PENDING", message="wait", frames=3, expected=10)
    _, body = await _body(app, ApiError(409, pending.error, pending.message, body=pending))
    assert body == {"error": "REPORT_PENDING", "message": "wait", "frames": 3, "expected": 10}
