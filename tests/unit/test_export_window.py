from datetime import UTC, datetime, timedelta, timezone

from gs.api.export import STAMP, utc_window
from gs.api.workers import hashes_in_window, spans_change

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


T = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
H = timedelta(hours=1)
EVENTS = [(T - H, "a"), (T + H, "b"), (T + 2 * H, "b")]


def test_window_spanning_two_hashes_flagged() -> None:
    assert hashes_in_window(EVENTS, T, T + 3 * H) == ["a", "b"]  # in force at start, then applied
    assert spans_change(hashes_in_window(EVENTS, T, T + 3 * H))


def test_single_hash_not_flagged() -> None:
    assert hashes_in_window(EVENTS, T - H // 2, T + H // 2) == ["a"]
    assert not spans_change(["a"]) and not spans_change([])
    assert hashes_in_window(EVENTS, T + 3 * H, T + 4 * H) == ["b"]  # inside none: still in force
