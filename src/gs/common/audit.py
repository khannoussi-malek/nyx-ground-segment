"""audit_log writes (D-CMD-08): one row per action, in the caller's transaction."""

from collections.abc import Mapping
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from gs.ingest.events import details_json

Action = Literal[
    "LOGIN", "LOGIN_FAILED", "LOGOUT", "CMD_REQUESTED", "CMD_CONFIRM_REQUIRED", "CMD_REJECTED",
    "CMD_STATE", "CMD_LATE_ACK", "CMD_LATE_VERIFICATION", "ALARM_ACK", "ALARM_ACK_APPLIED",
    "LIMITS_RELOADED", "SESSION_START", "SESSION_STOP", "REPORT_REFROZEN",
]  # fmt: skip


async def audit(
    conn: psycopg.AsyncConnection,
    actor: str,
    action: Action,
    reason: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Times in `details` are written with iso_z (D-ENG-18)."""
    await conn.execute(
        "INSERT INTO audit_log (actor, action, reason, details) VALUES (%s, %s, %s, %s)",
        (actor, action, reason, Jsonb(dict(details or {}), dumps=details_json)),
    )
