"""Route dependencies shared by every API module (frozen Track 0 contract; no gs.api imports
besides bodies, so auth and the route modules can all import it)."""

from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, Request

from gs.api.bodies import ErrorBody
from gs.common.missiondb import MissionDb


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


def sess_key(sid: str) -> str:
    return f"sess:{sid}"


async def current_user(request: Request) -> str:
    """The user behind the signed session cookie; revoked or unknown sessions are 401."""
    sid = request.session.get("sid")
    user = await request.app.state.redis.get(sess_key(sid)) if isinstance(sid, str) else None
    if not user:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "UNAUTHENTICATED", "login required")
    return str(user)


User = Annotated[str, Depends(current_user)]


def errors(*codes: int) -> dict[int | str, dict[str, object]]:
    """OpenAPI: error statuses carry ErrorBody (422 replaces HTTPValidationError)."""
    return {c: {"model": ErrorBody} for c in codes}


def mission(request: Request) -> MissionDb:
    """The mission-db set the API works with (Task 12 makes it follow reloads)."""
    db: MissionDb = request.app.state.mission
    return db


def vehicles(request: Request) -> tuple[str, ...]:
    v: tuple[str, ...] = request.app.state.vehicles
    return v
