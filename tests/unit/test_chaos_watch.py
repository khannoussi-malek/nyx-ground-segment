"""The I4 watcher's result reading and its secret-safety (D-EXT-15)."""

import json
import re
from pathlib import Path

import pytest

import chaos.watch as watch
from tests.support.env import REPO


def write(tmp: Path, **r: object) -> Path:
    f = tmp / "watch.json"
    f.write_text(json.dumps({"bad": [], "rows": 100, "samples": 200, "blind_ms": 0, **r}))
    return f


def test_a_clean_run_passes(tmp_path: Path) -> None:
    assert watch.i4_result(write(tmp_path), 0).startswith("PASS")


def test_a_string_bad_is_malformed_not_split_into_characters(tmp_path: Path) -> None:
    assert "malformed" in watch.i4_result(write(tmp_path, bad="OK at 9 ms"), 0)


def test_a_missing_blind_field_is_malformed(tmp_path: Path) -> None:
    f = tmp_path / "watch.json"
    f.write_text(json.dumps({"bad": [], "rows": 1, "samples": 1}))
    assert "malformed" in watch.i4_result(f, 0)


def test_a_long_stretch_without_ok_rows_fails(tmp_path: Path) -> None:
    ok = watch.i4_result(write(tmp_path, blind_ms=watch.MAX_BLIND_MS), 0)
    assert ok.startswith("PASS") and "longest stretch without an OK row" in ok
    assert watch.i4_result(write(tmp_path, blind_ms=watch.MAX_BLIND_MS + 1), 0).startswith("FAIL")


def test_a_failed_log_open_leaves_i4_not_run_and_the_soak_going(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Path.open

    def deny(self: Path, *a: object, **k: object) -> object:
        if self.name == "watch.log":
            raise PermissionError("no")
        return real(self, *a, **k)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", deny)
    w = watch.Watcher(tmp_path)
    assert w.proc is None
    assert w.finish().startswith("NOT RUN")


def test_the_watcher_never_records_the_login() -> None:
    cfg = (REPO / "e2e" / "chaos.config.ts").read_text()
    assert re.search(r'trace:\s*"off"', cfg)  # a trace of the login holds the password
    spec = (REPO / "chaos" / "watch_console.spec.ts").read_text()
    assert spec.index('login(page, "fc1")') < spec.index("tracing.start")
    assert "snapshots: false" in spec
    assert "docs/chaos/*/trace/" in (REPO / ".gitignore").read_text()


def test_the_spec_rejects_a_bad_override_and_a_missing_stale_threshold() -> None:
    spec = (REPO / "chaos" / "watch_console.spec.ts").read_text()
    assert "Number.isFinite" in spec and "no data-stale-ms" in spec
