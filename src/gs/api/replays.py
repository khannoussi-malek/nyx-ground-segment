"""Replays of recorded data over SSE (D-REP-01..06; route stubs, Track RP Task 13)."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from gs.api.bodies import ReplayCreated, ReplayRequest
from gs.api.deps import ApiError, User, errors

router = APIRouter()


def _todo(what: str) -> ApiError:
    return ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", what)


# 400: INVALID_WINDOW, WINDOW_TOO_LARGE (and a body that is not UTF-8); 404 NO_DATA;
# 409 ARCHIVE_CORRUPT "archive object failed hash check" (T4-f, D-ARC-11)
@router.post("/api/replays", status_code=201, responses=errors(400, 401, 403, 404, 409, 422, 503))
async def create_replay(body: ReplayRequest, request: Request, user: User) -> ReplayCreated:
    raise _todo("replay create (Track RP Task 13)")


@router.delete(
    "/api/replays/{replay_id}",
    status_code=204,
    response_class=Response,
    responses=errors(401, 403, 404, 422),
)
async def delete_replay(replay_id: UUID, request: Request, user: User) -> Response:
    raise _todo("replay delete (Track RP Task 13)")


@router.get(
    "/api/replays/{replay_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, **errors(401, 404, 422)},
)
async def replay_events(replay_id: UUID, request: Request, user: User) -> StreamingResponse:
    """SSE of gs.common.interfaces.replay messages (ReplayEvent), framed like /api/events."""
    raise _todo("replay events (Track RP Task 13)")
