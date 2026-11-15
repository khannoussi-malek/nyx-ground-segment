"""Pure command decision from stored evidence (D-CMD-01..05, D-CMD-11, ruling R-j).

Evidence timestamps decide, never when the pass runs: an ack or echo inside its window wins over
a timeout judged in the same pass, and timeouts apply only once the owner has caught up.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from gs.common.audit import Action
from gs.common.interfaces.sse import CmdState, FailureReason

EARLIER: dict[CmdState, tuple[CmdState, ...]] = {
    "SENT": ("QUEUED",),
    "RECEIVED": ("QUEUED", "SENT"),
    "VERIFIED": ("QUEUED", "SENT", "RECEIVED"),
    "FAILED": ("QUEUED", "SENT", "RECEIVED"),
}
# SEND_ERROR too: vehicle evidence beats the API's "not sent" (P-pf16; outcome() checks it first)
LATE_REASONS: tuple[FailureReason, ...] = (
    "NOT_RECEIVED",
    "NOT_VERIFIED",
    "ACK_INVALID",
    "SEND_ERROR",
)
LATE_ACK_REASONS: tuple[FailureReason, ...] = ("NOT_RECEIVED", "ACK_INVALID", "SEND_ERROR")


@dataclass(frozen=True)
class CmdRow:
    cmd_id: UUID
    name: str
    state: CmdState
    failure_reason: FailureReason | None
    queued_at: datetime
    sent_at: datetime | None
    received_at: datetime | None
    failed_at: datetime | None
    late_verified_at: datetime | None
    late_ack_at: datetime | None
    receive_s: float
    verify_s: float


@dataclass(frozen=True)
class AckSeen:
    accepted: bool
    code: str
    t: datetime
    invalid: bool = False  # the entry failed TcAck (D-ING-27)


@dataclass(frozen=True)
class Change:
    """Fields to set; None leaves a column unchanged. `guard` = the states the UPDATE may match."""

    state: CmdState | None = None
    failure_reason: FailureReason | None = None
    received_at: datetime | None = None
    verified_at: datetime | None = None
    failed_at: datetime | None = None
    late_verified_at: datetime | None = None
    late_ack_at: datetime | None = None
    late_ack_accepted: bool | None = None
    guard: tuple[CmdState, ...] = ()
    # every state passed through, in order: one CMD_STATE audit each (P-pf37, D-CMD-08)
    states: tuple[CmdState, ...] = ()
    audits: tuple[Action, ...] = ()  # one CMD_STATE per entry of `states` first, then late ones
    late_verification: bool = False  # write the LATE_VERIFICATION event


def _move(state: CmdState, **fields: Any) -> Change:
    return Change(
        state=state, guard=EARLIER[state], states=(state,), audits=("CMD_STATE",), **fields
    )


def _fail(
    reason: FailureReason, at: datetime, ack: AckSeen | None, echo_t: datetime | None
) -> Change:
    """FAILED plus whatever late evidence this pass already holds (P-pf14)."""
    c = _move("FAILED", failure_reason=reason, failed_at=at)
    if ack is not None and not ack.invalid and reason in LATE_ACK_REASONS:
        c = replace(
            c,
            late_ack_at=ack.t,
            late_ack_accepted=ack.accepted,
            audits=(*c.audits, "CMD_LATE_ACK"),
        )
    if echo_t is not None:
        c = replace(
            c,
            late_verified_at=echo_t,
            late_verification=True,
            audits=(*c.audits, "CMD_LATE_VERIFICATION"),
        )
    return c


def _start(cmd: CmdRow) -> datetime:
    return cmd.sent_at or cmd.queued_at  # coalesce(sent_at, queued_at): QUEUED times out too


def _open(
    cmd: CmdRow, ack: AckSeen | None, echo_t: datetime | None, now: datetime, caught_up: bool
) -> Change | None:
    """QUEUED / SENT (rule 1)."""
    r = _start(cmd) + timedelta(seconds=cmd.receive_s)
    if ack is not None and ack.invalid:
        return _fail("ACK_INVALID", now, None, echo_t)
    if ack is not None and ack.t <= r:
        if not ack.accepted:
            return _move("FAILED", failure_reason="REJECTED", failed_at=ack.t)
        received = replace(cmd, state="RECEIVED", received_at=ack.t)
        then = _received(received, None, echo_t, now, caught_up)  # rule 2 in the same call
        if then is None:
            return _move("RECEIVED", received_at=ack.t)
        # one UPDATE; RECEIVED and the next state each get their CMD_STATE row, in order
        return replace(
            then,
            received_at=ack.t,
            states=("RECEIVED", *then.states),
            audits=("CMD_STATE", *then.audits),
        )
    if echo_t is not None and echo_t <= r:
        return _move("VERIFIED", verified_at=echo_t)  # received_at stays NULL
    if caught_up and now >= r:
        return _fail("NOT_RECEIVED", now, ack, echo_t)
    return None


def _received(
    cmd: CmdRow, ack: AckSeen | None, echo_t: datetime | None, now: datetime, caught_up: bool
) -> Change | None:
    """RECEIVED (rule 2)."""
    v = _start(cmd) + timedelta(seconds=cmd.verify_s)
    if ack is not None and ack.invalid:  # P-pf14: the vehicle answered, unreadably
        return _fail("ACK_INVALID", now, None, echo_t)
    if echo_t is not None and echo_t <= v:
        return _move("VERIFIED", verified_at=echo_t)
    if caught_up and now >= v:
        return _fail("NOT_VERIFIED", now, None, echo_t)
    return None


def _late(
    cmd: CmdRow, ack: AckSeen | None, echo_t: datetime | None, now: datetime, window_s: float
) -> Change | None:
    """FAILED (rule 3): first late echo / ack within the window; FAILED stays terminal."""
    if cmd.failure_reason not in LATE_REASONS or cmd.failed_at is None:
        return None
    if now - cmd.failed_at > timedelta(seconds=window_s):
        return None
    c = Change(guard=("FAILED",))
    if echo_t is not None and cmd.late_verified_at is None:
        c = replace(
            c,
            late_verified_at=echo_t,
            late_verification=True,
            audits=("CMD_LATE_VERIFICATION",),
        )
    late_ack_ok = cmd.failure_reason in LATE_ACK_REASONS and cmd.late_ack_at is None
    if ack is not None and not ack.invalid and late_ack_ok:
        c = replace(
            c,
            late_ack_at=ack.t,
            late_ack_accepted=ack.accepted,
            audits=(*c.audits, "CMD_LATE_ACK"),
        )
    return c if c.audits else None


def decide(
    cmd: CmdRow,
    ack: AckSeen | None,
    echo_t: datetime | None,
    now: datetime,
    caught_up: bool,
    late_window_s: float = 600.0,
) -> Change | None:
    """The change the evidence calls for, or None. `ack` = the command's tcack (first seen),
    `echo_t` = t_onboard of the first frame whose last_cmd_id is this command."""
    if cmd.state in ("QUEUED", "SENT"):
        return _open(cmd, ack, echo_t, now, caught_up)
    if cmd.state == "RECEIVED":
        return _received(cmd, ack, echo_t, now, caught_up)
    if cmd.state == "FAILED":
        return _late(cmd, ack, echo_t, now, late_window_s)
    return None  # VERIFIED
