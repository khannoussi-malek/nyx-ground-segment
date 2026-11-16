from datetime import UTC, datetime

from perf.run_p import DURATION_S, LAG_LIMIT_S, RATE_HZ, lag_row, lag_verdict, totals

T = datetime(2026, 11, 15, 12, tzinfo=UTC)
GROUPS = [
    {"name": "other", "lag": 9, "pending": 9},
    {"name": "ingest", "lag": 12, "pending": 3},
]


def test_design_values() -> None:
    assert (DURATION_S, RATE_HZ, LAG_LIMIT_S) == (300, 100, 1.0)


def test_lag_row_takes_the_ingest_group_and_the_time_lag() -> None:
    assert lag_row(GROUPS, 0.25, T) == (T, 12, 3, 0.25)
    assert lag_row([], 0.25, T) == (T, 0, 0, 0.25)  # no group yet: nothing queued
    assert lag_row([{"name": b"ingest", "lag": None, "pending": 1}], 0.1, T) == (T, 0, 1, 0.1)


def test_lag_verdict_is_the_max_time_lag_under_one_second() -> None:
    ok = lag_verdict([lag_row(GROUPS, x, T) for x in (0.1, 0.99, 0.5)])
    assert ok.passed and ok.p95 == 0.99 and ok.limit == 1.0
    assert not lag_verdict([lag_row(GROUPS, x, T) for x in (0.1, 1.0)]).passed
    assert not lag_verdict([]).passed  # no samples is not a pass


def test_totals_from_hand_built_samples() -> None:
    t = totals(
        cpu={"ingest-1": [10.0, 30.0], "ingest-2": [20.0]},
        mem_before=100_000_000,
        mem_after=160_000_000,
        ingest_ms=[float(i) for i in range(1, 101)],
        frames=60_000,
        vehicles=2,
        duration_s=300,
    )
    assert t["cpu_pct"] == {
        "ingest-1": {"mean": 20.0, "max": 30.0},
        "ingest-2": {"mean": 20.0, "max": 20.0},
    }
    assert t["redis_used_memory"] == {
        "before": 100_000_000,
        "after": 160_000_000,
        "per_vehicle_hour": 360_000_000,
    }
    assert t["ingest_ms"] == {"p50": 50.0, "p95": 95.0} and t["frames_stored"] == 60_000


# --- lifecycle: main() with every external dependency faked --------------------------------
import threading  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
import redis  # noqa: E402

from perf import run_n, run_p  # noqa: E402

N = 5  # DURATION_S patched to 5 s: the run must hold >= 80 % of 5 samples per series


class _Db:
    def __enter__(self) -> _Db:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    time_lag: float = 0.1,
    restore_fails: bool = False,
    steps_fail: bool = False,
    cpu: bool = True,
) -> list[int]:
    rates: list[int] = []

    def set_rate(rate: int) -> None:
        rates.append(rate)
        if restore_fails and rate == 10:
            raise RuntimeError("compose up failed")

    def lag(r: Any, db: Any, rows: dict[str, list[run_p.LagRow]], until: float) -> None:
        for v in run_n.VEHICLES:
            rows[v].extend((T, 0, 0, time_lag) for _ in range(N))

    def steps(port: str, duration_s: int) -> None:
        if steps_fail:
            raise AssertionError("sim POST 500")

    def cpu_sampler(stop: threading.Event, out: dict[str, list[float]]) -> None:
        for w in run_p.WORKERS:
            out[w].extend([5.0] * N if cpu else [])

    def samples(db: Any, query: str, args: tuple[object, ...]) -> list[tuple[datetime, float]]:
        return [(T, 1.0)] * (3 if query == run_n.INGEST_SQL else 1)

    fakes: dict[str, Callable[..., Any]] = {
        "read_env": lambda: dict.fromkeys(
            ["GS_APP_PASSWORD", "DB_HOST_PORT", "REDIS_HOST_PORT", "REDIS_INGEST_PW",
             "SIM_HOST_PORT"], "1"),
        "spec": lambda: "spec", "digests": lambda: "digests", "load": lambda: "0.1",
        "clock": lambda db: T, "_used_memory": lambda r: 1, "set_sim_rate": set_rate,
        "_sample_lag": lag, "drive_steps": steps, "_cpu_sampler": cpu_sampler,
        "query_times": lambda env, end: [(T, 1.0)] * 3, "samples": samples,
    }  # fmt: skip
    for name, fake in fakes.items():
        monkeypatch.setattr(run_p, name, fake)
    monkeypatch.setattr(run_p, "REPO", tmp_path)
    monkeypatch.setattr(run_p, "DURATION_S", N)
    monkeypatch.setattr(redis, "Redis", lambda **kw: object())
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: _Db())
    run_p.main()
    return rates


def _summary(tmp_path: Path) -> str:
    return next((tmp_path / "docs" / "perf").glob("run-p-*")).joinpath("summary.md").read_text()


def test_clean_run_restores_10hz_and_writes_every_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _run(monkeypatch, tmp_path) == [100, 10]
    out = next((tmp_path / "docs" / "perf").glob("run-p-*"))
    assert {p.name for p in out.iterdir()} == {
        "lag.csv",
        "ingest.csv",
        "alarm.csv",
        "query.csv",
        "totals.json",
        "summary.md",
    }
    assert "**PASS**" in (out / "summary.md").read_text()


def test_failed_ing10_exits_nonzero_but_writes_the_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="ING-10"):
        _run(monkeypatch, tmp_path, time_lag=1.5)
    assert "**FAIL**" in _summary(tmp_path)


def test_restore_failure_still_writes_artifacts_marked_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, restore_fails=True)
    s = _summary(tmp_path)
    assert "INCOMPLETE RUN" in s and "compose up failed" in s


def test_background_step_driver_failure_is_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, steps_fail=True)
    assert "sim POST 500" in _summary(tmp_path)


def test_missing_cpu_samples_make_the_run_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, cpu=False)
    assert "ingest-1" in _summary(tmp_path)


def test_incomplete_names_every_short_series() -> None:
    lag = {v: [(T, 0, 0, 0.1)] * 300 for v in run_n.VEHICLES}
    cpu = {w: [1.0] * 300 for w in run_p.WORKERS}
    assert run_p.incomplete(lag, cpu, 10, 300) == []
    short = run_p.incomplete({**lag, "NYX-TA1": []}, {**cpu, "ingest-2": []}, 0, 300)
    assert len(short) == 3
