"""Alarm types shared by the engine (Track A) and the ingest worker (Track B/Z)."""

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Literal
from uuid import UUID


class Level(IntEnum):
    NOMINAL = 0
    YELLOW = 1
    RED = 2


AlarmState = Literal["UNACK", "ACKED", "RTN_UNACK", "CLEARED"]
TransitionKind = Literal["RAISE", "ESCALATE", "DEESCALATE", "RTN", "ACK", "CLEAR"]


@dataclass(frozen=True)
class Transition:
    key: str
    alarm_id: UUID
    vehicle: str
    param: str
    kind: TransitionKind
    check_kind: str | None
    level: str | None
    severity: Level
    alarm_state: AlarmState
    acked_sev: Level | None
    trigger_check: str
    trigger_seq: int | None
    trigger_t: datetime | None
    alert: bool
    by_user: str
    note: str | None
    opened_at: datetime
    closed_at: datetime | None
    # the trigger slot's reading on the transition frame; None for ACK/RTN/CLEAR and when that
    # slot gave no reading on that frame (ruling R-b)
    value: float | None = None
    check_set: str = "default"  # 'default' or the mode whose limits judged it (D-EXT-06)


@dataclass(frozen=True)
class OpenAlarm:
    alarm_id: UUID
    param: str
    state: AlarmState
    severity: Level
    acked_sev: Level | None
    trigger_check: str
    opened_at: datetime
