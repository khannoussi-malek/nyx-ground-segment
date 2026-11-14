"""Ordered outage buffer and the sender that drains it (D-SIM-05)."""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

import redis.exceptions

BUFFER_MAX = 6000
BACKOFF_FIRST_S, BACKOFF_CAP_S, IDLE_S = 0.1, 1.0, 0.01
log = logging.getLogger(__name__)


class FrameBuffer:
    def __init__(self) -> None:
        self._q: deque[tuple[str, str]] = deque()
        self.overflow = 0

    def __len__(self) -> int:
        return len(self._q)

    def append(self, stream: str, data: str) -> bool:
        """Full: drop the NEW frame so downstream sees a GAP, not reordering."""
        if len(self._q) >= BUFFER_MAX:
            self.overflow += 1
            log.warning("buffer full, dropped frame for %s (overflow=%d)", stream, self.overflow)
            return False
        self._q.append((stream, data))
        return True

    def head(self) -> tuple[str, str] | None:
        return self._q[0] if self._q else None

    def pop(self) -> None:
        self._q.popleft()


async def send_loop(
    buf: FrameBuffer,
    xadd: Callable[[str, str], Awaitable[object]],
    stop: asyncio.Event,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> None:
    """Send the head until it succeeds, then pop: order is kept across outages."""
    delay = BACKOFF_FIRST_S
    while not stop.is_set():
        head = buf.head()
        if head is None:
            await sleep(
                IDLE_S
            )  # ponytail: polling; an Event would save wakeups, not needed at 20 fps
            continue
        try:
            await xadd(*head)
        except redis.exceptions.ConnectionError, redis.exceptions.TimeoutError:
            await sleep(delay)
            delay = min(delay * 2, BACKOFF_CAP_S)
            continue
        buf.pop()
        delay = BACKOFF_FIRST_S
