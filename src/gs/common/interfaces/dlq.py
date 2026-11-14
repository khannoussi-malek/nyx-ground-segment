from typing import Literal

from pydantic import ConfigDict, Field

from gs.common.interfaces.base import StrictModel, UtcTime

DlqReason = Literal["json_invalid", "schema", "vehicle_mismatch", "t_onboard_range", "poison"]


class DlqRecord(StrictModel):
    model_config = ConfigDict(extra="forbid")

    reason: DlqReason
    detail: str
    raw: str
    stream: str
    entry_id: str
    deliveries: int = Field(ge=1)
    first_seen: UtcTime
    dlq_at: UtcTime
