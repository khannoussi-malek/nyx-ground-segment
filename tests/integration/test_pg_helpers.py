"""tests/support/pg.py helpers that other tracks' tests rely on."""

from datetime import UTC, datetime, timedelta

import psycopg

from tests.support.env import vid
from tests.support.pg import app_dsn, lock_holder, out_of_order


def _hash(v: str) -> int:
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute("select hashtext('tm:' || %s)", (v,)).fetchone()
    assert row is not None
    return int(row[0])


def _vehicle_with_hash(negative: bool) -> str:
    while True:
        v = vid()
        if (_hash(v) < 0) is negative:
            return v


def test_lock_holder_finds_holder_for_negative_and_positive_hash() -> None:
    for negative in (True, False):
        v = _vehicle_with_hash(negative)
        assert lock_holder(v) is None
        with psycopg.connect(app_dsn("gs"), autocommit=True, application_name="holder-x") as c:
            c.execute("select pg_advisory_lock(hashtext('tm:' || %s))", (v,))
            assert lock_holder(v) == "holder-x"
        assert lock_holder(v) is None


def test_lock_holder_ignores_a_waiter() -> None:
    v = vid()
    with (
        psycopg.connect(app_dsn("gs"), autocommit=True, application_name="holder-x") as a,
        psycopg.connect(app_dsn("gs"), autocommit=True, application_name="waiter-x") as b,
    ):
        a.execute("select pg_advisory_lock(hashtext('tm:' || %s))", (v,))
        b.execute("set lock_timeout = '300ms'")
        try:
            b.execute("select pg_advisory_lock(hashtext('tm:' || %s))", (v,))
        except psycopg.errors.LockNotAvailable:
            pass
        assert lock_holder(v) == "holder-x"


def test_out_of_order_counts_seq_inversions_by_t_received() -> None:
    v = vid()
    base = datetime.now(UTC) - timedelta(minutes=5)
    # received order: 0, 1, 3, 2, 4 -> exactly one inversion (2 after 3)
    arrival = [0, 1, 3, 2, 4]
    with psycopg.connect(app_dsn("gs"), autocommit=True) as c:
        for i, seq in enumerate(arrival):
            c.execute(
                "insert into telemetry (t_onboard, vehicle, seq, t_received, mode)"
                " values (%s, %s, %s, %s, 'TEST')",
                (base + timedelta(milliseconds=100 * seq), v, seq, base + timedelta(seconds=i)),
            )
    assert out_of_order(v, base) == 1
    assert out_of_order(v, base + timedelta(hours=1)) == 0
