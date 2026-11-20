"""The handover list carries the console's safety advice for an UNKNOWN command outcome."""

from datetime import UTC, datetime
from uuid import uuid4

from gs.api.summary import command_text
from gs.common.interfaces.sse import CommandView


def test_unknown_outcome_text_has_the_console_safety_advice() -> None:
    t = datetime(2026, 11, 20, tzinfo=UTC)
    c = CommandView(
        cmd_id=uuid4(), vehicle="NYX-TA1", name="vent_cabin", args={}, hazardous=True,
        reason="r", issued_by="fc1", state="FAILED", failure_reason="NOT_VERIFIED", queued_at=t,
        sent_at=t, received_at=None, verified_at=None, failed_at=t, late_verified_at=None,
        late_ack_at=None, late_ack_accepted=None, outcome="UNKNOWN",
    )  # fmt: skip
    text = command_text(c)
    assert "check mode, last_cmd_id and the affected parameters" in text
    assert "never blind-resend a hazardous command; tell the test conductor" in text
