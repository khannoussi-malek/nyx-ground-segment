"""Worker side of the one-worker process pattern shared by compare and export (EX-02): a read-only
connection, and every failure returned as a picklable marker (see workers.run_in_worker)."""

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any

import psycopg
from psycopg_pool import PoolTimeout

from gs.api.dbguard import DB_TIMEOUT_S
from gs.archiver.s3 import S3_ERRORS
from gs.archiver.window import ArchiveCorrupt
from gs.common.time import iso_z

# One hour of hot rows per statement, so more than the API's 3 s; 30 s still frees the only worker
# when a query is stuck.
STATEMENT_TIMEOUT_MS = 30_000
Outcome = tuple[str, Any]  # ("ok", result) | ("corrupt" | "s3" | "db", message): all picklable


def corrupt_text(e: ArchiveCorrupt) -> str:
    return f"{e}: {e.vehicle} {iso_z(e.hour)}"


def connect(
    database_url: str, statement_timeout_ms: int = STATEMENT_TIMEOUT_MS
) -> Awaitable[psycopg.AsyncConnection[Any]]:
    return psycopg.AsyncConnection.connect(
        database_url, autocommit=True, connect_timeout=DB_TIMEOUT_S,
        options=f"-c default_transaction_read_only=on -c statement_timeout={statement_timeout_ms}",
    )  # fmt: skip


def guarded(work: Coroutine[Any, Any, Any]) -> Outcome:
    """Run `work` in its own event loop. A failure comes back as a marker, never raised: MinIO and
    psycopg errors do not unpickle, and a broken result would break the pool."""
    try:
        return "ok", asyncio.run(work)
    except ArchiveCorrupt as e:
        return "corrupt", corrupt_text(e)
    except S3_ERRORS as e:
        return "s3", f"{type(e).__name__}: {e}"
    except (psycopg.Error, PoolTimeout) as e:
        return "db", f"{type(e).__name__}: {e}"
