from datetime import UTC, datetime, timedelta, timezone

from gs.api.export import STAMP, utc_window

PLUS5 = timezone(timedelta(hours=5))


def test_session_window_is_utc_for_both_ends() -> None:
    a, b = utc_window(
        datetime(2026, 11, 20, 13, 0, tzinfo=PLUS5), datetime(2026, 11, 20, 14, 30, tzinfo=PLUS5)
    )
    assert (a.tzinfo, b.tzinfo) == (UTC, UTC)
    assert f"{a:{STAMP}}_{b:{STAMP}}" == "20261120T080000Z_20261120T093000Z"


def test_running_session_ends_now_in_utc() -> None:
    _, b = utc_window(datetime(2026, 11, 20, 13, 0, tzinfo=PLUS5), None)
    assert b.tzinfo == UTC and abs(datetime.now(UTC) - b) < timedelta(seconds=5)
