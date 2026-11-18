from typing import Any

from tests.support.env import vid
from tests.support.redisx import client, timed_serial_xadds, xadd_bound_met


def test_durability_config() -> None:  # D-ING-07
    admin = client("admin")
    keys = ("appendonly", "appendfsync", "maxmemory", "maxmemory-policy")
    got: Any = {k: admin.config_get(k) for k in keys}
    cfg = {k: got[k][k] for k in keys}
    assert cfg == {
        "appendonly": "yes",
        "appendfsync": "always",
        "maxmemory": str(512 * 1024**2),
        "maxmemory-policy": "noeviction",
    }


def test_xadd_cost_appendfsync_always() -> None:  # S4: p99 0.56-0.68 ms
    # D-ING-07: up to 3 batches of 1 000; stop at the first one with p99 < 10 ms (one 100 Hz tick)
    batches: list[list[float]] = []
    for _ in range(3):
        lat = timed_serial_xadds(client("sim"), f"tm:{vid()}", n=1000, payload="x" * 300)
        s = sorted(lat)
        print(f"XADD p50={s[499] * 1e3:.3f}ms p99={s[989] * 1e3:.3f}ms")
        batches.append(lat)
        if xadd_bound_met([lat]):
            break
    assert xadd_bound_met(batches)
