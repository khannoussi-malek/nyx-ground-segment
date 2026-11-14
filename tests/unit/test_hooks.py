import asyncio
import os
import signal
from pathlib import Path
from typing import NoReturn

import pytest

from gs.common.config import ConfigError
from gs.common.hooks import POINTS, Hooks

VARS = ("GS_TEST_HOOKS", "CRASH_AT", "STOP_AT", "HANG_AT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in VARS:
        monkeypatch.delenv(k, raising=False)


class Exited(Exception):
    pass


def recorder(calls: list[int]) -> object:
    def exit_(code: int) -> NoReturn:
        calls.append(code)
        raise Exited

    return exit_


def test_off_without_flag(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CRASH_AT", "before_commit")
    monkeypatch.setenv("HANG_AT", "in_loop")
    h = Hooks.from_env()
    assert (h.crash_at, h.stop_at, h.hang_at) == (None, None, None)
    assert caplog.text.count("GS_TEST_HOOKS") == 1  # one WARNING for the ignored vars


def test_on_reads_every_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {
        "GS_TEST_HOOKS": "1",
        "CRASH_AT": "frame_seq:NYX-TA1:7",
        "STOP_AT": "after_fence_before_read",
        "HANG_AT": "in_loop",
    }.items():
        monkeypatch.setenv(k, v)
    h = Hooks.from_env()
    assert (h.crash_at, h.stop_at, h.hang_at) == (
        "frame_seq:NYX-TA1:7",
        "after_fence_before_read",
        "in_loop",
    )


@pytest.mark.parametrize("ok", [*POINTS, "frame_seq:12", "frame_seq:NYX-TA1:0"])
def test_good_points(monkeypatch: pytest.MonkeyPatch, ok: str) -> None:
    monkeypatch.setenv("GS_TEST_HOOKS", "1")
    monkeypatch.setenv("CRASH_AT", ok)
    assert Hooks.from_env().crash_at == ok


@pytest.mark.parametrize(
    ("var", "v"),
    [
        ("CRASH_AT", "nope"),
        ("CRASH_AT", "frame_seq:x"),
        ("CRASH_AT", "frame_seq:NYX-TA1:"),
        ("CRASH_AT", "frame_seq:nyx-ta1:3"),
        ("STOP_AT", "in_loop"),
        ("HANG_AT", "before_commit"),
    ],
)
def test_bad_point_fails_fast(monkeypatch: pytest.MonkeyPatch, var: str, v: str) -> None:
    monkeypatch.setenv("GS_TEST_HOOKS", "1")
    monkeypatch.setenv(var, v)
    with pytest.raises(ConfigError, match=var):
        Hooks.from_env()


def test_named_point_fires_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    calls: list[int] = []
    h = Hooks(crash_at="after_xack", marker_dir=tmp_path, exit=recorder(calls))  # type: ignore[arg-type]
    with pytest.raises(Exited):
        h.fire("after_xack")
    h.fire("after_xack")  # marker exists: a restarted container does not crash again
    h.fire("before_commit")
    assert calls == [99] and (tmp_path / "gs-hook-after_xack").exists()
    assert "test hook CRASH_AT=after_xack" in caplog.text


def test_frame_seq_every_delivery_and_vehicle_filter(tmp_path: Path) -> None:
    calls: list[int] = []
    h = Hooks(crash_at="frame_seq:NYX-TA1:5", marker_dir=tmp_path, exit=recorder(calls))  # type: ignore[arg-type]
    for _ in range(2):
        with pytest.raises(Exited):
            h.frame("NYX-TA1", 5)
    h.frame("NYX-TA2", 5)
    h.frame("NYX-TA1", 6)
    h.fire("frame_seq:NYX-TA1:5")  # not a named point
    assert calls == [99, 99] and list(tmp_path.iterdir()) == []


def test_frame_seq_without_vehicle_matches_any(tmp_path: Path) -> None:
    calls: list[int] = []
    h = Hooks(crash_at="frame_seq:3", marker_dir=tmp_path, exit=recorder(calls))  # type: ignore[arg-type]
    for v in ("NYX-TA1", "NYX-TA2"):
        with pytest.raises(Exited):
            h.frame(v, 3)
    assert calls == [99, 99]


def test_stop_sends_sigstop(tmp_path: Path) -> None:
    sent: list[tuple[int, int]] = []
    h = Hooks(
        stop_at="after_fence_before_read",
        marker_dir=tmp_path,
        kill=lambda p, s: sent.append((p, s)),
    )
    h.fire("after_fence_before_read")
    h.fire("after_fence_before_read")
    assert sent == [(os.getpid(), signal.SIGSTOP)]


async def test_hang_waits_forever_only_at_its_point() -> None:
    h = Hooks(hang_at="in_loop")
    await asyncio.wait_for(Hooks().hang("in_loop"), 1)  # off: returns at once
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(h.hang("in_loop"), 0.2)


def test_crash_recorder_that_returns_never_reaches_sigstop(tmp_path: Path) -> None:
    calls: list[int] = []
    sent: list[tuple[int, int]] = []
    h = Hooks(
        crash_at="after_xack",
        marker_dir=tmp_path,
        exit=calls.append,  # type: ignore[arg-type]
        kill=lambda p, s: sent.append((p, s)),
    )
    h.fire("after_xack")
    h.frame("NYX-TA1", 1)
    assert calls == [99] and sent == []


def test_frame_hot_path_with_hooks_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from gs.common import hooks as hooks_mod

    def boom(*_: object) -> bool:
        raise AssertionError("regex consulted with hooks off")

    monkeypatch.setattr(hooks_mod, "_frame_match", boom)
    Hooks().frame("NYX-TA1", 5)
