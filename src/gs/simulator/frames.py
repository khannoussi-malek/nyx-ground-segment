"""Frame generation and the absolute-schedule tick loop (D-SIM-01..03)."""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from gs.common.time import iso_z
from gs.simulator.buffer import FrameBuffer
from gs.simulator.commands import Cabin, CommandBox, cabin_tick, execute
from gs.simulator.faults import LEAK_KPA_PER_S, Active, FaultBoard, apply
from gs.simulator.nominals import NOMINALS as NOMINALS

PERIOD_S = 0.1


def next_seq(seq: int) -> int:
    return (seq + 1) % 2**32


def draw_values(rng: random.Random) -> dict[str, float]:
    return {p: rng.gauss(nominal, sigma) for p, (nominal, sigma) in NOMINALS.items()}


def build_frame(
    vehicle: str,
    seq: int,
    t: datetime,
    values: Mapping[str, float],
    last_cmd_id: UUID | None = None,
) -> str:
    frame = {
        "vehicle": vehicle,
        "seq": seq,
        "t_onboard": iso_z(t),
        "mode": "TEST",
        "last_cmd_id": str(last_cmd_id) if last_cmd_id else None,
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


def _find(active: tuple[Active, ...], name: str) -> Active | None:
    return next((a for a in active if a.name == name), None)


async def tick_loop(
    vehicle: str,
    buf: FrameBuffer,
    rng: random.Random,
    stop: asyncio.Event,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    now: Callable[[], datetime] = _utcnow,
    faults: FaultBoard | None = None,
    box: CommandBox | None = None,
    period_s: float = PERIOD_S,
    ledger: Callable[[str, int, datetime, str], object] | None = None,
) -> None:
    """Tick n fires at start + n*period_s; stalls are caught up, grid fixed (D-SIM-01).

    With a `box`, each tick runs <= 1 command after its frame; the frame carries the command
    executed in an earlier tick (D-SIM-06) and its acks follow the frame in the same buffer.
    `ledger(vehicle, seq, t, fate)` is told what became of each frame: sent (once it is in Redis),
    dropout, overflow (the buffer was full) and, per extra entry, duplicate or malformed (D-EXT-15).
    """
    board = faults or FaultBoard()
    note = ledger or (lambda *_: None)

    def put(vehicle: str, seq: int, t: datetime, data: str, fate: str) -> None:
        # the fate is written when the XADD succeeded (send_loop), so a frame still in the buffer
        # at shutdown never reads as sent; a full buffer is told at once
        if not buf.append(stream, data, lambda: note(vehicle, seq, t, fate)):
            note(vehicle, seq, t, "overflow")

    start, seq, n = monotonic(), 0, 0
    last: dict[str, float] = {}  # values of the previous frame, what a freeze repeats
    frozen: dict[str, float] | None = None
    cabin, prev_mono = Cabin(), start
    stream = f"tm:{vehicle}"
    while not stop.is_set():
        await sleep(max(0, start + n * period_s - monotonic()))
        t = now()  # stamped at the tick, before the frame enters the buffer
        mono = monotonic()
        cabin, prev_mono = cabin_tick(cabin, mono - prev_mono, mono), mono
        active = board.active(vehicle, mono)
        raw = draw_values(rng)
        raw["cabin_pressure_kpa"] += cabin.offset_kpa  # before faults: a freeze repeats it
        if any(a.name == "freeze" for a in active):
            frozen = frozen or last or raw  # first frame emitted when nothing came before
        else:
            frozen = None
        values = apply(raw, active, mono, frozen)
        if jump := board.take_once(vehicle, "seq_jump"):
            seq = (seq + int(str(jump.params["by"]))) % 2**32
        dup, bad = board.take_once(vehicle, "duplicate"), board.take_once(vehicle, "malformed")
        dropout = _find(active, "dropout") is not None
        if dropout:
            note(vehicle, seq, t, "dropout")
        else:  # dropout: seq advances, nothing sent
            data = build_frame(vehicle, seq, t, values, box.last_cmd_id if box else None)
            put(vehicle, seq, t, data, "sent")
            for _ in range(int(str(dup.params["count"])) if dup else 0):
                put(vehicle, seq, t, data, "duplicate")
            if bad:
                put(vehicle, seq, t, malformed_entry(vehicle, str(bad.params["kind"])), "malformed")
        if box:
            hold = _find(active, "hold_exec")
            hold_s = float(str(hold.params["duration_s"])) if hold else None
            paused = _find(active, "pause") is not None
            acks, done = box.step(mono, t, paused, hold_s, dropout)
            for ack in acks:
                buf.append(f"tcack:{vehicle}", ack.model_dump_json())
            if done:
                leak = _find(active, "leak")
                leak_offset = LEAK_KPA_PER_S * (mono - leak.started) if leak else None
                cabin = execute(done, cabin, leak_offset, mono)
                if done.name == "isolate_valve":
                    board.clear(vehicle, "leak")
        last = values
        seq, n = next_seq(seq), n + 1
