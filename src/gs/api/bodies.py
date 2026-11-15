"""REST request/response bodies (frozen for the day-3 tracks; generated into schema.d.ts)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from gs.common.interfaces.base import StrictModel, UtcTime, VehicleId
from gs.common.interfaces.sse import CommandView, EventView, TransitionView
from gs.common.interfaces.streams import ArgValue


class _Body(StrictModel):
    model_config = ConfigDict(extra="forbid")

    # every REST body, before any side effect: text Postgres refuses (NUL, or a lone surrogate
    # from a JSON \ud800 escape, which has no UTF-8 form) is a 422 VALIDATION_ERROR, never a 500
    # or a 503 after a confirm token was spent (Task 27b).
    # ponytail: top-level str fields only; the one dict (command args) is checked against the
    # mission db before anything is stored.
    @field_validator("*")
    @classmethod
    def _postgres_text(cls, v: Any) -> Any:
        if isinstance(v, str):
            if "\x00" in v:
                raise ValueError("text must not contain NUL (\\u0000)")
            try:
                v.encode()
            except UnicodeEncodeError as e:
                raise ValueError("text must be valid UTF-8 (no unpaired surrogates)") from e
        return v


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
