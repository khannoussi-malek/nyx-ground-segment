"""Test controls (D-SIM-12): start and clear simulator faults from the console. The browser never
calls the simulator; the API forwards to its fault endpoint (D-SIM-09), audited, and only when
GS_TEST_CONTROLS=1 (every route answers 404 otherwise)."""

import logging
from http import HTTPStatus
from typing import get_args

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from gs.api.dbguard import connect, db_503
from gs.api.deps import ApiError, User, errors, vehicles
from gs.common.audit import audit
from gs.simulator.faults import FaultClear, FaultName, FaultRequest

SIM_TIMEOUT_S = 3
PATH = "/api/test-controls"
log = logging.getLogger(__name__)
router = APIRouter()


class TestControls(BaseModel):
    __test__ = False  # not a pytest class
    vehicles: list[str]
    faults: list[FaultName]


def _on(request: Request) -> None:
    if not request.app.state.test_controls:
        raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "test controls are off")


def _live(request: Request, vehicle: str) -> None:
    if vehicle not in vehicles(request):
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", f"{vehicle} is not live"
        )


async def _forward(request: Request, method: str, body: FaultRequest | FaultClear) -> None:
    url = request.app.state.sim_url + "/sim/fault"
    try:
        async with httpx.AsyncClient(timeout=SIM_TIMEOUT_S) as c:
            r = await c.request(method, url, content=body.model_dump_json())
    except httpx.HTTPError as e:
        log.warning("simulator %s failed: %s: %s", method, type(e).__name__, e)
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "simulator unreachable"
        ) from e
    if r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
        msg = "the simulator refused the fault"
        try:
            msg = str(r.json()["message"])
        except ValueError, TypeError, KeyError:  # not JSON, not an object, no message
            pass
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", msg)
    if not r.is_success:
        log.warning("simulator %s answered %s", method, r.status_code)
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "simulator unreachable")


async def _run(
    request: Request,
    user: str,
    method: str,
    action: str,
    body: FaultRequest | FaultClear,
) -> None:
    _on(request)
    _live(request, body.vehicle)
    details = body.model_dump(mode="json")
    with db_503(request):  # on record before it is injected
        async with connect(request) as conn:
            await audit(conn, user, action, None, details)  # type: ignore[arg-type]
    await _forward(request, method, body)


@router.get(PATH, responses=errors(401, 404, 503))
async def get_controls(request: Request, _user: User) -> TestControls:
    _on(request)
    return TestControls(vehicles=list(vehicles(request)), faults=list(get_args(FaultName)))


@router.post(PATH + "/fault", status_code=204, responses=errors(400, 401, 403, 404, 422, 503))
async def start_fault(request: Request, user: User, body: FaultRequest) -> None:
    await _run(request, user, "POST", "TEST_FAULT", body)


@router.delete(PATH + "/fault", status_code=204, responses=errors(400, 401, 403, 404, 422, 503))
async def clear_fault(request: Request, user: User, body: FaultClear) -> None:
    await _run(request, user, "DELETE", "TEST_FAULT_CLEAR", body)
