from typing import Literal
from uuid import UUID

from pydantic import ConfigDict

from gs.common.interfaces.base import U32, Mode, StrictModel, UtcTime, VehicleId


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


class Snapshot(_Sse):
    kind: Literal["snapshot"] = "snapshot"
    vehicles: dict[str, Tm]


class Hb(_Sse):
    kind: Literal["hb"] = "hb"
    server_ms: int
