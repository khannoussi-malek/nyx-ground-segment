"""Frame generation and the absolute-schedule tick loop (D-SIM-01..03)."""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from gs.common.time import iso_z
from gs.simulator.buffer import FrameBuffer

PERIOD_S = 0.1
# (nominal, sigma) per design 3.3; test_sim_matches_mission_db keeps these inside the limits
NOMINALS: dict[str, tuple[float, float]] = {
    "cabin_pressure_kpa": (101.3, 0.02),
    "cabin_temp_c": (22.0, 0.05),
    "bus_voltage_v": (28.0, 0.05),
    "bus_current_a": (5.0, 0.05),
    "tank_pressure_bar": (20.0, 0.02),
    "rate_x_dps": (0.0, 0.01),
    "rate_y_dps": (0.0, 0.01),
    "rate_z_dps": (0.0, 0.01),
}


def next_seq(seq: int) -> int:
    return (seq + 1) % 2**32


def make_frame(vehicle: str, seq: int, t: datetime, rng: random.Random) -> str:
    values = {p: rng.gauss(nominal, sigma) for p, (nominal, sigma) in NOMINALS.items()}
    frame = {
        "vehicle": vehicle,
        "seq": seq,
        "t_onboard": iso_z(t),
        "mode": "TEST",
        "last_cmd_id": None,
        **values,
    }
    return json.dumps(frame, allow_nan=False)


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
) -> None:
    """Tick n fires at start + n*PERIOD_S; stalls are caught up, grid fixed (D-SIM-01)."""
    start, seq, n = monotonic(), 0, 0
    while not stop.is_set():
        await sleep(max(0, start + n * PERIOD_S - monotonic()))
        t = now()  # stamped at the tick, before the frame enters the buffer
        buf.append(f"tm:{vehicle}", make_frame(vehicle, seq, t, rng))
        seq, n = next_seq(seq), n + 1
