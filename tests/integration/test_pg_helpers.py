"""tests/support/pg.py helpers that other tracks' tests rely on."""

import threading
from datetime import UTC, datetime, timedelta

import psycopg

from tests.support.env import vid, wait_until
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
    key = "select pg_advisory_lock(hashtext('tm:' || %s))"
    holder = psycopg.connect(app_dsn("gs"), autocommit=True, application_name="holder-x")
    waiter = psycopg.connect(app_dsn("gs"), autocommit=True, application_name="waiter-x")
    t = threading.Thread(target=lambda: waiter.execute(key, (v,)))
    holder.execute(key, (v,))
    t.start()  # before the try: a setup failure is not masked by joining an unstarted thread
    try:
        wait_until(lambda: _waiting("waiter-x"), timeout=5)  # granted = false in pg_locks
        assert lock_holder(v) == "holder-x"  # fails without the `granted` filter
    finally:
        holder.close()  # releases the lock; the waiter is granted and returns
        t.join(timeout=5)
        waiter.close()


def _waiting(app: str) -> bool:
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute(
            "select 1 from pg_locks l join pg_stat_activity a using (pid)"
            " where l.locktype='advisory' and not l.granted and a.application_name=%s",
            (app,),
        ).fetchone()
    return row is not None


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
