from gs.simulator.buffer import BUFFER_MAX, FrameBuffer
from gs.simulator.main import DEGRADED_BACKLOG, sim_status


def _buf(n: int) -> FrameBuffer:
    b = FrameBuffer()
    for i in range(n):
        b.append("tm:NYX-TA1", str(i))
    return b


def test_ok_below_backlog_threshold() -> None:
    assert sim_status(_buf(0)) == ("ok", {"buffered": 0, "overflow": 0})
    assert sim_status(_buf(DEGRADED_BACKLOG - 1))[0] == "ok"


def test_degraded_at_backlog_threshold() -> None:
    state, checks = sim_status(_buf(DEGRADED_BACKLOG))
    assert state == "degraded" and checks["buffered"] == DEGRADED_BACKLOG


def test_degraded_after_overflow_even_when_drained() -> None:
    b = _buf(BUFFER_MAX + 1)
    assert b.overflow == 1
    while len(b):
        b.pop()
    assert sim_status(b) == ("degraded", {"buffered": 0, "overflow": 1})
