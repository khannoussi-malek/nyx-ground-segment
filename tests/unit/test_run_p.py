import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
import redis

from perf import run_n, run_p
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
    assert totals({}, None, 5, [], 0, 2, 300)["redis_used_memory"]["per_vehicle_hour"] is None
    assert t["ingest_ms"] == {"p50": 50.0, "p95": 95.0} and t["frames_stored"] == 60_000


# --- lifecycle: main() with every external dependency faked ---

N = 5  # DURATION_S patched to 5 s: the run must hold >= 80 % of 5 samples per series


class _Db:
    def __enter__(self) -> _Db:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rates: list[int],
    *,
    time_lag: float = 0.1,
    restore_fails: bool = False,
    steps_fail: bool = False,
    cpu: bool = True,
    lag_fails: bool = False,
) -> None:

    def set_rate(rate: int) -> None:
        rates.append(rate)
        if restore_fails and rate == 10:
            raise RuntimeError("compose up failed")

    def lag(r: Any, db: Any, rows: dict[str, list[run_p.LagRow]], until: float) -> None:
        if lag_fails:
            raise ConnectionError("redis gone")
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


def _summary(tmp_path: Path) -> str:
    return next((tmp_path / "docs" / "perf").glob("run-p-*")).joinpath("summary.md").read_text()


def test_clean_run_restores_10hz_and_writes_every_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rates: list[int] = []
    _run(monkeypatch, tmp_path, rates)
    assert rates == [100, 10]
    out = next((tmp_path / "docs" / "perf").glob("run-p-*"))
    assert {p.name for p in out.iterdir()} == {
        "lag.csv",
        "ingest.csv",
        "alarm.csv",
        "query.csv",
        "totals.json",
        "summary.md",
    }
    assert (
        "**ING-10 lag**: max time lag 0.100 s against 1 s: **PASS**"
        in (out / "summary.md").read_text()
    )


def test_failed_ing10_exits_nonzero_but_writes_the_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rates: list[int] = []
    with pytest.raises(SystemExit, match="ING-10"):
        _run(monkeypatch, tmp_path, rates, time_lag=1.5)
    assert rates == [100, 10] and "**FAIL**" in _summary(tmp_path)


def test_restore_failure_still_writes_artifacts_marked_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rates: list[int] = []
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, rates, restore_fails=True)
    s = _summary(tmp_path)
    assert "INCOMPLETE RUN" in s and "compose up failed" in s


def test_background_step_driver_failure_is_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rates: list[int] = []
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, rates, steps_fail=True)
    assert rates == [100, 10] and "sim POST 500" in _summary(tmp_path)


def test_sampling_loop_failure_restores_10hz_and_writes_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rates: list[int] = []
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, rates, lag_fails=True)
    assert rates == [100, 10] and "redis gone" in _summary(tmp_path)
    assert (next((tmp_path / "docs" / "perf").glob("run-p-*")) / "totals.json").exists()


def test_missing_cpu_samples_make_the_run_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rates: list[int] = []
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        _run(monkeypatch, tmp_path, rates, cpu=False)
    assert "ingest-1" in _summary(tmp_path)


def test_incomplete_names_every_short_series() -> None:
    lag = {v: [(T, 0, 0, 0.1)] * 300 for v in run_n.VEHICLES}
    cpu = {w: [1.0] * 300 for w in run_p.WORKERS}
    assert run_p.incomplete(lag, cpu, 10, 300) == []
    short = run_p.incomplete({**lag, "NYX-TA1": []}, {**cpu, "ingest-2": []}, 0, 300)
    assert len(short) == 3


# --- the real pieces: parsers, the 1 Hz schedules, the compose call ------------------------
IDS = {"ingest-1": "aaaaaaaaaaaa1111", "ingest-2": "bbbbbbbbbbbb2222"}
STATS = "aaaaaaaaaaaa 12.50%\nbbbbbbbbbbbb --\n\ngarbage\n"


def test_parse_cpu_skips_bad_lines_and_keeps_good_ones() -> None:
    assert run_p.parse_cpu(STATS, IDS) == {"ingest-1": 12.5}
    assert run_p.parse_cpu("aaaaaaaaaaaa 1.0%\nbbbbbbbbbbbb 2.0%", IDS) == {
        "ingest-1": 1.0,
        "ingest-2": 2.0,
    }


