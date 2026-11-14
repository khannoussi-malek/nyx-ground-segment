"""Test hooks (D-ENG-16): CRASH_AT / STOP_AT / HANG_AT, active only with GS_TEST_HOOKS=1."""

import asyncio
import logging
import os
import re
import signal
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from gs.common.config import ConfigError
from gs.common.interfaces.base import VEHICLE_ID_PATTERN

log = logging.getLogger(__name__)

POINTS = (
    "before_commit",
    "after_commit_before_publish",
    "after_publish_before_ack",
    "after_xack",
    "after_fence_before_read",
)
HANG_POINTS = ("in_loop",)
CRASH_CODE = 99
_FRAME = re.compile(rf"frame_seq:(?:({VEHICLE_ID_PATTERN.strip('^$')}):)?(\d+)")


def _point(name: str, raw: str | None) -> str | None:
    if raw and raw not in POINTS and not _FRAME.fullmatch(raw):
        raise ConfigError(
            f"{name}={raw!r} is not one of {POINTS}, frame_seq:<n> or frame_seq:<vehicle>:<n>"
        )
    return raw or None


def _frame_match(spec: str | None, vehicle: str, seq: int) -> bool:
    m = _FRAME.fullmatch(spec or "")
    return bool(m) and int(m[2]) == seq and m[1] in (None, vehicle)  # type: ignore[index]


@dataclass(frozen=True)
class Hooks:
    crash_at: str | None = None
    stop_at: str | None = None
    hang_at: str | None = None
    marker_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    exit: Callable[[int], NoReturn] = os._exit
    kill: Callable[[int, int], None] = os.kill

    @classmethod
    def from_env(cls) -> Hooks:
        names = ("CRASH_AT", "STOP_AT", "HANG_AT")
        if os.environ.get("GS_TEST_HOOKS") != "1":
            if any(os.environ.get(n) for n in names):
                log.warning("test hook variables set but GS_TEST_HOOKS != 1: ignored")
            return cls()
        hang = os.environ.get("HANG_AT") or None
        if hang is not None and hang not in HANG_POINTS:
            raise ConfigError(f"HANG_AT={hang!r} is not one of {HANG_POINTS}")
        return cls(
            crash_at=_point("CRASH_AT", os.environ.get("CRASH_AT")),
            stop_at=_point("STOP_AT", os.environ.get("STOP_AT")),
            hang_at=hang,
        )

    def fire(self, point: str) -> None:
        """A named point fires once per container: the marker survives `restart: unless-stopped`,
        which would otherwise re-run the hook until the batch reaches the poison count (G2)."""
        if point not in POINTS or point not in (self.crash_at, self.stop_at):
            return
        marker = self.marker_dir / f"gs-hook-{point}"
        try:
            marker.touch(exist_ok=False)
        except FileExistsError:
            return
        self._act(point, crash=point == self.crash_at)

    def frame(self, vehicle: str, seq: int) -> None:
        """frame_seq hooks fire on every delivery of the matching frame (no marker)."""
        if _frame_match(self.crash_at, vehicle, seq):
            self._act(f"frame_seq:{vehicle}:{seq}", crash=True)
        elif _frame_match(self.stop_at, vehicle, seq):
            self._act(f"frame_seq:{vehicle}:{seq}", crash=False)

    async def hang(self, point: str) -> None:
        if point == self.hang_at:
            log.warning("test hook HANG_AT=%s: hanging", point)
            await asyncio.Event().wait()

    def _act(self, point: str, crash: bool) -> None:
        if crash:
            log.warning("test hook CRASH_AT=%s", point)
            self.exit(CRASH_CODE)
        log.warning("test hook STOP_AT=%s", point)
        self.kill(os.getpid(), signal.SIGSTOP)
