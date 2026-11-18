"""Scaling analysis (D-PERF-09): run P totals extrapolated to 10 vehicles x 100 Hz, naming the
first bottleneck and its fix. `uv run python -m perf.scaling <run dir>` writes
docs/perf/scaling.md."""

import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from perf.run_n import REPO, VEHICLES
from perf.run_p import DURATION_S, RATE_HZ, WORKERS

TARGET_VEHICLES, TARGET_FPS = 10, 1000  # 10 vehicles x 100 Hz
RESTART_S = 30.0  # outage a restarted worker must make up: this many seconds of target load
CATCHUP_MAX_S = (
    60.0  # ruling G4-16: "fast enough" = the backlog clears within this (capacity >= 1.5x load)
)
OUT = REPO / "docs" / "perf" / "scaling.md"


class Scaling(NamedTuple):
    capacity: float  # frames/s one worker sustains at 100 % of a core
    cpu_at_target: float  # cores needed at 1000 frames/s
    redis_mb_hour: float  # Redis growth, 10 vehicles
    insert_ms: float  # p95 ingest time (t_received - t_onboard), informative
    catch_up: float  # s to clear a RESTART_S backlog at target load; inf = metastable


def capacity_fps(fps: float, cpu_fraction: float) -> float:
    """Frames/s at 100 % CPU, from `fps` measured at `cpu_fraction` (linear in load)."""
    return fps / cpu_fraction


def catch_up_s(backlog_frames: float, capacity: float, load: float) -> float:
    """Time to drain the backlog while the load keeps arriving; inf when capacity <= load
    (metastable: the backlog never clears, Huang et al. 2022 p. 7)."""
    return backlog_frames / (capacity - load) if capacity > load else math.inf


def _rest(totals: Mapping[str, float]) -> tuple[float, float]:
    return totals["redis_bytes_per_vehicle_hour"] * TARGET_VEHICLES / 1e6, totals["insert_ms"]


def extrapolate(totals: Mapping[str, float]) -> Scaling:
    cpu = totals["cpu_fraction"]
    if not cpu > 0:  # no samples or an idle reading: nothing to extrapolate from
        return Scaling(math.nan, math.nan, *_rest(totals), math.nan)
    cap = capacity_fps(totals["fps"], cpu)
    return Scaling(
        cap,
        TARGET_FPS / cap,
        *_rest(totals),
        catch_up_s(RESTART_S * TARGET_FPS, cap, TARGET_FPS),
    )


def _num(x: float, fmt: str, unit: str = "") -> str:
    return "not measured" if math.isnan(x) else f"{x:{fmt}}{unit}"


def render(s: Scaling) -> str:
    if math.isnan(s.catch_up):
        headroom = "Not measured: no usable worker CPU samples in the run."
    elif s.catch_up <= CATCHUP_MAX_S:
        headroom = (
            "Headroom: a restarted worker is fast enough to catch up with the missed and "
            f"ongoing load: {RESTART_S:.0f} s of backlog clears in {s.catch_up:.2f} s "
            f"(limit {CATCHUP_MAX_S:.0f} s)."
        )
    else:
        headroom = (
            "Headroom: a restarted worker is not fast enough to catch up with the missed and "
            f"ongoing load: {RESTART_S:.0f} s of backlog needs "
            f"{'never' if math.isinf(s.catch_up) else f'{s.catch_up:.2f} s'} "
            f"(limit {CATCHUP_MAX_S:.0f} s). If capacity does not exceed the load the backlog "
            "never clears (metastable failure, Huang et al. 2022 p. 7): add workers."
        )
    share = TARGET_VEHICLES // len(WORKERS)
    headroom += (
        f"\n\nPer worker: {len(WORKERS)} workers, {share} vehicles per worker = "
        f"{share * RATE_HZ} frames/s of the fleet's {TARGET_FPS} frames/s; a restarted worker "
        "takes its own vehicles back.\n\n"
        f"Model: backlog = {RESTART_S:.0f} s of {TARGET_FPS} frames/s; capacity = one worker "
        "at 100 % of a core; catch-up = backlog / (capacity - load); metastable when capacity "
        "<= load."
    )
    cpu = (
        "not measured"
        if math.isnan(s.cpu_at_target)
        else f"{s.cpu_at_target * 100:.0f} % of one core (capacity {s.capacity:.0f} frames/s)"
    )
    growth = (
        "not measurable (streams capped at MAXLEN)"
        if s.redis_mb_hour <= 0  # a before/after delta on capped streams is noise
        else _num(s.redis_mb_hour, ".1f", " MB/hour")
    )
    return (
        "# Scaling analysis (D-PERF-09)\n\n"
        f"Extrapolated linearly from run P ({len(VEHICLES)} vehicles x {RATE_HZ} Hz, "
        f"{DURATION_S} s) to {TARGET_VEHICLES} vehicles x {RATE_HZ} Hz = {TARGET_FPS} frames/s. "
        "No per-stage instrumentation (SR-06).\n\n"
        "| Quantity | At 1000 frames/s |\n|---|---|\n"
        f"| Worker CPU | {cpu} |\n"
        f"| Redis growth | {growth} |\n"
        f"| Ingest time p95 | {_num(s.insert_ms, '.1f', ' ms')} |\n\n"
        f"## Headroom\n\n{headroom}\n\n"
        "## First bottleneck and fix\n\n"
        "1. The engine per vehicle is single-threaded: split it by parameter group.\n"
        "2. The UI coalesces to 10 Hz renders so the browser keeps up.\n"
        "3. Storage: 1 min rollups at ~22 GB/day raw.\n"
    )


def flatten(t: Mapping[str, object]) -> dict[str, float]:
    cpu = t["cpu_pct"]
    assert isinstance(cpu, dict)
    mem = t["redis_used_memory"]
    ing = t["ingest_ms"]
    assert isinstance(mem, dict) and isinstance(ing, dict)
    pvh = mem["per_vehicle_hour"]
    return {
        "fps": float(len(VEHICLES) * RATE_HZ),
        "cpu_fraction": sum(w["mean"] for w in cpu.values()) / 100,
        "redis_bytes_per_vehicle_hour": math.nan if pvh is None else float(pvh),
        "insert_ms": math.nan if ing["p95"] is None else float(ing["p95"]),
    }


def main(run_dir: Path, out: Path = OUT) -> None:
    totals = json.loads((run_dir / "totals.json").read_text())
    out.write_text(render(extrapolate(flatten(totals))))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
