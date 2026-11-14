from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

from gs.common.time import iso_z


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, frozen=True)


def _utc_only(dt: datetime) -> datetime:
    if dt.utcoffset() != timedelta(0):
        raise ValueError("timestamp must have a UTC (+00:00 / Z) offset")
    return dt.astimezone(UTC)


U32 = Annotated[int, Field(ge=0, le=2**32 - 1)]
UtcTime = Annotated[
    AwareDatetime,
    AfterValidator(_utc_only),
    PlainSerializer(iso_z, return_type=str, when_used="json"),
]
Mode = Literal["SAFE", "STANDBY", "TEST", "FAULT"]
VehicleId = Annotated[str, Field(pattern=r"^[A-Z0-9-]{1,32}$")]