def _fake_run(stats: Callable[[], str]) -> Callable[..., Any]:
    def run(cmd: list[str], **kw: Any) -> Any:
        if cmd[:3] == ["docker", "compose", "ps"]:
            return SimpleNamespace(stdout=IDS[cmd[-1]] + "\n")
        if cmd[:2] == ["docker", "stats"]:
            return SimpleNamespace(stdout=stats())
        raise AssertionError(cmd)

    return run


def test_cpu_sampler_ticks_on_a_grid_and_survives_a_bad_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = []

    def stats() -> str:
        calls.append(time.monotonic())
        n = len(calls)
        time.sleep(0.2)  # a real docker stats takes 1-2 s: 4x the grid step here
        if n == 2:
            raise OSError("docker hiccup")
        return STATS if n == 3 else "aaaaaaaaaaaa 1.0%\nbbbbbbbbbbbb 2.0%"

    monkeypatch.setattr(run_p, "SAMPLE_EVERY_S", 0.05)
    monkeypatch.setattr(subprocess, "run", _fake_run(stats))
    stop = threading.Event()
    out: dict[str, list[float]] = {w: [] for w in run_p.WORKERS}
    threading.Timer(0.5, stop.set).start()
    run_p._cpu_sampler(stop, out)
    # 0.5 s at 0.05 s ticks; synchronous 0.2 s calls would manage 3, the pool reaches ~10
    assert 8 <= len(calls) <= 12
    assert len(out["ingest-1"]) == len(calls) - 1  # only the raising call is lost
    assert len(out["ingest-2"]) == len(calls) - 2  # and the "--" line
    assert "docker hiccup" in caplog.text


def test_sample_lag_is_one_row_per_vehicle_per_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_p, "SAMPLE_EVERY_S", 0.02)
    streams = []

    class R:
        def xinfo_groups(self, name: str) -> list[dict[str, Any]]:
            streams.append(name)
            return [{"name": "ingest", "lag": 4, "pending": 2}]

    class Db:
        def execute(self, q: str, args: tuple[str]) -> Any:
            assert q == run_p.TIME_LAG_SQL
            return SimpleNamespace(fetchone=lambda: (0.25,))

    rows: dict[str, list[run_p.LagRow]] = {v: [] for v in run_n.VEHICLES}
    run_p._sample_lag(R(), Db(), rows, time.monotonic() + 0.2)  # type: ignore[arg-type]
    n = len(rows["NYX-TA1"])
    assert 8 <= n <= 11 and len(rows["NYX-TA2"]) == n
    assert set(streams) == {"tm:NYX-TA1", "tm:NYX-TA2"}
    assert rows["NYX-TA1"][0][1:] == (4, 2, 0.25)


def test_time_lag_without_a_row_is_infinite_so_ing10_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class R:
        def xinfo_groups(self, name: str) -> list[dict[str, Any]]:
            return []

    class Db:
        def execute(self, q: str, args: tuple[str]) -> Any:
            return SimpleNamespace(fetchone=lambda: (None,))

    monkeypatch.setattr(run_p, "SAMPLE_EVERY_S", 0.01)
    rows: dict[str, list[run_p.LagRow]] = {v: [] for v in run_n.VEHICLES}
    run_p._sample_lag(R(), Db(), rows, time.monotonic() + 0.03)  # type: ignore[arg-type]
    assert rows["NYX-TA1"][0][3] == float("inf")


def test_set_sim_rate_puts_the_rate_in_the_compose_env(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def run(cmd: list[str], **kw: Any) -> Any:
        seen.append((cmd, kw))
        return SimpleNamespace()

    monkeypatch.setattr(subprocess, "run", run)
    run_p.set_sim_rate(100)
    cmd, kw = seen[0]
    assert cmd == ["docker", "compose", "up", "-d", "--force-recreate", "--wait", "simulator"]
    assert kw["env"]["SIM_RATE_HZ"] == "100" and kw["check"] is True and kw["cwd"] == run_n.REPO
