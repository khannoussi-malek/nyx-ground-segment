"""D-EXT-11: one bucket width for all sessions, time relative to the session start, NULLs skipped,
empty buckets absent, a running session ends at now."""

from datetime import UTC, datetime, timedelta

import pyarrow as pa

from gs.api.compare_calc import MIN_BUCKET_S, bucket_width_s, buckets, session_end
from gs.api.workers import merge_hashes, spans_change

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def table(offsets_s: list[float], values: list[float | None]) -> pa.Table:
    ts = [T0 + timedelta(seconds=s) for s in offsets_s]
    return pa.table(
        {
            "t_onboard": pa.array(ts, pa.timestamp("us", tz="UTC")),
            "p": pa.array(values, pa.float64()),
        }
    )


def test_width_is_longest_over_1500_with_a_100_ms_floor() -> None:
    assert bucket_width_s([600.0, 3000.0]) == 2.0
    assert bucket_width_s([60.0, 30.0]) == MIN_BUCKET_S == 0.1  # 60 / 1500 = 40 ms -> floor


def test_relative_time_and_aggregates() -> None:
    t = table([0.0, 0.5, 1.2, 1.4], [1.0, 3.0, 10.0, 20.0])
    assert buckets([t], T0, 1.0, "p") == [(0.0, 1.0, 3.0, 2.0, 2), (1.0, 10.0, 20.0, 15.0, 2)]


def test_relative_to_start_not_to_the_first_row() -> None:
    t = table([5.5], [7.0])
    assert buckets([t], T0, 1.0, "p") == [(5.0, 7.0, 7.0, 7.0, 1)]


def test_nulls_skipped() -> None:
    t = table([0.1, 0.2, 0.3], [None, 4.0, None])
    assert buckets([t], T0, 1.0, "p") == [(0.0, 4.0, 4.0, 4.0, 1)]
    assert buckets([table([0.1], [None])], T0, 1.0, "p") == []


def test_empty_buckets_absent() -> None:
    t = table([0.0, 5.0], [1.0, 2.0])
    assert [b[0] for b in buckets([t], T0, 1.0, "p")] == [0.0, 5.0]


def test_a_bucket_spanning_two_tables_is_one_bucket() -> None:
    a, b = table([0.1], [2.0]), table([0.9], [4.0])
    assert buckets([a, b], T0, 1.0, "p") == [(0.0, 2.0, 4.0, 3.0, 2)]


def test_no_tables_no_buckets() -> None:
    assert buckets([], T0, 1.0, "p") == []


def test_running_session_ends_at_now() -> None:
    now = T0 + timedelta(hours=3)
    assert session_end(None, now) == now
    assert session_end(T0 + timedelta(hours=1), now) == T0 + timedelta(hours=1)


def test_compare_flags_two_hashes() -> None:
    assert merge_hashes([["a"], ["a"]]) == ["a"] and not spans_change(merge_hashes([["a"], ["a"]]))
    assert merge_hashes([["a"], ["b", "a"]]) == ["a", "b"]  # each session one set, the two differ
    assert spans_change(merge_hashes([["a"], ["b"]]))
