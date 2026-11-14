"""D-ING-06, §3.6: XADD ... ACKED MAXLEN ~ n never trims an entry a group still holds pending."""

from typing import Any

from gs.simulator.main import xadd_tm
from tests.support.env import vid
from tests.support.redisx import GROUP, aclient, client, frame, read_without_ack


def _pending_ids(s: str) -> list[str]:
    rows: Any = client("admin").xpending_range(s, GROUP, "-", "+", 10_000)
    return [r["message_id"] for r in rows]


def _ack_all(v: str, s: str) -> None:
    while read_without_ack(v, "c"):  # deliver whatever is still undelivered
        pass
    ids = _pending_ids(s)
    client("admin").xack(s, GROUP, *ids)


async def test_trim_keeps_unacked() -> None:
    v = vid()
    s = f"tm:{v}"
    r = aclient("sim", decode=False)
    admin: Any = client("admin")
    admin.xgroup_create(s, GROUP, id="0", mkstream=True)
    try:
        await xadd_tm(r, s, frame(v, 0), maxlen=10)
        read_without_ack(v, "c")  # entry 0 pending
        for i in range(1, 200):
            await xadd_tm(r, s, frame(v, i), maxlen=10)
        assert admin.xrange(s, count=1)[0][0] == _pending_ids(s)[0]  # the un-acked entry survived
        _ack_all(v, s)
        await xadd_tm(r, s, frame(v, 200), maxlen=10)
        assert admin.xlen(s) <= 120
    finally:
        await r.aclose()
        admin.delete(s)
