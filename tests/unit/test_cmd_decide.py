"""decide(): every D-CMD branch as a table, from stored evidence only (D-CMD-01..05, R-j)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from gs.commanding.decide import EARLIER, AckSeen, Change, CmdRow, decide
from gs.common.interfaces.sse import CmdState

S = datetime(2026, 11, 15, 10, tzinfo=UTC)  # sent_at


def s(x: float) -> datetime:
    return S + timedelta(seconds=x)


BASE = CmdRow(
    UUID(int=1), "isolate_valve", "SENT", None, s(-0.1), S, None, None, None, None, 5.0, 15.0
)


def row(state: CmdState = "SENT", **kw: Any) -> CmdRow:
    return replace(BASE, state=state, **kw)


def failed(reason: str, at: float, **kw: Any) -> CmdRow:
    return row("FAILED", failure_reason=reason, failed_at=s(at), **kw)


def ack(t: float, accepted: bool = True) -> AckSeen:
    return AckSeen(accepted, "OK" if accepted else "INVALID_ARGS", s(t))


def bad(t: float) -> AckSeen:
    return AckSeen(False, "X", s(t), invalid=True)


OK = ack(0.3)
RCV = row("RECEIVED", received_at=s(0.1))
Want = tuple[str, str | None, datetime | None, bool | None] | None
F, NR, NV, AI = "FAILED", "NOT_RECEIVED", "NOT_VERIFIED", "ACK_INVALID"
CASES: list[tuple[CmdRow, AckSeen | None, datetime | None, datetime, bool, Want]] = [
    (row(), OK, s(0.4), s(0.5), True, ("VERIFIED", None, None, None)),
    (row(), OK, None, s(0.5), True, ("RECEIVED", None, None, None)),
    (row(), ack(0.3, False), None, s(0.5), True, (F, "REJECTED", None, None)),
    (row(), None, s(2.0), s(2.1), True, ("VERIFIED", None, None, None)),
    (row(), None, None, s(5.0), True, (F, NR, None, None)),
    (row(), None, None, s(9.0), False, None),  # not caught up: no timeout
    (row(), None, None, s(4.9), True, None),  # inside the window
    (row(), ack(5.0), None, s(5.0), True, ("RECEIVED", None, None, None)),  # ack at R wins
    (row(), None, s(5.0), s(5.0), True, ("VERIFIED", None, None, None)),  # echo at R wins
    (RCV, None, s(15.0), s(15.0), True, ("VERIFIED", None, None, None)),  # echo at V wins
    (row(), ack(6.0), s(6.1), s(6.2), True, (F, NR, s(6.1), True)),  # pause 6: both late
    (row(), ack(6.0, False), None, s(6.2), True, (F, NR, None, False)),  # → NOT_EXECUTED
    (row(), ack(6.0), None, s(6.2), False, None),  # late ack, not caught up yet
    (RCV, None, None, s(15.0), True, (F, NV, None, None)),
    (RCV, None, None, s(20.0), False, None),
    (RCV, None, s(14.9), s(20.0), True, ("VERIFIED", None, None, None)),
    (RCV, None, s(15.5), s(15.6), True, (F, NV, s(15.5), None)),
    (failed(NV, 15), None, s(16.1), s(16.2), True, (F, NV, s(16.1), None)),  # hold_exec 16
    (row(), bad(0.2), None, s(0.3), True, (F, AI, None, None)),
    (RCV, bad(0.2), None, s(0.3), True, (F, AI, None, None)),  # P-pf14: RECEIVED too
    (row(), bad(0.2), s(0.4), s(0.5), True, (F, AI, s(0.4), None)),  # echo rides along late
    (row("QUEUED", sent_at=None), None, None, s(4.95), True, (F, NR, None, None)),  # queued_at
    (failed(NR, 5), None, s(700), s(700), True, None),  # outside 600 s
    (failed(NR, 5), ack(6), None, s(7), True, (F, NR, None, True)),
    (failed(AI, 1), ack(2), None, s(3), True, (F, AI, None, True)),
    (failed(NV, 15), ack(16), None, s(17), True, None),  # an ack after NOT_VERIFIED: nothing new
    (failed(NR, 5, late_verified_at=s(6)), None, s(6), s(7), True, None),  # written once
    (failed(NR, 5, late_ack_at=s(6)), ack(6), None, s(7), True, None),
    (failed(AI, 1), bad(2), None, s(3), True, None),  # an invalid ack is never late evidence
    (failed("REJECTED", 0.3), None, s(1), s(2), True, None),
    (failed("SEND_ERROR", 0), OK, s(1), s(2), True, None),
    (row("VERIFIED"), OK, s(0.4), s(30), True, None),
]


def outcome_of(r: CmdRow, c: Change | None) -> Want:
    if c is None:
        return None
    return (
        c.state or r.state,
        c.failure_reason or r.failure_reason,
        c.late_verified_at or r.late_verified_at,
        c.late_ack_accepted,
    )


@pytest.mark.parametrize("r,ack,echo,now,cu,want", CASES)
def test_decide_table(
    r: CmdRow, ack: AckSeen | None, echo: datetime | None, now: datetime, cu: bool, want: Want
) -> None:
    assert outcome_of(r, decide(r, ack, echo, now, cu)) == want


def test_backlog_evidence_wins_over_timeout() -> None:  # Review Focus 1
    c = decide(row(), OK, s(0.4), now=s(8.0), caught_up=True)
    assert c is not None
    assert (c.state, c.verified_at, c.received_at) == ("VERIFIED", s(0.4), s(0.3))
    assert c.failed_at is None and c.audits == ("CMD_STATE",)


def test_received_then_not_verified_in_one_call() -> None:  # rule 1 then rule 2
    c = decide(row(), OK, None, now=s(15.0), caught_up=True)
    assert c is not None
    assert (c.state, c.failure_reason, c.received_at, c.failed_at) == (
        "FAILED",
        "NOT_VERIFIED",
        s(0.3),
        s(15.0),
    )


def test_evidence_times_are_stored() -> None:
    rec = decide(row(), OK, None, s(0.5), True)
    rej = decide(row(), ack(0.3, False), None, s(9), True)
    echo_only = decide(row(), None, s(2.0), s(9), True)
    timeout = decide(row(), None, None, s(9), True)
    assert rec and rec.received_at == s(0.3) and rec.verified_at is None
    assert rej and rej.failed_at == s(0.3)  # the ack's time, not the pass's
    assert echo_only and echo_only.verified_at == s(2.0) and echo_only.received_at is None
    assert timeout and timeout.failed_at == s(9)


def test_late_evidence_audits_and_event() -> None:
    both = decide(row(), ack(6.0), s(6.1), s(6.2), True)
    assert both is not None
    assert both.audits == ("CMD_STATE", "CMD_LATE_ACK", "CMD_LATE_VERIFICATION")
    assert both.late_verification and both.late_ack_at == s(6.0)
    late = decide(
        row("FAILED", failure_reason="NOT_VERIFIED", failed_at=s(15)), None, s(16), s(17), True
    )
    assert late is not None and late.state is None  # FAILED stays terminal
    assert late.audits == ("CMD_LATE_VERIFICATION",) and late.late_verification
    late_ack = decide(failed(NR, 5), OK, None, s(7), True)
    assert late_ack is not None and late_ack.audits == ("CMD_LATE_ACK",)
    assert not late_ack.late_verification
    plain = decide(row(), None, None, s(5), True)
    assert plain is not None and plain.audits == ("CMD_STATE",) and not plain.late_verification


def test_late_window_is_a_parameter() -> None:
    r = failed(NR, 5)
    assert decide(r, None, s(20), s(20), True, late_window_s=10.0) is None
    assert decide(r, None, s(15), s(15), True, late_window_s=10.0) is not None


@pytest.mark.parametrize("r,ack,echo,now,cu,want", CASES)
def test_guards_are_monotonic(
    r: CmdRow, ack: AckSeen | None, echo: datetime | None, now: datetime, cu: bool, want: Want
) -> None:
    c = decide(r, ack, echo, now, cu)
    if c is None:
        return
    assert c.guard == (EARLIER[c.state] if c.state else ("FAILED",))
    assert r.state in c.guard  # the row it was decided from passes its own guard
