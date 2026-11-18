from datetime import UTC, datetime, timedelta

import pytest

from gs.archiver.hours import HOUR
from gs.archiver.plan import ArchiveRow, oldest_unarchived
from gs.archiver.status import BACKLOG_H, archiver_status

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


def st(**kw: object) -> tuple[str, dict[str, object]]:
    args: dict[str, object] = {
        "now": NOW, "db_ok": True, "redis_ok": True, "s3_ok": True, "oldest_unarchived": None,
        "corrupt": 0, "refused": 0, "last_pass": NOW, "last_snapshot": NOW,
    }  # fmt: skip
    return archiver_status(**(args | kw))  # type: ignore[arg-type]


def test_ok() -> None:
    state, checks = st()
    assert state == "ok"
    assert checks == {
        "oldest_unarchived_hour": None, "scrub": "ok", "delete_refused": 0, "db": "ok",
        "redis": "ok", "minio": "ok", "last_pass": "2026-11-15T12:00:00.000000Z",
        "last_state_snapshot": "2026-11-15T12:00:00.000000Z",
    }  # fmt: skip


def test_backlog_older_than_72h_degraded() -> None:
    assert BACKLOG_H == 72
    assert st(oldest_unarchived=NOW - timedelta(hours=72))[0] == "ok"
    state, checks = st(oldest_unarchived=NOW - timedelta(hours=72, seconds=1))
    assert state == "degraded"
    assert checks["oldest_unarchived_hour"] == "2026-11-12T11:59:59.000000Z"


def test_corrupt_or_refused_degraded() -> None:
    state, checks = st(corrupt=2)
    assert (state, checks["scrub"]) == ("degraded", "2 corrupt")
    state, checks = st(refused=1)
    assert (state, checks["delete_refused"]) == ("degraded", 1)


def test_db_down_is_down_s3_down_degraded() -> None:
    assert st(db_ok=False)[0] == "down"
    assert st(db_ok=False, s3_ok=False)[0] == "down"
    state, checks = st(s3_ok=False)
    assert (state, checks["minio"]) == ("degraded", "down")
    state, checks = st(redis_ok=False)
    assert (state, checks["redis"]) == ("degraded", "down")


def test_backlog_counts_pending_late_rows_g4_1() -> None:
    h1, h2 = NOW - 40 * HOUR, NOW - 50 * HOUR
    row = ArchiveRow("v", h1, "x", 10, h1, h1, 0, NOW, None, None)
    gone = ArchiveRow("v", h2, "x", 10, h2, h2, 0, NOW, NOW, None)
    # archived, not deleted, n > rows -> pending v1; deleted with n > 0 -> pending merge
    assert oldest_unarchived(NOW, {("v", h1): 11}, {("v", h1): row}) == h1
    assert oldest_unarchived(NOW, {("v", h1): 10}, {("v", h1): row}) is None
    assert (
        oldest_unarchived(NOW, {("v", h1): 10, ("v", h2): 1}, {("v", h1): row, ("v", h2): gone})
        == h2
    )
    assert oldest_unarchived(NOW, {("v", h2): 0}, {("v", h2): gone}) is None


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from gs.archiver.main import ArchiverSettings
    from gs.common.config import ConfigError

    for k, v in {
        "DATABASE_URL": "postgresql://x", "REDIS_URL": "redis://x", "S3_ENDPOINT": "m:9000",
        "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "b", "S3_BUCKET": "archive", "BIND_HOST": "h",
        "HEALTH_PORT": "8081",
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    s = ArchiverSettings.from_env()
    assert (s.retention_h, s.loop_s, s.health_port) == (25.0, 300.0, 8081)
    monkeypatch.setenv("HOT_RETENTION_H", "0")
    assert ArchiverSettings.from_env().retention_h == 0.0
    for name, bad in [("HOT_RETENTION_H", "-1"), ("HOT_RETENTION_H", "nan"),
                      ("HOT_RETENTION_H", "x"), ("ARCHIVE_LOOP_S", "0")]:  # fmt: skip
        monkeypatch.setenv(name, bad)
        with pytest.raises(ConfigError):
            ArchiverSettings.from_env()
        monkeypatch.delenv(name)
        monkeypatch.setenv("HOT_RETENTION_H", "25")


def test_plan_corrupt_hours_self_heal_but_never_shrink_or_delete() -> None:
    from gs.archiver.plan import Anomaly, Archive, DeleteHot, plan_pass

    h = NOW - 40 * HOUR
    row = ArchiveRow("v", h, "x", 10, h, h, 0, NOW, None, None)
    gone = ArchiveRow("v", h, "x", 10, h, h, 0, NOW, NOW, None)
    k = frozenset({("v", h)})
    assert plan_pass(NOW, {("v", h): 10}, {("v", h): row}, 0.0) == [DeleteHot("v", h)]
    assert plan_pass(NOW, {("v", h): 10}, {("v", h): row}, 0.0, k) == [Archive("v", h, 1)]
    assert plan_pass(NOW, {("v", h): 11}, {("v", h): row}, 0.0, k) == [Archive("v", h, 1)]
    (odd,) = plan_pass(NOW, {("v", h): 9}, {("v", h): row}, 0.0, k)
    assert isinstance(odd, Anomaly)  # healing would shrink the hour
    assert plan_pass(NOW, {("v", h): 1}, {("v", h): gone}, 0.0, k) == []  # event stays open


def test_archiver_status_snapshot_age() -> None:
    assert st(last_snapshot=NOW - timedelta(hours=48))[0] == "ok"
    old = NOW - timedelta(hours=48, seconds=1)
    state, checks = st(last_snapshot=old)
    assert (state, checks["last_state_snapshot"]) == ("degraded", "2026-11-13T11:59:59.000000Z")
    assert st(last_snapshot=NOW - timedelta(hours=5), snapshot_every_h=2)[0] == "degraded"
    assert st(last_snapshot=None, last_pass=None)[0] == "ok"  # before the first pass
    state, checks = st(last_snapshot=None)  # a pass completed and took none
    assert (state, checks["last_state_snapshot"]) == ("degraded", None)
