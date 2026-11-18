"""A killed archiver loses nothing: crash after the PUT / after the re-read, restart, the hour is
archived, hash-verified, and its hot rows are untouched. Real stack, NYX-TA9 history backfilled
through the stream path. `make up` first; run through syslock.sh."""

import hashlib
import subprocess
from collections.abc import Iterator
from datetime import datetime

import pytest

from gs.archiver.hours import object_key
from gs.common.hooks import CRASH_CODE
from tests.support.archive import TA9, archive_rows, backfill_ta9, next_ta9_window, stored_in
from tests.support.compose import compose, container_exit_code, recreate, restart_count
from tests.support.env import REPO, wait_until
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


def _docker_update_restart(policy: str) -> None:
    cid = compose("ps", "-a", "-q", "archiver").stdout.strip()
    subprocess.run(
        ["docker", "update", f"--restart={policy}", cid], check=True, capture_output=True
    )


@pytest.mark.parametrize("point", ["archive:after_put", "archive:after_reread"])
def test_kill_mid_upload_then_resume(point: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hour = _backfill_one_hour()
    for k, v in {"GS_TEST_HOOKS": "1", "ARCHIVER_CRASH_AT": point, "ARCHIVE_LOOP_S": "2",
                 "HOT_RETENTION_H": "1000"}.items():  # fmt: skip
        # retention 1000 h (G4-8): with the default 25 h a days-old backfilled hour is delete-due
        # the moment it is archived, and "hot rows intact" could not be asserted
        monkeypatch.setenv(k, v)
    compose("up", "--no-start", "--no-deps", "archiver")  # a new container with the hook armed
    _docker_update_restart("no")  # stay down once it dies: "while it is down" is then exact
    compose("start", "archiver")
    wait_until(lambda: container_exit_code("archiver") == CRASH_CODE, 120)  # it died on the hook
    assert hour not in [r["hour"] for r in archive_rows(TA9)]  # killed before the row insert
    assert stored_in(TA9, hour) == HOURLY_FRAMES  # and the hot rows are all there
    s3_client("root").stat_object("archive", object_key(TA9, hour))  # put, but never recorded
    _docker_update_restart("unless-stopped")
    compose("start", "archiver")  # the hook fired once per container (marker file): no second kill
    wait_until(lambda: hour in [r["hour"] for r in archive_rows(TA9)], 180)  # resumed
    (row,) = [r for r in archive_rows(TA9) if r["hour"] == hour]
    data = s3_client("root").get_object("archive", object_key(TA9, hour)).read()
    assert hashlib.sha256(data).hexdigest() == row["sha256"]  # the object is the recorded one
    assert row["rows"] == HOURLY_FRAMES and row["version"] == 0 and row["verified_at"] is not None
    assert stored_in(TA9, hour) == HOURLY_FRAMES  # hot rows intact
    assert restart_count("archiver") == 0  # no crash loop after the resume


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
    assert subprocess.run(["docker", "compose", "config", "-q"], cwd=REPO).returncode == 0
