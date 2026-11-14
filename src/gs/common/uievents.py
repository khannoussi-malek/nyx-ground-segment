"""The single publisher of ui:events entries."""

from collections.abc import Sequence

import redis.asyncio

from gs.common.interfaces.sse import Alarm, Cmd, Link, Tm
from gs.common.interfaces.ui_event import Resync

UI_EVENTS = "ui:events"
UI_MAXLEN = 3000


async def publish_ui(
    r: redis.asyncio.Redis, msgs: Sequence[Tm | Alarm | Cmd | Link | Resync]
) -> None:
    if not msgs:
        return
    pipe = r.pipeline(transaction=False)
    for m in msgs:
        pipe.xadd(UI_EVENTS, {"data": m.model_dump_json()}, maxlen=UI_MAXLEN, approximate=True)
    await pipe.execute()
