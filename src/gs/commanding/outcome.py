"""D-CMD-02: what we can honestly say about a FAILED command."""

from datetime import datetime

from gs.common.interfaces.sse import FailureReason, Outcome


def outcome(
    failure_reason: FailureReason | None,
    late_verified_at: datetime | None,
    late_ack_accepted: bool | None,
) -> Outcome | None:
    if failure_reason is None:
        return None
    if failure_reason == "SEND_ERROR":
        return "NOT_SENT"
    if failure_reason == "REJECTED":
        return "NOT_EXECUTED"
    if late_verified_at is not None:
        return "EXECUTED_LATE"
    if late_ack_accepted is None:
        return "UNKNOWN"
    return "RECEIVED_LATE" if late_ack_accepted else "NOT_EXECUTED"
