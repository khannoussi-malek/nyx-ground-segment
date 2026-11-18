"""Replay SSE messages (T4-g): the live shapes flagged `replay: true`, plus replay_start, clock and
replay_end. A console keeps them in a store separate from the live one (D-REP-03, D-UI-16)."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, RootModel

from gs.common.interfaces.base import UtcTime, VehicleId
from gs.common.interfaces.sse import Alarm, AlarmView, Link, Tm, _Sse


class ReplayStart(_Sse):
    kind: Literal["replay_start"] = "replay_start"
    replay: Literal[True] = True
    replay_id: UUID
    vehicle: VehicleId
    t_from: UtcTime
    t_to: UtcTime
    speed: Literal[1, 10]
    alarms: list[AlarmView]  # the recorded alarm state at t_from
    los_open: bool


class ReplayTm(Tm):
    replay: Literal[True] = True
    # parameters with an open recorded alarm; absent = NOMINAL
    severity: dict[str, Literal["YELLOW", "RED"]]


class ReplayAlarm(Alarm):
    replay: Literal[True] = True


class ReplayLink(Link):
    replay: Literal[True] = True


class ReplayClock(_Sse):
    kind: Literal["clock"] = "clock"
    replay: Literal[True] = True
    t: UtcTime  # replay t_onboard now


class ReplayEnd(_Sse):
    kind: Literal["replay_end"] = "replay_end"
    replay: Literal[True] = True
    reason: Literal["done", "stopped", "archive_corrupt"]
    detail: str | None = None


ReplayMessage = ReplayStart | ReplayTm | ReplayAlarm | ReplayLink | ReplayClock | ReplayEnd


class ReplayEvent(RootModel[Annotated[ReplayMessage, Field(discriminator="kind")]]):
    pass
