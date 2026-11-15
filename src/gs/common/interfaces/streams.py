"""Redis stream payloads: telecommands and their acknowledgements."""

from typing import Annotated
from uuid import UUID

from pydantic import ConfigDict, Field

from gs.common.interfaces.base import StrictModel, UtcTime

ArgValue = str | int | float
ACK_OK = "OK"
ACK_INVALID_ARGS = "INVALID_ARGS"
ACK_SCHEMA = "SCHEMA"


class Tc(StrictModel):
    model_config = ConfigDict(extra="forbid")

    cmd_id: UUID
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
    args: dict[str, ArgValue]
    issued_by: Annotated[str, Field(min_length=1)]
    issued_at: UtcTime
    reason: str | None


class TcAck(StrictModel):
    model_config = ConfigDict(extra="forbid")

    cmd_id: UUID
    accepted: bool
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")]
    reason: str | None
    t_onboard: UtcTime
