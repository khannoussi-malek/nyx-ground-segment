from datetime import UTC, datetime, timedelta

from gs.archiver.hours import HOUR
from gs.archiver.plan import (
    Action,
    Anomaly,
    Archive,
    ArchiveRow,
    DeleteHot,
    MergeLate,
    oldest_unarchived,
    plan_pass,
)

H = datetime(2026, 11, 14, 13, tzinfo=UTC)
NOW = H + timedelta(hours=30)
V = "NYX-TA1"


def row(
    hour: datetime = H, rows: int = 36000, version: int = 0, deleted: bool = False, v: str = V
) -> ArchiveRow:
    return ArchiveRow(
        v, hour, "ab" * 32, rows, hour, hour + HOUR, version, NOW, NOW if deleted else None, None
    )


def test_new_eligible_hours_archived_oldest_first() -> None:
    counts = {(V, H + HOUR): 5, (V, H): 7, ("NYX-TA0", H): 3}
    assert plan_pass(NOW, counts, {}, 25) == [
        Archive("NYX-TA0", H, 0),
        Archive(V, H, 0),
        Archive(V, H + HOUR, 0),
    ]


def test_current_hour_and_ten_minute_window_not_archived() -> None:
    now = H + HOUR + timedelta(minutes=10)
    assert plan_pass(now, {(V, H): 5}, {}, 25) == [Archive(V, H, 0)]
    assert plan_pass(now - timedelta(microseconds=1), {(V, H): 5}, {}, 25) == []
    assert plan_pass(NOW, {(V, NOW.replace(minute=0, second=0)): 5}, {}, 25) == []


def test_empty_hours_are_not_archived() -> None:
    assert plan_pass(NOW, {(V, H): 0}, {}, 25) == []


def test_late_rows_before_delete_give_next_version() -> None:
    assert plan_pass(NOW, {(V, H): 36001}, {(V, H): row()}, 25) == [Archive(V, H, 1)]
    assert plan_pass(NOW, {(V, H): 36002}, {(V, H): row(version=3)}, 25) == [Archive(V, H, 4)]


def test_late_rows_after_delete_merge() -> None:
    assert plan_pass(NOW, {(V, H): 3}, {(V, H): row(deleted=True)}, 25) == [MergeLate(V, H, 1)]
    assert plan_pass(NOW, {}, {(V, H): row(deleted=True, version=2)}, 25) == []
    assert plan_pass(NOW, {(V, H): 0}, {(V, H): row(deleted=True)}, 25) == []


def test_delete_only_when_due_and_counts_equal() -> None:
    assert plan_pass(NOW, {(V, H): 36000}, {(V, H): row()}, 25) == [DeleteHot(V, H)]
    young = H + timedelta(hours=20)
    assert plan_pass(young, {(V, H): 36000}, {(V, H): row()}, 25) == []
    assert plan_pass(NOW, {(V, H): 36001}, {(V, H): row()}, 25) == [
        Archive(V, H, 1)
    ]  # late rows: no delete


def test_missing_hot_rows_is_anomaly() -> None:
    got = plan_pass(NOW, {(V, H): 35999}, {(V, H): row()}, 25)
    assert got == [Anomaly(V, H, "35999 hot rows, archived 36000")]
    assert plan_pass(NOW, {}, {(V, H): row()}, 25) == [Anomaly(V, H, "0 hot rows, archived 36000")]


def test_order_builds_first_then_deletes_then_anomalies() -> None:
    h2, h3, h4 = (H + HOUR * i for i in (1, 2, 3))
    counts = {(V, H): 36000, (V, h2): 36000, (V, h3): 9, (V, h4): 4}
    rows = {(V, H): row(H), (V, h2): row(h2), (V, h3): row(h3, rows=10)}
    got: list[Action] = plan_pass(NOW, counts, rows, 25)
    assert got == [
        Archive(V, h4, 0),
        DeleteHot(V, H),
        DeleteHot(V, h2),
        Anomaly(V, h3, "9 hot rows, archived 10"),
    ]


def test_oldest_unarchived() -> None:
    counts = {(V, H + HOUR): 5, (V, H): 7, (V, H - HOUR): 0}
    assert oldest_unarchived(NOW, counts, {}) == H
    assert oldest_unarchived(NOW, counts, {(V, H): row()}) == H + HOUR
    assert oldest_unarchived(NOW, counts, {(V, H): row(), (V, H + HOUR): row(H + HOUR)}) is None
    assert (
        oldest_unarchived(NOW, {(V, NOW.replace(minute=0, second=0)): 5}, {}) is None
    )  # not yet eligible
