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
from perf.run_p import DURATION_S, RATE_HZ

TARGET_VEHICLES, TARGET_FPS = 10, 1000  # 10 vehicles x 100 Hz
RESTART_S = 30.0  # outage a restarted worker must make up: this many seconds of target load
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


def extrapolate(totals: Mapping[str, float]) -> Scaling:
    cap = capacity_fps(totals["fps"], totals["cpu_fraction"])
    return Scaling(
        cap,
        TARGET_FPS / cap,
        totals["redis_bytes_per_vehicle_hour"] * TARGET_VEHICLES / 1e6,
        totals["insert_ms"],
        catch_up_s(RESTART_S * TARGET_FPS, cap, TARGET_FPS),
    )


def render(s: Scaling) -> str:
    headroom = (
        "Headroom: a restarted worker is fast enough to catch up with the missed and ongoing "
        "load: "
        f"{RESTART_S:.0f} s of backlog clears in {s.catch_up:.1f} s."
        if math.isfinite(s.catch_up)
        else "Capacity does not exceed the load, so a restarted worker never catches up "
        "(metastable failure, Huang et al. 2022 p. 7): add workers before 10 vehicles."
    )
    redis = "not measured" if math.isnan(s.redis_mb_hour) else f"{s.redis_mb_hour:.1f} MB/hour"
    return (
        "# Scaling analysis (D-PERF-09)\n\n"
        f"Extrapolated linearly from run P ({len(VEHICLES)} vehicles x {RATE_HZ} Hz, "
        f"{DURATION_S} s) to {TARGET_VEHICLES} vehicles x {RATE_HZ} Hz = {TARGET_FPS} frames/s. "
        "No per-stage instrumentation (SR-06).\n\n"
        "| Quantity | At 1000 frames/s |\n|---|---|\n"
        f"| Worker CPU | {s.cpu_at_target * 100:.0f} % of one core "
        f"(capacity {s.capacity:.0f} frames/s) |\n"
        f"| Redis growth | {redis} |\n"
        f"| Ingest time p95 | {s.insert_ms:.1f} ms |\n\n"
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
