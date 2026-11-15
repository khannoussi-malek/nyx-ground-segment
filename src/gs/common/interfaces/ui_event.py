"""The ui:events stream payload: every message the ingest/API side fans out to SSE."""

from typing import Annotated, Literal

from pydantic import Field, RootModel

from gs.common.interfaces.base import VehicleId
from gs.common.interfaces.sse import Alarm, Cmd, Link, Tm, _Sse


class Resync(_Sse):
    kind: Literal["resync"] = "resync"
    vehicle: VehicleId


UiMessage = Tm | Alarm | Cmd | Link | Resync


class UiEvent(RootModel[Annotated[UiMessage, Field(discriminator="kind")]]):
    pass
