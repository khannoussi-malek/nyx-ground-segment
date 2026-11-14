from datetime import UTC, datetime, timedelta, timezone

import pytest

from gs.common.time import iso_z


def test_iso_z_always_six_digits_and_z() -> None:
    assert iso_z(datetime(2026, 11, 14, 10, 0, 0, tzinfo=UTC)) == "2026-11-14T10:00:00.000000Z"
    assert iso_z(datetime(2026, 11, 14, 10, 0, 0, 123, tzinfo=UTC)) == "2026-11-14T10:00:00.000123Z"


def test_iso_z_converts_offset_to_utc() -> None:
    dt = datetime(2026, 11, 14, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert iso_z(dt) == "2026-11-14T10:00:00.000000Z"


def test_iso_z_rejects_naive() -> None:
    with pytest.raises(ValueError):
        iso_z(datetime(2026, 11, 14))


def test_iso_z_sorts_like_time() -> None:
    ts = [datetime(2026, 11, 14, 10, 0, 0, us, tzinfo=UTC) for us in (0, 5, 100_000, 999_999)]
    assert sorted(iso_z(t) for t in ts) == [iso_z(t) for t in ts]
