from pathlib import Path

from perf.run_n import (
    DURATION_S,
    STEP_EVERY_S,
    STEP_S,
    STEP_VALUE,
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
