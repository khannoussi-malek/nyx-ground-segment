from gs.simulator.buffer import FrameBuffer
from tests.support.sim import drive_send_loop


def test_overflow_counts_and_keeps_order() -> None:
    b = FrameBuffer()
    assert all(b.append("tm:NYX-TA1", str(i)) for i in range(6000))
    assert b.append("tm:NYX-TA1", "x") is False and b.overflow == 1 and len(b) == 6000
    assert b.head() == ("tm:NYX-TA1", "0")  # the new frame was dropped, not the oldest


async def test_send_loop_retries_in_order_with_capped_backoff() -> None:
    sent, sleeps = await drive_send_loop(frames=["a", "b", "c"], failures=3)
    assert sent == ["a", "b", "c"] and sleeps == [0.1, 0.2, 0.4]


async def test_backoff_caps_at_1s() -> None:
    sent, sleeps = await drive_send_loop(frames=["a"], failures=10)
    assert sent == ["a"] and max(sleeps) == 1.0 and sleeps[-1] == 1.0
