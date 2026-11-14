from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, ValidationInfo, create_model, model_validator
from pydantic_core import PydanticCustomError

from gs.common.interfaces.base import U32, Mode, StrictModel, UtcTime, VehicleId

T_ONBOARD_PAST = timedelta(days=7)
T_ONBOARD_FUTURE = timedelta(minutes=5)


class FrameHeader(StrictModel):
    model_config = ConfigDict(extra="allow")

    vehicle: VehicleId
    seq: U32
    t_onboard: UtcTime
    mode: Mode
    last_cmd_id: UUID | None

    @model_validator(mode="after")
    def _runtime_checks(self, info: ValidationInfo) -> Self:
        ctx = info.context
        if not ctx:  # no context: pure shape validation
            return self
        if self.vehicle != ctx["stream_vehicle"]:
            raise PydanticCustomError(
                "vehicle_mismatch",
                "vehicle {vehicle} does not match stream vehicle {stream}",
                {"vehicle": self.vehicle, "stream": ctx["stream_vehicle"]},
            )
        now: datetime = ctx["now"]
        if not now - T_ONBOARD_PAST <= self.t_onboard <= now + T_ONBOARD_FUTURE:
            raise PydanticCustomError(
                "t_onboard_range",
                "t_onboard {t} outside [now-7d, now+5min]",
                {"t": self.t_onboard.isoformat()},
            )
        return self


def build_frame_model(params: Sequence[str]) -> type[FrameHeader]:
    """Frame model with one nullable float per mission parameter (missing -> NULL, D-ING-20)."""
    fields = {p: (float | None, None) for p in params}
    return create_model("Frame", __base__=FrameHeader, **fields)  # type: ignore[call-overload,no-any-return]
