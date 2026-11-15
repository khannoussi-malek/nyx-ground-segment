"""audit_log is append-only on the live database (D-CMD-08): gs_app has no UPDATE/DELETE/TRUNCATE
grant, and the trigger stops even the table owner. Every statement runs in a transaction that is
rolled back, so a missing guard could never destroy the real log."""

from collections.abc import Callable

import psycopg
import pytest

from tests.support.pg import app_dsn, owner_dsn

STATEMENTS = (
    "update audit_log set actor = actor",
    "delete from audit_log",
    "truncate audit_log",
)


@pytest.mark.parametrize(
    ("dsn", "error", "match"),
    [
        (app_dsn, psycopg.errors.InsufficientPrivilege, "permission denied"),
        (owner_dsn, psycopg.errors.RaiseException, "append-only"),  # the trigger, not the grant
    ],
    ids=["gs_app", "gs_owner"],
)
@pytest.mark.parametrize("stmt", STATEMENTS)
def test_update_delete_truncate_fail(
    dsn: Callable[[str], str], error: type[Exception], match: str, stmt: str
) -> None:
    with psycopg.connect(dsn("gs")) as c, pytest.raises(error, match=match):
        c.execute(stmt)  # the connection's exit rolls the failed transaction back
