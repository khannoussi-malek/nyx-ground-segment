"""Frame generation and the absolute-schedule tick loop (D-SIM-01..03)."""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from gs.common.time import iso_z
from gs.simulator.buffer import FrameBuffer
from gs.simulator.faults import FaultBoard, apply
from gs.simulator.nominals import NOMINALS as NOMINALS

PERIOD_S = 0.1


def next_seq(seq: int) -> int:
    return (seq + 1) % 2**32


def draw_values(rng: random.Random) -> dict[str, float]:
    return {p: rng.gauss(nominal, sigma) for p, (nominal, sigma) in NOMINALS.items()}


def build_frame(vehicle: str, seq: int, t: datetime, values: Mapping[str, float]) -> str:
    frame = {
        "vehicle": vehicle,
        "seq": seq,
        "t_onboard": iso_z(t),
        "mode": "TEST",
        "last_cmd_id": None,
        **values,
    }
    return json.dumps(frame, allow_nan=False)


def make_frame(vehicle: str, seq: int, t: datetime, rng: random.Random) -> str:
    return build_frame(vehicle, seq, t, draw_values(rng))


def malformed_entry(vehicle: str, kind: str) -> str:
    """Extra invalid entry: truncated JSON, or valid JSON with a string seq (A-R5-2)."""
    if kind == "json":
        return f'{{"vehicle": "{vehicle}", "seq": '
    return json.dumps({"vehicle": vehicle, "seq": "5"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def tick_loop(
    vehicle: str,
    buf: FrameBuffer,
    rng: random.Random,
    stop: asyncio.Event,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    now: Callable[[], datetime] = _utcnow,
    faults: FaultBoard | None = None,
) -> None:
    """Tick n fires at start + n*PERIOD_S; stalls are caught up, grid fixed (D-SIM-01)."""
    board = faults or FaultBoard()
    start, seq, n = monotonic(), 0, 0
    last: dict[str, float] = {}  # values of the previous frame, what a freeze repeats
    frozen: dict[str, float] | None = None
    stream = f"tm:{vehicle}"
    while not stop.is_set():
        await sleep(max(0, start + n * PERIOD_S - monotonic()))
        t = now()  # stamped at the tick, before the frame enters the buffer
        mono = monotonic()
        active = board.active(vehicle, mono)
        raw = draw_values(rng)
        if any(a.name == "freeze" for a in active):
            frozen = frozen or last or raw  # first frame emitted when nothing came before
        else:
            frozen = None
        values = apply(raw, active, mono, frozen)
        if jump := board.take_once(vehicle, "seq_jump"):
            seq = (seq + int(str(jump.params["by"]))) % 2**32
        dup, bad = board.take_once(vehicle, "duplicate"), board.take_once(vehicle, "malformed")
        if not any(a.name == "dropout" for a in active):  # dropout: seq advances, nothing sent
            data = build_frame(vehicle, seq, t, values)
            buf.append(stream, data)
            for _ in range(int(str(dup.params["count"])) if dup else 0):
                buf.append(stream, data)
            if bad:
                buf.append(stream, malformed_entry(vehicle, str(bad.params["kind"])))
        last = values
        seq, n = next_seq(seq), n + 1
