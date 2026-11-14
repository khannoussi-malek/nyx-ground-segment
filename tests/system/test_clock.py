"""S5: the Docker VM clock (Redis runs inside it) matches the host clock."""

import statistics

from tests.support.redisx import client, redis_time_offset


def test_vm_clock_matches_host() -> None:
    r = client("admin")
    offs = [redis_time_offset(r) for _ in range(20)]
    median = statistics.median(offs)
    print(f"median offset {median * 1e3:.3f} ms")
    assert abs(median) < 0.005
