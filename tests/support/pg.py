"""Postgres helpers: DSNs for the compose db, initdb-style migration apply, throwaway databases."""

import secrets
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from tests.support.env import REPO, dotenv

HOST = "127.0.0.1"
RESET_SEQ_MAX = 50  # design: a drop to <= this is a vehicle reset


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


def newest_t(vehicle: str) -> datetime | None:
    """t_onboard of the newest stored frame of a vehicle."""
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select max(t_onboard) from telemetry where vehicle=%s", (vehicle,)
        ).fetchone()
    return row[0] if row else None


def stored_row(vehicle: str, seq: int) -> dict[str, object]:
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        cur = c.execute("select * from telemetry where vehicle=%s and seq=%s", (vehicle, seq))
        return cur.fetchone() or {}


def events_for(
    vehicle: str | None, kind: str | None = None, since: datetime | None = None
) -> list[dict[str, Any]]:
    """events rows of a vehicle (None: ground events) in insertion order; `since` filters on
    created_at."""
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        return c.execute(
            "select * from events where vehicle is not distinct from %s"
            " and (%s::text is null or kind=%s)"
            " and (%s::timestamptz is null or created_at >= %s) order by id",
            (vehicle, kind, kind, since, since),
        ).fetchall()


def alarm_events_for(vehicle: str, since: datetime | None = None) -> list[dict[str, Any]]:
    """alarm_events rows of a vehicle in insertion order; `since` filters on processed_at."""
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        return c.execute(
            "select * from alarm_events where vehicle=%s"
            " and (%s::timestamptz is null or processed_at >= %s) order by id",
            (vehicle, since, since),
        ).fetchall()


def seqs_since(vehicle: str, since: datetime) -> list[int]:
    """Stored seqs with t_onboard >= since, in t_onboard order."""
    with psycopg.connect(app_dsn("gs")) as c:
        rows = c.execute(
            "select seq from telemetry where vehicle=%s and t_onboard >= %s order by t_onboard",
            (vehicle, since),
        ).fetchall()
    return [r[0] for r in rows]


def out_of_order(vehicle: str, since: datetime) -> int:
    """Inversions of seq when ordered by t_received (frames since `since` by t_onboard)."""
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select count(*) from (select seq, lag(seq) over (order by t_received, seq) p"
            " from telemetry where vehicle=%s and t_onboard >= %s) t where seq < p",
            (vehicle, since),
        ).fetchone()
    assert row is not None
    return int(row[0])


def lock_holder(vehicle: str, dsn: str | None = None) -> str | None:
    """application_name of the session holding pg_advisory_lock(hashtext('tm:'||vehicle)).

    A bigint key is split over classid/objid (high/low 32 bits), also when the int4 hash is
    negative, hence the shifted comparison.
    """
    with psycopg.connect(dsn or app_dsn("gs")) as c:
        rows = c.execute(
            "select a.application_name from pg_locks l join pg_stat_activity a using (pid)"
            " where l.locktype='advisory' and l.granted"
            " and ((l.classid::bigint << 32) | l.objid::bigint) = hashtext('tm:' || %s)::bigint",
            (vehicle,),
        ).fetchall()
    assert len(rows) <= 1, f"advisory lock for {vehicle} granted to {len(rows)} sessions"
    return str(rows[0][0]) if rows else None


def gapless(seqs: Sequence[int]) -> bool:
    """Each step is +1 mod 2**32, or a drop to <= RESET_SEQ_MAX (a vehicle reset)."""
    return all(
        b == (a + 1) % 2**32 or b <= RESET_SEQ_MAX for a, b in zip(seqs, seqs[1:], strict=False)
    )


def rows(sql: str, *args: object, db: str = "gs") -> list[dict[str, Any]]:
    """An app-role query on `db`, rows as dicts."""
    with psycopg.connect(app_dsn(db), row_factory=dict_row) as c:
        return c.execute(sql, args).fetchall()


def audit_rows(
    action: str | None = None, actor: str | None = None, since: datetime | None = None
) -> list[dict[str, Any]]:
    """audit_log rows in insertion order, optionally by action, actor and `at >= since`."""
    return rows(
        "select * from audit_log where (%s::text is null or action=%s)"
        " and (%s::text is null or actor=%s) and (%s::timestamptz is null or at >= %s) order by id",
        action, action, actor, actor, since, since,
    )  # fmt: skip


def commands_for(vehicle: str) -> list[dict[str, Any]]:
    return rows("select * from commands where vehicle=%s order by queued_at", vehicle)


def ack_requests_for(vehicle: str) -> list[dict[str, Any]]:
    return rows("select * from alarm_ack_request where vehicle=%s order by requested_at", vehicle)


def open_alarm(vehicle: str, param: str) -> dict[str, Any] | None:
    """The not-CLEARED alarm of (vehicle, param); the partial unique index allows at most one."""
    found = rows(
        "select * from alarms where vehicle=%s and param=%s and state <> 'CLEARED'", vehicle, param
    )
    return found[0] if found else None


def insert_rows(vehicle: str, rows_: Sequence[Mapping[str, Any]]) -> None:
    """Telemetry rows as gs_app; each mapping needs t_onboard and seq, other columns (mode
    defaults to TEST) are the parameter columns."""
    with psycopg.connect(app_dsn("gs")) as c:
        for row in rows_:
            cols = {"vehicle": vehicle, "mode": "TEST"} | dict(row)
            c.execute(
                sql.SQL("insert into telemetry ({}) values ({})").format(
                    sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                ),
                list(cols.values()),
            )
