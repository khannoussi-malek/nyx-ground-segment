"""Postgres helpers: DSNs for the compose db, initdb-style migration apply, throwaway databases."""

import secrets
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from tests.support.env import REPO, dotenv

HOST = "127.0.0.1"


def _dsn(user: str, password: str, db: str) -> str:
    return f"postgresql://{user}:{password}@{HOST}:{dotenv()['DB_HOST_PORT']}/{db}"


def admin_dsn(db: str) -> str:
    return _dsn("postgres", dotenv()["POSTGRES_PASSWORD"], db)


def owner_dsn(db: str) -> str:
    return _dsn("gs_owner", dotenv()["GS_OWNER_PASSWORD"], db)


def app_dsn(db: str) -> str:
    return _dsn("gs_app", dotenv()["GS_APP_PASSWORD"], db)


def apply_migrations(db: str, files: Sequence[Path]) -> None:
    """One superuser autocommit connection per file; raises on the first error (ON_ERROR_STOP)."""
    for f in files:
        with psycopg.connect(admin_dsn(db), autocommit=True) as c:
            c.execute(f.read_text())


@contextmanager
def throwaway_db(apply: bool = True) -> Iterator[str]:
    name = "schema_" + secrets.token_hex(4)
    with psycopg.connect(admin_dsn("postgres"), autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {} OWNER gs_owner").format(sql.Identifier(name)))
    try:
        if apply:
            apply_migrations(name, sorted((REPO / "migrations").glob("*.sql")))
        yield name
    finally:
        with psycopg.connect(admin_dsn("postgres"), autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def column_types(conn: psycopg.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(
        "select column_name, data_type from information_schema.columns where table_name=%s",
        (table,),
    ).fetchall()
    return {name: dtype for name, dtype in rows}


def stored(vehicle: str) -> int:
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute("select count(*) from telemetry where vehicle=%s", (vehicle,)).fetchone()
    assert row is not None
    return int(row[0])


def seqs_by_t_received(vehicle: str) -> list[int]:
    with psycopg.connect(app_dsn("gs")) as c:
        rows = c.execute(
            "select seq from telemetry where vehicle=%s order by t_received", (vehicle,)
        ).fetchall()
    return [r[0] for r in rows]


def stored_row(vehicle: str, seq: int) -> dict[str, object]:
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        cur = c.execute("select * from telemetry where vehicle=%s and seq=%s", (vehicle, seq))
        return cur.fetchone() or {}
