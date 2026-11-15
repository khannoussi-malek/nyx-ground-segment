"""A killed archiver loses nothing: crash after the PUT / after the re-read, restart, the hour is
archived, hash-verified, and its hot rows are untouched. Real stack, NYX-TA9 history backfilled
through the stream path. `make up` first; run through syslock.sh."""

import hashlib
import subprocess
from collections.abc import Iterator
from datetime import datetime

import pytest

from gs.archiver.hours import object_key
from tests.support.archive import TA9, archive_rows, backfill_ta9, next_ta9_window, stored_in
from tests.support.compose import compose, recreate, restart_count
from tests.support.env import wait_until
from tests.support.s3 import inspect_health, s3_client

HOURLY_FRAMES = 36_000


@pytest.fixture(scope="module", autouse=True)
def archiver_default() -> Iterator[None]:
    yield
    recreate(["archiver"])  # compose defaults again, whatever the tests did


def _backfill_one_hour() -> datetime:
    compose("stop", "archiver")  # a running one would archive the hour before the hook is armed
    t_from, t_to = next_ta9_window(1)
    backfill_ta9(t_from, t_to)
    wait_until(lambda: stored_in(TA9, t_from) == HOURLY_FRAMES, 600)  # ingest drains the stream
    return t_from


@pytest.mark.parametrize("point", ["archive:after_put", "archive:after_reread"])
def test_kill_mid_upload_then_resume(point: str) -> None:
    hour = _backfill_one_hour()
    # recreate without --wait: the hook kills the first pass; retention 1000 h keeps the hot rows
    recreate(
        ["archiver"],
        {"GS_TEST_HOOKS": "1", "ARCHIVER_CRASH_AT": point, "ARCHIVE_LOOP_S": "2",
         "HOT_RETENTION_H": "1000"},
        wait=False,
    )  # fmt: skip
    wait_until(lambda: restart_count("archiver") >= 1, 120)  # the new container died on the hook
    # the crash is before the row insert: nothing is recorded for the hour at that moment
    # (the restarted process needs a second to start, connect and rebuild the hour)
    assert hour not in [r["hour"] for r in archive_rows(TA9)]
    wait_until(lambda: hour in [r["hour"] for r in archive_rows(TA9)], 180)  # resumed
    (row,) = [r for r in archive_rows(TA9) if r["hour"] == hour]
    data = s3_client("root").get_object("archive", object_key(TA9, hour)).read()
    assert hashlib.sha256(data).hexdigest() == row["sha256"]  # the object is the recorded one
    assert row["rows"] == HOURLY_FRAMES and row["version"] == 0
    assert stored_in(TA9, hour) == HOURLY_FRAMES  # hot rows intact
    assert restart_count("archiver") == 1  # killed once (the marker), not in a crash loop


def test_backfill_cli_in_compose() -> None:
    t_from, _ = next_ta9_window(1)
    out = backfill_ta9(t_from, t_from.replace(second=10))  # 100 frames
    assert "sent 100 frames to tm:NYX-TA9" in out
    wait_until(lambda: stored_in(TA9, t_from) == 100, 120)
    bad = compose(
        "run", "--rm", "-T", "--no-deps", "simulator", "gs-sim", "backfill",
        "--vehicle", "NYX-TA1", "--from", "2026-01-01T00:00:00Z", "--to", "2026-01-01T01:00:00Z",
        check=False,
    )  # fmt: skip
    assert bad.returncode == 2 and "backfill writes only NYX-TA9" in bad.stderr


def test_archiver_is_healthy_at_the_end() -> None:
    recreate(["archiver"])
    assert inspect_health("archiver") == "healthy"
    assert subprocess.run(["docker", "compose", "config", "-q"]).returncode == 0
