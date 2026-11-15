"""EARLIER is the command lifecycle QUEUED → SENT → RECEIVED → VERIFIED | FAILED (D-CMD-01/11)."""

from typing import get_args

from gs.commanding.decide import EARLIER
from gs.common.interfaces.sse import CmdState

ORDER = ("QUEUED", "SENT", "RECEIVED", "VERIFIED")


def test_earlier_table_is_the_lifecycle() -> None:
    assert set(EARLIER) == set(get_args(CmdState)) - {"QUEUED"}  # nothing moves back to QUEUED
    for state, earlier in EARLIER.items():
        assert state not in earlier
        assert "VERIFIED" not in earlier and "FAILED" not in earlier  # both terminal
        if state in ORDER:  # no state moves backwards
            assert earlier == ORDER[: ORDER.index(state)]
    assert EARLIER["FAILED"] == ("QUEUED", "SENT", "RECEIVED")
