"""tests/support/redisx.py helpers."""

import pytest
import redis

from tests.support.env import vid
from tests.support.redisx import client, pending_of, produce, read_without_ack


def test_pending_of_counts_only_that_consumers_entries() -> None:
    v = vid()
    assert pending_of(v, "c1") == 0  # no stream, no group: NOGROUP means 0
    produce(v, 3)
    read_without_ack(v, "c1")
    assert (pending_of(v, "c1"), pending_of(v, "c2")) == (3, 0)


def test_pending_of_reraises_other_errors() -> None:
    v = vid()
    client("admin").set(f"tm:{v}", "x")  # a string, not a stream -> WRONGTYPE, not NOGROUP
    try:
        with pytest.raises(redis.ResponseError, match="WRONGTYPE"):
            pending_of(v, "c1")
    finally:
        client("admin").delete(f"tm:{v}")
