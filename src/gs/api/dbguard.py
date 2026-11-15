"""Track D routes: a database error is 503 UNAVAILABLE "database unreachable", never a 500
(P-pf27; auth.py keeps its own copy of the same rule)."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import AbstractAsyncContextManager, contextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import psycopg
from fastapi import Request
from psycopg_pool import PoolTimeout

from gs.api.deps import ApiError

log = logging.getLogger("gs.api")
DB_TIMEOUT_S = 3


def connect(request: Request) -> AbstractAsyncContextManager[psycopg.AsyncConnection[Any]]:
    """A pooled connection that gives up after DB_TIMEOUT_S instead of the pool's 30 s."""
    cm: AbstractAsyncContextManager[psycopg.AsyncConnection[Any]] = (
        request.app.state.pool.connection(timeout=DB_TIMEOUT_S)
    )
    return cm


@contextmanager
def db_503(request: Request) -> Iterator[None]:
    try:
        yield
    except (psycopg.Error, PoolTimeout) as e:
        log.warning("database error on %s: %s: %s", request.url.path, type(e).__name__, e)
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "database unreachable") from e


def utc(values: Sequence[Any]) -> list[Any]:
    """timestamptz arrives in the session's TimeZone; the bodies take UTC only (like views._utc)."""
    return [v.astimezone(UTC) if isinstance(v, datetime) else v for v in values]
