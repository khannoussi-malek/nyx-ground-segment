from typing import Any

from tests.support.env import vid
from tests.support.redisx import client, timed_serial_xadds


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
    lat = sorted(timed_serial_xadds(client("sim"), f"tm:{vid()}", n=1000, payload="x" * 300))
    p50, p99 = lat[499], lat[989]
    print(f"XADD p50={p50 * 1e3:.3f}ms p99={p99 * 1e3:.3f}ms")
    assert p99 < 0.010  # one 100 Hz tick
