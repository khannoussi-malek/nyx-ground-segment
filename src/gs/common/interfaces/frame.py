from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ConfigDict, ValidationInfo, create_model, field_validator
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

    # Field-level (not model-level) so these still fire when another field fails the schema:
    # classify() needs vehicle_mismatch / t_onboard_range to outrank "schema" (D-ING-18).
    @field_validator("vehicle")
    @classmethod
    def _vehicle_matches_stream(cls, v: str, info: ValidationInfo) -> str:
        ctx = info.context
        if ctx and v != ctx["stream_vehicle"]:
            raise PydanticCustomError(
                "vehicle_mismatch",
                "vehicle {vehicle} does not match stream vehicle {stream}",
                {"vehicle": v, "stream": ctx["stream_vehicle"]},
            )
        return v

    @field_validator("t_onboard")
    @classmethod
    def _t_onboard_in_window(cls, v: datetime, info: ValidationInfo) -> datetime:
        ctx = info.context
        if ctx and not ctx["now"] - T_ONBOARD_PAST <= v <= ctx["now"] + T_ONBOARD_FUTURE:
            raise PydanticCustomError(
                "t_onboard_range",
                "t_onboard {t} outside [now-7d, now+5min]",
                {"t": v.isoformat()},
            )
        return v


def build_frame_model(params: Sequence[str]) -> type[FrameHeader]:
    """Frame model with one nullable float per mission parameter (missing -> NULL, D-ING-20)."""
    fields = {p: (float | None, None) for p in params}
    return create_model("Frame", __base__=FrameHeader, **fields)  # type: ignore[call-overload,no-any-return]
