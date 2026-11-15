from datetime import UTC, datetime
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


def schema_reject(raw: str, stream: str, entry_id: str, detail: str, now: datetime) -> DlqRecord:
    return DlqRecord(
        reason="schema",
        detail=detail,
        raw=raw,
        stream=stream,
        entry_id=entry_id,
        deliveries=1,
        first_seen=datetime.fromtimestamp(int(entry_id.split("-")[0]) / 1000, UTC),
        dlq_at=now,
    )
