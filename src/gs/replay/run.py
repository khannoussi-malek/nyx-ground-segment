"""Stream recorded truth in position order, paced on the vehicle's clock."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from minio import Minio

from gs.archiver.hours import HOUR, hour_of
from gs.archiver.window import ArchiveCorrupt, window_hours
from gs.common.interfaces.replay import (
    ReplayAlarm,
    ReplayClock,
    ReplayEnd,
    ReplayLink,
    ReplayMessage,
    ReplayStart,
    ReplayTm,
)
from gs.common.interfaces.sse import EventView
from gs.common.missiondb import MissionDb
from gs.replay.rows import (
    alarm_after,
    initial_alarms,
    los_open_at,
    position,
    recorded_alarm_rows,
    recorded_event_rows,
)

CLOCK_STEP_S = 0.1
Kind = Literal["frame", "alarm", "ack", "event"]
Item = tuple[Kind, dict[str, Any]]


async def replay_messages(
    conn: psycopg.AsyncConnection[Any],
    s3: Minio,
    bucket: str,
    mdb: MissionDb,
    replay_id: UUID,
    vehicle: str,
    t_from: datetime,
    t_to: datetime,
    speed: Literal[1, 10],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncGenerator[ReplayMessage]:
    alarms = {a.alarm_id: a for a in await initial_alarms(conn, vehicle, t_from)}
    yield ReplayStart(
        replay_id=replay_id, vehicle=vehicle, t_from=t_from, t_to=t_to, speed=speed,
        alarms=list(alarms.values()), los_open=await los_open_at(conn, vehicle, t_from),
    )  # fmt: skip
    recorded: list[Item] = [
        ("ack" if r["kind"] == "ACK" else "alarm", r)
        for r in await recorded_alarm_rows(conn, vehicle, t_from, t_to)
    ]
    recorded.extend(
        ("event", e.model_dump() | {"id": i})
        for i, e in enumerate(await recorded_event_rows(conn, vehicle, t_from, t_to))
    )
    pending = deque(sorted(recorded, key=lambda item: position(*item)))
    clock = t_from

    async def advance(target: datetime) -> AsyncIterator[ReplayClock]:
        nonlocal clock
        step = timedelta(seconds=CLOCK_STEP_S * speed)
        while clock < target:
            following = min(clock + step, target)
            await sleep((following - clock).total_seconds() / speed)
            clock = following
            yield ReplayClock(t=clock)

    def message(kind: Kind, row: dict[str, Any]) -> ReplayTm | ReplayAlarm | ReplayLink:
        if kind == "frame":
            cmd = row["last_cmd_id"]
            return ReplayTm(
                vehicle=vehicle, seq=row["seq"], t_onboard=row["t_onboard"], mode=row["mode"],
                last_cmd_id=UUID(cmd) if isinstance(cmd, str) else cmd,
                values={p: row[p] for p in mdb.param_names},
                severity={a.param: a.severity for a in alarms.values()},
            )  # fmt: skip
        if kind == "event":
            return ReplayLink(event=EventView(**{k: v for k, v in row.items() if k != "id"}))
        aid = row["alarm_id"]
        alarm = alarm_after(alarms.get(aid), row)
        if alarm.state == "CLEARED":
            alarms.pop(aid, None)
        else:
            alarms[aid] = alarm
        return ReplayAlarm(alarm=alarm)

    try:
        async for table in window_hours(conn, s3, bucket, vehicle, t_from, t_to, mdb.param_names):
            frames = table.to_pylist()
            end = hour_of(frames[0]["t_onboard"]) + HOUR
            items: list[Item] = [("frame", row) for row in frames]
            while pending and position(*pending[0])[0] < end:
                items.append(pending.popleft())
            for kind, row in sorted(items, key=lambda item: position(*item)):
                async for tick in advance(position(kind, row)[0]):
                    yield tick
                yield message(kind, row)
        for kind, row in pending:
            async for tick in advance(position(kind, row)[0]):
                yield tick
            yield message(kind, row)
        async for tick in advance(t_to):
            yield tick
    except ArchiveCorrupt:
        yield ReplayEnd(reason="archive_corrupt", detail="archive object failed hash check")
        return
    yield ReplayEnd(reason="done")
