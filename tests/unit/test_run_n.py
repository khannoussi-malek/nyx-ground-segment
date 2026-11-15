import subprocess
from pathlib import Path

import psycopg
import pytest

from perf.run_n import (
    DURATION_S,
    STEP_EVERY_S,
    STEP_S,
    STEP_VALUE,
    _guarded,
    _summary,
    step_schedule,
    verdict,
)


def test_schedule_gives_120_steps_inside_the_run() -> None:
    s = step_schedule(0.0)
    assert len(s) == 120 and s[-1] + STEP_S <= DURATION_S
    assert all(b - a == STEP_EVERY_S for a, b in zip(s, s[1:], strict=False))


def test_schedule_is_offset_by_t0() -> None:
    assert step_schedule(1000.0)[:2] == [1000.0, 1000.0 + STEP_EVERY_S]


def test_step_value_is_over_the_red_limit() -> None:
    assert STEP_VALUE > 30.0  # cabin_temp_c red_high in mission/limits.yaml


def test_verdict_pass_fail_and_empty() -> None:
    ok = verdict("ingest", [10.0] * 99 + [250.0], 200.0, excluded=3)
    assert "n = 100" in ok and "p95 = 10.0 ms" in ok and "PASS" in ok and "excluded: 3" in ok
    bad = verdict("ingest", [300.0] * 100, 200.0, excluded=0)
    assert "FAIL" in bad and "PASS" not in bad
    assert "NO SAMPLES" in verdict("ui", [], 1000.0, excluded=0)


def test_verdict_fails_when_the_series_is_not_the_expected_size() -> None:
    full = verdict("alarm", [100.0] * 120, 300.0, expected_n=120)
    assert "PASS" in full and "INCOMPLETE" not in full
    short = verdict("alarm", [100.0] * 119, 300.0, expected_n=120)
    assert "FAIL" in short and "PASS" not in short and "expected 120" in short


def test_warmup_is_only_mentioned_where_it_applies() -> None:
    assert "excluded" not in verdict("alarm", [1.0], 300.0)
    assert "first 10 s excluded: 7" in verdict("ingest", [1.0], 200.0, excluded=7)


def test_summary_text_scopes_warmup_and_names_gate_4_items() -> None:
    ctx = {"spec": "x", "digests": "d", "load_before": "0", "load_after": "1"}
    text = _summary(Path("run-n-2026-11-15"), ctx, ["- r"], 120, None)
    assert "ingest series only" in text and "D-PERF-10" in text and "D-PERF-07" in text
    assert "Gate 4" in text and "INCOMPLETE" not in text
    assert "INCOMPLETE RUN: boom" in _summary(Path("r"), ctx, [], 3, "boom")


class FakePopen:
    def __init__(self, running: bool = True, hangs: bool = False) -> None:
        self.running, self.hangs, self.calls = running, hangs, []  # type: ignore[var-annotated]

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
        self.running = False

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"wait({timeout})")
        if self.hangs and timeout is not None:
            raise subprocess.TimeoutExpired("npx", timeout)
        self.running = False
        return 0


def _guard(ui: FakePopen, body):  # type: ignore[no-untyped-def]
    return _guarded(ui, body)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "exc", [OSError("sim down"), AssertionError("status"), psycopg.OperationalError("db")]
)
def test_a_failure_after_the_spawn_stops_the_browser_and_reports(exc: Exception) -> None:
    def body() -> None:
        raise exc

    ui = FakePopen()
    result, error = _guard(ui, body)
    assert result is None and error is not None and type(exc).__name__ in error
    assert ui.calls[:2] == ["terminate", "wait(30)"]


def test_success_still_cleans_up_a_browser_that_is_running() -> None:
    ui = FakePopen()
    result, error = _guard(ui, lambda: 42)
    assert (result, error) == (42, None) and "terminate" in ui.calls
    done = FakePopen(running=False)
    _guard(done, lambda: 1)
    assert done.calls == []  # an exited browser is left alone


def test_a_browser_that_ignores_terminate_is_killed() -> None:
    ui = FakePopen(hangs=True)
    _guard(ui, lambda: None)
    assert ui.calls == ["terminate", "wait(30)", "kill", "wait(None)"]
