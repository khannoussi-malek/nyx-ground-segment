from gs.common.interfaces.base import U32, Mode, StrictModel, UtcTime, VehicleId
from gs.common.interfaces.dlq import DlqReason, DlqRecord
from gs.common.interfaces.frame import FrameHeader, build_frame_model, frame_schema
from gs.common.interfaces.replay import (
    ReplayAlarm,
    ReplayClock,
    ReplayEnd,
    ReplayEvent,
    ReplayLink,
    ReplayStart,
    ReplayTm,
)
from gs.common.interfaces.sse import Alarm, Cmd, Ground, Hb, Hello, Link, Snapshot, Tm
from gs.common.interfaces.streams import Tc, TcAck
from gs.common.interfaces.ui_event import Resync, UiEvent

INTERFACE_MODELS = (
    FrameHeader,
    DlqRecord,
    Hello,
    Snapshot,
    Tm,
    Hb,
    Tc,
    TcAck,
    UiEvent,
    Alarm,
    Cmd,
    Link,
    Ground,
    ReplayStart,
    ReplayTm,
    ReplayAlarm,
    ReplayLink,
    ReplayClock,
    ReplayEnd,
    ReplayEvent,
)

__all__ = [
    "Alarm",
    "Cmd",
    "Ground",
    "Link",
    "Resync",
    "ReplayAlarm",
    "ReplayClock",
    "ReplayEnd",
    "ReplayEvent",
    "ReplayLink",
    "ReplayStart",
    "ReplayTm",
    "Tc",
    "TcAck",
    "UiEvent",
    "INTERFACE_MODELS",
    "U32",
    "DlqReason",
    "DlqRecord",
    "FrameHeader",
    "frame_schema",
    "Hb",
    "Hello",
    "Mode",
    "Snapshot",
    "StrictModel",
    "Tm",
    "UtcTime",
    "VehicleId",
    "build_frame_model",
]
