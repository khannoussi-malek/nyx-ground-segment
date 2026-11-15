from datetime import UTC, datetime, timedelta, timezone

import pytest

from gs.archiver.hours import (
    ELIGIBLE_AFTER,
    HOUR,
    delete_due,
    eligible,
    hour_bounds,
    hour_of,
    object_key,
    sidecar,
)

H = datetime(2026, 11, 14, 13, tzinfo=UTC)


def test_bounds_are_half_open_utc_hour() -> None:
    w = hour_bounds("NYX-TA9", H)
    assert (w.start, w.end) == (H, H + HOUR)
    assert "t_onboard < %(end)s" in w.SQL and "t_onboard >= %(start)s" in w.SQL
    assert w.params() == {"vehicle": "NYX-TA9", "start": H, "end": H + HOUR}


def test_bounds_reject_unaligned_and_naive() -> None:
    for bad in (
        H + timedelta(seconds=1),
        H + timedelta(microseconds=1),
        H.replace(tzinfo=None),
        datetime(2026, 11, 14, 14, tzinfo=timezone(timedelta(hours=5, minutes=30))),  # 08:30 UTC
    ):
        with pytest.raises(ValueError):
            hour_bounds("V", bad)


def test_bounds_accept_an_aligned_instant_in_another_zone_as_utc() -> None:
    w = hour_bounds("V", datetime(2026, 11, 14, 14, tzinfo=timezone(timedelta(hours=1))))
    assert w.start == H and w.start.utcoffset() == timedelta(0)


def test_hour_of_floors_in_utc() -> None:
    assert hour_of(H + timedelta(minutes=59, seconds=59, microseconds=999999)) == H
    assert hour_of(datetime(2026, 11, 14, 15, 30, tzinfo=timezone(timedelta(hours=2)))) == H
    assert hour_of(
        datetime(2026, 11, 14, 14, 45, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    ) == datetime(2026, 11, 14, 9, tzinfo=UTC)  # floors the UTC instant, not the local clock
    with pytest.raises(ValueError):
        hour_of(datetime(2026, 11, 14, 13))


def test_eligible_at_exactly_ten_minutes() -> None:
    assert eligible(H, H + HOUR + ELIGIBLE_AFTER)
    assert not eligible(H, H + HOUR + ELIGIBLE_AFTER - timedelta(microseconds=1))


def test_delete_due_at_retention() -> None:
    assert delete_due(H, H + HOUR + timedelta(hours=25), 25)
    assert not delete_due(H, H + timedelta(hours=25), 25)
    assert delete_due(H, H + HOUR + timedelta(hours=1, minutes=30), 1.5)


def test_object_keys() -> None:
    assert object_key("NYX-TA9", H) == "NYX-TA9/2026/11/14/13.parquet"
    assert object_key("NYX-TA9", H, 2) == "NYX-TA9/2026/11/14/13.v2.parquet"
    assert object_key("V", datetime(2026, 1, 2, 3, tzinfo=UTC)) == "V/2026/01/02/03.parquet"
    assert sidecar("NYX-TA9/2026/11/14/13.parquet", "ab" * 32) == (
        "NYX-TA9/2026/11/14/13.parquet.sha256",
        f"{'ab' * 32}  13.parquet\n".encode(),
    )


def test_object_key_rejects_unaligned_naive_and_negative_versions() -> None:
    for bad in (H + timedelta(minutes=1), H.replace(tzinfo=None)):
        with pytest.raises(ValueError):
            object_key("V", bad)
    with pytest.raises(ValueError):
        object_key("V", H, -1)
    assert (
        object_key("V", datetime(2026, 11, 14, 14, tzinfo=timezone(timedelta(hours=1))))
        == "V/2026/11/14/13.parquet"
    )
