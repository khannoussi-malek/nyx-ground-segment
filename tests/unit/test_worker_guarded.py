"""worker_calc.guarded turns every failure of a worker into a marker (the pool must never see a
raised MinIO or psycopg error: they do not unpickle). Run in-process: the spawned worker is not
measured by the coverage run."""

from collections.abc import Awaitable
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from gs.api.worker_calc import guarded
from gs.archiver.window import ArchiveCorrupt


async def _ok() -> int:
    return 7


def _raising(e: BaseException) -> Awaitable[None]:
    async def go() -> None:
        raise e

    return go()


def test_ok_and_failure_markers() -> None:
    assert guarded(_ok()) == ("ok", 7)
    when = datetime(2026, 11, 1, 10, tzinfo=UTC)
    kind, text = guarded(_raising(ArchiveCorrupt("V1", when, "a", None)))  # type: ignore[arg-type]
    assert kind == "corrupt" and "V1" in text
    assert guarded(_raising(OSError("minio down")))[0] == "s3"  # type: ignore[arg-type]
    for e in (psycopg.OperationalError("down"), PoolTimeout("slow")):
        assert guarded(_raising(e))[0] == "db"  # type: ignore[arg-type]


def test_other_errors_propagate() -> None:
    with pytest.raises(KeyError):
        guarded(_raising(KeyError("x")))  # type: ignore[arg-type]
