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
    assert "is not fast enough to catch up" in bad
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


def _scaling(catch_up: float) -> scaling.Scaling:
    return scaling.Scaling(1500.0, 0.67, 20.0, 40.0, catch_up)


def test_zero_or_missing_cpu_is_not_measured() -> None:
    for cpu in (0.0, math.nan):
        text = scaling.render(scaling.extrapolate({**TOTALS, "cpu_fraction": cpu}))
        assert "not measured" in text and "fast enough" not in text
        assert "| Worker CPU | not measured |\n" in text
    got = scaling.flatten(
        {"cpu_pct": {}, "redis_used_memory": {"per_vehicle_hour": 1.0}, "ingest_ms": {"p95": None}}
    )
    text = scaling.render(scaling.extrapolate(got))
    assert "nan" not in text and "not measured" in text
    assert "| Ingest time p95 | not measured |\n" in text


def test_fast_enough_boundary() -> None:
    assert scaling.CATCHUP_MAX_S == 60.0
    at = scaling.render(_scaling(60.0))
    assert "clears in 60.00 s (limit 60 s)." in at
    assert "clears in 60.04 s" not in at  # 60.04 s must not render as "60.0 s"
    assert "needs 60.04 s (limit 60 s)" in scaling.render(_scaling(60.04))
    over = scaling.render(_scaling(60.1))
    assert "is not fast enough" in over and "60.10 s" in over
    # capacity 1500 vs load 1000 is exactly the 60 s boundary
    assert scaling.catch_up_s(30000, 1500, 1000) == 60.0
    assert scaling.extrapolate({**TOTALS, "cpu_fraction": 0.2 * 1000 / 1001}).catch_up > 60
