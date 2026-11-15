"""REST request/response bodies (frozen for the day-3 tracks; generated into schema.d.ts)."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from gs.common.interfaces.base import StrictModel, UtcTime, VehicleId
from gs.common.interfaces.sse import CommandView, EventView, TransitionView
from gs.common.interfaces.streams import ArgValue


class _Body(StrictModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(_Body):
    error: str
    message: str


class LoginRequest(_Body):
    user: str
    password: str


class Me(_Body):
    user: str


class Health(_Body):
    status: Literal["ok", "degraded", "down"]
    checks: dict[str, str]


class CommandRequest(_Body):
    vehicle: VehicleId
    name: str
    args: dict[str, ArgValue]
    confirm_token: str | None = None
    reason: str | None = None


class CommandAccepted(_Body):
    cmd_id: UUID


class CommandConflict(_Body):
    """Every 409 of POST /api/commands (ruling R-s); the fields its `error` needs are set."""

    error: str
    message: str
    hazard: str | None = None
    confirm_token: str | None = None
    expires_in_s: int | None = None
    by: str | None = None
    seconds_ago: float | None = None
    cmd_id: UUID | None = None


class AckRequest(_Body):
    # FastAPI validates the decoded JSON in python mode, where a strict UUID rejects the string
    request_id: Annotated[UUID, Field(strict=False)]
    note: str
    # AlarmView.last.key when the ACK dialog opened (P-pf43)
    seen_key: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class AckAccepted(_Body):
    request_id: UUID
    status: Literal["pending", "applied", "superseded", "rejected"]


class SessionStart(_Body):
    vehicle: VehicleId
    name: Annotated[str, Field(min_length=1, max_length=200)]


class SessionView(_Body):
    id: int
    vehicle: VehicleId
    name: str
    conductor: str
    started_at: UtcTime
    ended_at: UtcTime | None
    ended_by: str | None


class ParamStats(_Body):
    unit: str
    min: float | None
    max: float | None
    avg: float | None
    n: int


class AlarmTransition(_Body):
    alarm_id: UUID
    param: str
    transition: TransitionView


class SessionReport(_Body):
    session: SessionView
    complete: bool
    note: str | None
    frozen_at: UtcTime
    duration_s: float
    frames: int
    expected: int
    parameters: dict[str, ParamStats]
    link_events: list[EventView]
    alarms: list[AlarmTransition]
    commands: list[CommandView]


class ReportConflict(_Body):
    error: str
    message: str
    frames: int | None = None
    expected: int | None = None


class Bucket(_Body):
    t: UtcTime
    lo: float
    hi: float
    mean: float
    first: float
    last: float
    n: int
