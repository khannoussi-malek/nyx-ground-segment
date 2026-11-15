"""Simulator command path: read tc:<v>, ack on tcack:<v>, run <= 1 per tick (D-SIM-06, D-SIM-10).

The tick loop owns the per-vehicle CommandBox; tc_loop only parses and queues (schema rejects too).
step() emits every ack on the tick clock, through the same ordered FrameBuffer as the frames, so
pause, dropout and a Redis outage treat acks and frames the same way.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

import redis.asyncio
import redis.exceptions
from pydantic import ValidationError

from gs.commanding.args import check_args
from gs.common.interfaces.streams import ACK_INVALID_ARGS, ACK_OK, ACK_SCHEMA, Tc, TcAck
from gs.common.missiondb import Command
from gs.simulator.nominals import NOMINALS

ISOLATE_KPA_PER_S, VENT_KPA_PER_S = 0.5, -2.0
FLOOR_KPA = -NOMINALS["cabin_pressure_kpa"][0]  # a vented cabin stops at 0 kPa absolute
GROUP = "sim"
RETRY_S = 1.0
_EPS = 1e-9  # ponytail: float slack so a 0.1 s grid hits sums like 0.2 + 1.6 exactly
log = logging.getLogger(__name__)


def parse_tc(raw: str) -> Tc | tuple[UUID | None, str]:
    """The Tc, or (its cmd_id if readable, the validation message) (D-ING-27)."""
    try:
        return Tc.model_validate_json(raw)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'tc'}: {x['msg']}" for x in e.errors())
    try:
        cmd_id: UUID | None = UUID(json.loads(raw)["cmd_id"])
    except ValueError, KeyError, TypeError, AttributeError, RecursionError:  # not a UUID string
        cmd_id = None
    return cmd_id, msg


@dataclass(frozen=True)
class Cabin:
    """Commanded cabin pressure offset from nominal (kPa), moving at `rate` kPa/s."""

    offset_kpa: float = 0.0
    rate: float = 0.0
    until: float | None = None  # monotonic end of a negative rate (vent)


def cabin_tick(c: Cabin, dt: float, now: float) -> Cabin:
    """Advance by `dt` ending at `now`: a positive rate stops at 0, a negative one at `until`."""
    if c.rate > 0:
        offset = c.offset_kpa + c.rate * dt
        return Cabin() if offset >= -_EPS else replace(c, offset_kpa=offset)
    if c.rate < 0 and c.until is not None:
        span = max(0.0, min(now, c.until) - (now - dt))
        offset = max(FLOOR_KPA, c.offset_kpa + c.rate * span)
        return Cabin(offset) if now >= c.until - _EPS else replace(c, offset_kpa=offset)
    return c


def execute(tc: Tc, cabin: Cabin, leak_offset: float | None, now: float) -> Cabin:
    """Effect of an accepted command; the caller clears the `leak` fault on isolate_valve."""
    if tc.name == "isolate_valve":
        return Cabin(cabin.offset_kpa + (leak_offset or 0.0), ISOLATE_KPA_PER_S)
    if tc.name == "vent_cabin":
        return Cabin(cabin.offset_kpa, VENT_KPA_PER_S, now + float(tc.args["duration_s"]))
    return cabin  # ponytail: every mission command has an effect above; new ones add theirs


class CommandBox:
    """Per-vehicle command state shared by tc_loop (fills the inbox) and the tick loop (step)."""

    def __init__(self, commands: Mapping[str, Command]) -> None:
        self.commands = commands
        # a Tc, or (cmd_id, message) of a schema-rejected entry: answered SCHEMA by step()
        self.inbox: asyncio.Queue[Tc | tuple[UUID, str]] = asyncio.Queue()
        self.held: tuple[tuple[float, Tc], ...] = ()  # (execute at, tc) under hold_exec
        self.last_cmd_id: UUID | None = None
        self.schema_errors = 0

    def _run(self, tc: Tc) -> Tc:
        self.last_cmd_id = tc.cmd_id
        return tc

    def step(
        self, now: float, t: datetime, paused: bool, hold_s: float | None, dropout: bool
    ) -> tuple[list[TcAck], Tc | None]:
        """One tick: at most one command executed; returns (acks to send, the executed tc)."""
        if paused:  # neither acknowledged nor executed (D-SIM-07)
            return [], None
        due = next((h for h in self.held if h[0] <= now + _EPS), None)
        if due is not None:  # a held command takes this tick's slot; new ones wait
            self.held = tuple(h for h in self.held if h is not due)
            return [], self._run(due[1])
        try:
            tc = self.inbox.get_nowait()
        except asyncio.QueueEmpty:
            return [], None
        if isinstance(tc, tuple):  # D-ING-27: the tracker records REJECTED
            nack = TcAck(cmd_id=tc[0], accepted=False, code=ACK_SCHEMA, reason=tc[1], t_onboard=t)
            return ([] if dropout else [nack]), None
        err = check_args(self.commands.get(tc.name), tc.args)
        ack = TcAck(
            cmd_id=tc.cmd_id,
            accepted=err is None,
            code=ACK_OK if err is None else ACK_INVALID_ARGS,
            reason=err,
            t_onboard=t,
        )
        acks = [] if dropout else [ack]  # the link is down both ways; execution goes on
        if err is not None:
            return acks, None
        if hold_s is not None:
            self.held = (*self.held, (now + hold_s, tc))
            return acks, None
        return acks, self._run(tc)


async def _ensure_group(r: redis.asyncio.Redis, stream: str, at_start: bool) -> None:
    """Commands written while the simulator was not listening are never executed (P-pf29)."""
    try:
        await r.xgroup_create(stream, GROUP, id="$", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
        if at_start:  # a restart: skip what queued up while down (the tracker said NOT_RECEIVED)
            await r.xgroup_setid(stream, GROUP, id="$")


async def tc_loop(
    r: redis.asyncio.Redis,
    vehicle: str,
    box: CommandBox,
    stop: asyncio.Event,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> None:
    """Read tc:<v> one entry at a time into the box (valid or schema-rejected); XACK either way.

    A reconnect after a Redis error keeps the group's place; only a start moves it to the end.
    """
    stream, need_group, at_start = f"tc:{vehicle}", True, True
    while not stop.is_set():
        try:
            if need_group:
                await _ensure_group(r, stream, at_start)
                need_group = at_start = False
            resp: Any = await r.xreadgroup(GROUP, GROUP, {stream: ">"}, count=1, block=1000)
            for _, entries in resp or []:
                for entry_id, fields in entries:
                    _take(vehicle, box, fields.get(b"data", b""), entry_id)
                    await r.xack(stream, GROUP, entry_id)
        except redis.exceptions.RedisError as e:  # down, wiped (NOGROUP) or ACL: retry
            log.warning("tc read failed for %s: %s", vehicle, e, extra={"vehicle": vehicle})
            need_group = True
            await sleep(RETRY_S)


def _take(vehicle: str, box: CommandBox, data: bytes, entry_id: object) -> None:
    got = parse_tc(data.decode("utf-8", errors="replace"))
    if isinstance(got, Tc):
        box.inbox.put_nowait(got)
        return
    cmd_id, msg = got
    box.schema_errors += 1
    log.warning(
        "tc entry %s rejected (schema): %s",
        entry_id,
        msg,
        extra={"vehicle": vehicle, "cmd_id": str(cmd_id) if cmd_id else None},
    )
    if cmd_id is not None:  # unreadable: no tcack, the command times out NOT_RECEIVED
        box.inbox.put_nowait((cmd_id, msg))
