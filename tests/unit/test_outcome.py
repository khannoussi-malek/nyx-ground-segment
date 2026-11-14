from datetime import UTC, datetime

import pytest

from gs.commanding.outcome import outcome
from gs.common.interfaces.sse import FailureReason, Outcome

T = datetime(2026, 11, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("reason", "late_v", "late_ack", "want"),
    [
        (None, None, None, None),
        ("SEND_ERROR", None, None, "NOT_SENT"),
        ("REJECTED", T, True, "NOT_EXECUTED"),
        ("NOT_RECEIVED", T, None, "EXECUTED_LATE"),
        ("NOT_RECEIVED", T, False, "EXECUTED_LATE"),
        ("NOT_RECEIVED", None, False, "NOT_EXECUTED"),
        ("NOT_RECEIVED", None, True, "RECEIVED_LATE"),
        ("NOT_VERIFIED", None, None, "UNKNOWN"),
        ("ACK_INVALID", T, None, "EXECUTED_LATE"),
        ("ACK_INVALID", None, None, "UNKNOWN"),
    ],
)
def test_outcome_table(
    reason: FailureReason | None,
    late_v: datetime | None,
    late_ack: bool | None,
    want: Outcome | None,
) -> None:
    assert outcome(reason, late_v, late_ack) == want
