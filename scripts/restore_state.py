"""Restore a state snapshot (D-ARC-12) into an empty database: `restore_state.py <key> [--db gs]`.

Reads .env for the owner DSN and the MinIO archiver credentials; only the stdlib, psycopg and
the archiver's own S3 helpers.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import sys
import tarfile
from pathlib import Path

import psycopg
from psycopg import sql

from gs.archiver import s3
from gs.archiver.snapshot import TABLES

REPO = Path(__file__).resolve().parents[1]
IDENTITY = (
    "select table_name, column_name, pg_get_serial_sequence(table_name, column_name)"
    " from information_schema.columns where table_schema = 'public' and is_identity = 'YES'"
)


def restore(conn: psycopg.Connection, data: bytes) -> int:
    """0 on success, 2 (nothing written) when any snapshot table already has rows."""
    for t in TABLES:
        row = conn.execute(f"select exists (select 1 from {t})").fetchone()
        if row and row[0]:
            print(f"restore_state: target database is not empty ({t} has rows)", file=sys.stderr)
            return 2
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar, conn.transaction():
        for t in TABLES:
            member = tar.extractfile(f"{t}.csv")
            assert member is not None, f"{t}.csv missing from the snapshot"
            # the column list comes from the CSV header, never from the target's column order
            header = member.readline()
            cols = next(csv.reader([header.decode()]))
            with (
                conn.cursor() as cur,
                cur.copy(
                    sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv, HEADER)").format(
                        sql.Identifier(t), sql.SQL(", ").join(map(sql.Identifier, cols))
                    )
                ) as cp,
            ):
                cp.write(header)  # HEADER skips it
                while block := member.read(1 << 16):
                    cp.write(block)
        # COPY wrote the ids itself, so the sequences must catch up before the next INSERT
        for table, col, seq in conn.execute(IDENTITY).fetchall():
            conn.execute(
                sql.SQL(
                    "select setval(%s, coalesce(max({}), 1), max({}) is not null) from {}"
                ).format(sql.Identifier(col), sql.Identifier(col), sql.Identifier(table)),
                (seq,),
            )
    return 0


def _env() -> dict[str, str]:
    lines = (REPO / ".env").read_text().splitlines()
    return dict(ln.split("=", 1) for ln in lines if "=" in ln and ln[0] != "#")


async def _fetch(env: dict[str, str], key: str) -> bytes:
    c = s3.client(
        s3.S3Settings(
            f"127.0.0.1:{env['MINIO_HOST_PORT']}", "archiver", env["S3_ARCHIVER_SECRET"], "archive"
        )
    )
    data = await s3.get(c, "archive", key)
    want = (await s3.get(c, "archive", f"{key}.sha256")).decode().strip()
    if hashlib.sha256(data).hexdigest() != want:
        raise SystemExit(f"restore_state: {key} does not match its .sha256 sidecar")
    return data


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", help="e.g. state/2026/11/18/0300.tar.gz")
    ap.add_argument("--db", default="gs")
    args = ap.parse_args(argv)
    env = _env()
    data = asyncio.run(_fetch(env, args.key))
    dsn = (
        f"postgresql://gs_owner:{env['GS_OWNER_PASSWORD']}@127.0.0.1:{env['DB_HOST_PORT']}"
        f"/{args.db}"
    )
    with psycopg.connect(dsn) as conn:
        return restore(conn, data)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
