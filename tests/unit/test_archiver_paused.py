"""archiver_stopped works where CI has no archiver container (G4 final review I2)."""

import subprocess
from typing import Any

import pytest

from tests.support import archive


def _fake(ps_out: str, calls: list[tuple[str, ...]]) -> Any:
    def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, ps_out, "")

    return compose


def test_no_container_means_no_stop_start_or_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(archive, "compose", _fake("\n", calls))
    monkeypatch.setattr(archive, "inspect_health", lambda _s: pytest.fail("inspected"))
    with archive.archiver_stopped():
        pass
    assert calls == [("ps", "-aq", "archiver")]


def test_container_is_stopped_then_started_and_awaited(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(archive, "compose", _fake("abc123\n", calls))
    monkeypatch.setattr(archive, "inspect_health", lambda _s: "healthy")
    with archive.archiver_stopped():
        assert calls[-1] == ("stop", "archiver")
    assert calls[-1] == ("start", "archiver")
