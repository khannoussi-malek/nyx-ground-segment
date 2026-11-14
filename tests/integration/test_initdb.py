from pathlib import Path

import psycopg
import pytest

from tests.support.env import REPO, sh
from tests.support.pg import (
    admin_dsn,
    app_dsn,
    apply_migrations,
    column_types,
    owner_dsn,
    throwaway_db,
)

EXPECTED = {
    "telemetry", "seq_state", "limits_versions", "engine_state", "events", "test_session",
    "alarms", "alarm_events", "alarm_ack_request", "commands", "session_report",
    "archive_object", "audit_log",
}  # fmt: skip


def priv(c: psycopg.Connection[tuple[object, ...]], table: str, p: str) -> bool:
    row = c.execute("select has_table_privilege('gs_app', %s, %s)", (table, p)).fetchone()
    assert row is not None
    return bool(row[0])


def columns(c: psycopg.Connection[tuple[object, ...]], table: str) -> set[object]:
    rows = c.execute(
        "select column_name from information_schema.columns where table_name=%s", (table,)
    )
    return {r[0] for r in rows.fetchall()}


def test_compose_config_valid() -> None:
    sh("docker", "compose", "config", "-q")


def test_compose_db_healthy() -> None:
    sh("docker", "compose", "up", "-d", "--wait", "db")
    out = sh("docker", "compose", "ps", "--format", "{{.Health}}", "db").stdout.strip()
    assert out == "healthy"


def test_full_schema_on_empty_db() -> None:  # D-ENG-12, EX-08
    with throwaway_db() as db, psycopg.connect(admin_dsn(db)) as c:
        owners: dict[str, str] = dict(
            c.execute(
                "select tablename, tableowner from pg_tables where schemaname='public'"
            ).fetchall()
        )
        assert set(owners) == EXPECTED and set(owners.values()) == {"gs_owner"}
        assert c.execute(
            "select 1 from timescaledb_information.hypertables where hypertable_name='telemetry'"
        ).fetchone()
        for t in ("alarms", "alarm_events"):
            assert {"check_set", "set_sha256"} <= columns(c, t)
        assert priv(c, "audit_log", "INSERT") and not priv(c, "audit_log", "UPDATE")
        assert priv(c, "telemetry", "DELETE") and not priv(c, "events", "UPDATE")


def test_audit_log_is_append_only_even_for_owner() -> None:  # trigger, not just the grant
    with throwaway_db() as db, psycopg.connect(owner_dsn(db), autocommit=True) as c:
        c.execute("insert into audit_log (actor, action) values ('t', 'X')")
        for stmt in (
            "update audit_log set actor='y'",
            "delete from audit_log",
            "truncate audit_log",
        ):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                c.execute(stmt)
        assert c.execute("select count(*) from audit_log").fetchone() == (1,)


def test_out_of_order_schema_fails() -> (
    None
):  # negative control: a referenced table missing must fail
    with throwaway_db(apply=False) as db, pytest.raises(psycopg.errors.UndefinedTable):
        apply_migrations(db, sorted((REPO / "migrations").glob("*.sql"), reverse=True))


def test_compose_db_roles_use_env_passwords() -> (
    None
):  # 00-roles.sh read GS_APP_PASSWORD from .env (R2-16)
    with psycopg.connect(app_dsn("gs")) as c:
        assert c.execute("select current_user").fetchone() == ("gs_app",)


def test_roles_script_is_executable() -> None:
    assert (Path(REPO) / "migrations" / "00-roles.sh").stat().st_mode & 0o111


def test_ack_invalid_and_alarm_value_columns() -> None:
    with throwaway_db() as db, psycopg.connect(owner_dsn(db)) as c:
        assert column_types(c, "alarm_events")["value"] == "double precision"
        c.execute(
            "insert into commands (cmd_id, vehicle, name, args, args_hash, hazardous, issued_by,"
            " state, failure_reason, queued_at) values (gen_random_uuid(), 'V', 'n', '{}', 'h',"
            " false, 'u', 'FAILED', 'ACK_INVALID', now())"
        )
