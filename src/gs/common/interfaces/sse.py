from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue

from gs.alarms.contract import AlarmState, TransitionKind
from gs.common.interfaces.base import U32, Mode, StrictModel, UtcTime, VehicleId
from gs.common.interfaces.streams import ArgValue


class _Sse(StrictModel):
    model_config = ConfigDict(extra="forbid")


class Hello(_Sse):
    kind: Literal["hello"] = "hello"
    server_ms: int


class Tm(_Sse):
    kind: Literal["tm"] = "tm"
    vehicle: VehicleId
    seq: U32
    t_onboard: UtcTime
    mode: Mode
    last_cmd_id: UUID | None
    values: dict[str, float | None]
    age_ms: int | None = None  # api_now - t_onboard, set by the API only
    frozen: bool = False


CmdState = Literal["QUEUED", "SENT", "RECEIVED", "VERIFIED", "FAILED"]
FailureReason = Literal["SEND_ERROR", "REJECTED", "NOT_RECEIVED", "NOT_VERIFIED", "ACK_INVALID"]
Outcome = Literal["NOT_SENT", "NOT_EXECUTED", "UNKNOWN", "RECEIVED_LATE", "EXECUTED_LATE"]


class TransitionView(_Sse):
    key: str
    kind: TransitionKind
    t: UtcTime  # trigger t_onboard; an ACK: after_t_onboard (alarm views), apply time (report)
    alert: bool
    by_user: str
    note: str | None
    check_kind: str | None
    level: str | None
    value: float | None


class AckRequestView(_Sse):
    request_id: UUID
    by_user: str
    status: Literal["pending", "applied", "superseded", "rejected"]
    status_reason: str | None


class AlarmView(_Sse):
    alarm_id: UUID
    vehicle: VehicleId
    param: str
    state: AlarmState
    severity: Literal["YELLOW", "RED"]
    # last non-NOMINAL level in every state (RTN_UNACK/CLEARED: what it was, P-pf20): "RED" or
    # "<SEV>_<SIDE>", side in LOW HIGH RISING FALLING; "NOMINAL" only if no row had one
    level: str
    value: float | None  # the reading of the row `level` comes from (P-pf4: kept after an ACK)
    trigger_check: str
    limits_sha256: str
    set_sha256: str
    opened_at: UtcTime
    closed_at: UtcTime | None
    last: TransitionView
    ack_request: AckRequestView | None


class CommandView(_Sse):
    cmd_id: UUID
    vehicle: VehicleId
    name: str
    args: dict[str, ArgValue]
    hazardous: bool
    reason: str | None
    issued_by: str
    state: CmdState
    failure_reason: FailureReason | None
    queued_at: UtcTime
    sent_at: UtcTime | None
    received_at: UtcTime | None
    verified_at: UtcTime | None
    failed_at: UtcTime | None
    late_verified_at: UtcTime | None
    late_ack_at: UtcTime | None
    late_ack_accepted: bool | None
    outcome: Outcome | None


class EventView(_Sse):
    key: str
    vehicle: VehicleId | None
    kind: str
    t_onboard: UtcTime | None
    details: dict[str, JsonValue]


class Alarm(_Sse):
    kind: Literal["alarm"] = "alarm"
    alarm: AlarmView


class Cmd(_Sse):
    kind: Literal["cmd"] = "cmd"
    command: CommandView


class Link(_Sse):
    kind: Literal["link"] = "link"
    event: EventView


class Ground(_Sse):
    kind: Literal["ground"] = "ground"
    broker: Literal["down", "up"]


class Snapshot(_Sse):
    kind: Literal["snapshot"] = "snapshot"
    vehicles: dict[str, Tm]
    alarms: list[AlarmView] = []
    commands: list[CommandView] = []
    events: list[EventView] = []


class Hb(_Sse):
    kind: Literal["hb"] = "hb"
    server_ms: int
    processing: dict[str, bool] = Field(default_factory=dict)
