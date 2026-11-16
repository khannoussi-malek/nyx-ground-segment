import json
import math
from pathlib import Path

from perf import scaling

TOTALS = {
    "fps": 200.0,
    "cpu_fraction": 0.2,
    "redis_bytes_per_vehicle_hour": 2.0e6,
    "insert_ms": 40.0,
}


def test_capacity_and_catch_up() -> None:
    assert scaling.capacity_fps(200, 0.2) == 1000
    assert scaling.catch_up_s(6000, 1200, 200) == 6.0
    assert scaling.catch_up_s(6000, 400, 200) == 30.0
    assert scaling.catch_up_s(6000, 200, 200) == math.inf
    assert scaling.catch_up_s(6000, 100, 200) == math.inf


def test_extrapolate() -> None:
    s = scaling.extrapolate(TOTALS)
    assert s.cpu_at_target == 1.0
    assert s.redis_mb_hour == 20.0 * 1e6 / 1e6  # 10 vehicles x 2 MB
    assert s.insert_ms == 40.0
    assert s.capacity == 1000


def test_render_names_bottleneck_and_metastable_line() -> None:
    ok = scaling.render(scaling.extrapolate({**TOTALS, "cpu_fraction": 0.1}))
    assert "engine per vehicle is single-threaded" in ok
    assert "UI coalesces to 10 Hz renders" in ok and "1 min rollups at ~22 GB/day" in ok
    assert "a restarted worker is fast enough to catch up with the missed and ongoing load" in ok
    bad = scaling.render(scaling.extrapolate({**TOTALS, "cpu_fraction": 1.0}))
    assert "fast enough to catch up" not in bad
    assert "metastable" in bad


def test_main_reads_totals(tmp_path: Path) -> None:
    (tmp_path / "totals.json").write_text(
        json.dumps(
            {
                "cpu_pct": {
                    "ingest-1": {"mean": 10.0, "max": 30.0},
                    "ingest-2": {"mean": 10.0, "max": 20.0},
                },
                "redis_used_memory": {"per_vehicle_hour": 1.0e6},
                "ingest_ms": {"p50": 5.0, "p95": 40.0},
                "frames_stored": 60000,
            }
        )
    )
    out = tmp_path / "scaling.md"
    scaling.main(tmp_path, out)
    assert "1000 frames/s" in out.read_text()
