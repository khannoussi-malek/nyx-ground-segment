from datetime import UTC, datetime

import psycopg

from gs.common.audit import audit
from tests.support.env import vid
from tests.support.pg import app_dsn, audit_rows


async def test_audit_row_with_iso_details() -> None:
    actor = f"t-{vid()}"
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        await audit(
            c, actor, "CMD_REQUESTED", "test reason", {"at": datetime(2026, 11, 15, tzinfo=UTC)}
        )
    (row,) = audit_rows(actor=actor)
    assert (row["action"], row["reason"], row["details"]["at"]) == (
        "CMD_REQUESTED", "test reason", "2026-11-15T00:00:00.000000Z",
    )  # fmt: skip


async def test_audit_in_the_callers_transaction() -> None:
    actor = f"t-{vid()}"
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        try:
            async with c.transaction():
                await audit(c, actor, "LOGOUT")
                raise RuntimeError("roll back")
        except RuntimeError:
            pass
        assert audit_rows(actor=actor) == []  # rolled back with the caller's transaction
        await audit(c, actor, "LOGOUT")
    (row,) = audit_rows(actor=actor)
    assert (row["reason"], row["details"]) == (None, {})
