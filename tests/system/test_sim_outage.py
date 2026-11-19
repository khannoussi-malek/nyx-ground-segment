"""D-SIM-05 on the real container: a Redis outage fills the simulator buffer, which then
drains in order. Proven: the buffer holds ~every frame generated during the outage (2 vehicles x
10 Hz x the measured outage seconds, minus 40 slack); per vehicle the first drained seq is the
last pre-outage seq + 1 (no gap at the outage start) and the sequence is
contiguous (no gap or reorder; a back-to-back repeat from the at-least-once send is allowed;
redis-py's own retry must be off)."""

import json
import time
import urllib.request
from typing import Any

from tests.support.compose import compose, wait_healthy
from tests.support.env import dotenv
from tests.support.redisx import client

OUTAGE_S, DRAIN_S, SLACK = 6.0, 10.0, 40  # slack: 2 s of frames at 2 x 10 Hz


def _buffered() -> int:
    url = f"http://127.0.0.1:{dotenv()['SIM_HOST_PORT']}/healthz"
    body = json.loads(urllib.request.urlopen(url, timeout=3).read())
    return int(body["checks"]["buffered"])


def _seqs(v: str, after: str) -> list[int]:
    entries: Any = client("admin").xrange(f"tm:{v}", min=f"({after}")
    return [json.loads(f["data"])["seq"] for _, f in entries]


def test_outage_buffers_then_flushes_in_order() -> None:
    r = client("admin")
    tails: Any = {v: r.xrevrange(f"tm:{v}", count=1) for v in ("NYX-TA1", "NYX-TA2")}
    last: dict[str, str] = {v: e[0][0] for v, e in tails.items()}
    tail_seq: dict[str, int] = {v: json.loads(e[0][1]["data"])["seq"] for v, e in tails.items()}
    t_down = time.monotonic()  # before `compose stop`: overstates the outage, never understates
    compose("stop", "redis")
    try:
        time.sleep(OUTAGE_S)
        outage_s = time.monotonic() - t_down  # before the request, so a slow host can't inflate it
        grown = _buffered()
    finally:
        t = time.monotonic()
        compose("start", "redis")
    while _buffered() > 5:
        assert time.monotonic() - t < DRAIN_S, "buffer did not drain after Redis came back"
        time.sleep(0.2)
    print(f"buffered {grown} after {OUTAGE_S}s outage; drained {time.monotonic() - t:.1f}s")
    expected = 2 * 10 * outage_s
    assert grown >= expected - SLACK, f"buffered {grown}, expected about {expected:.0f}"
    for v, after in last.items():
        raw = _seqs(v, after)
        # at-least-once (D-SIM-05, G30): a timed-out XADD that landed is re-sent, so the stream
        # may hold a frame twice in a row; ingest's idempotent key stores it once
        seqs = [s for i, s in enumerate(raw) if i == 0 or s != raw[i - 1]]
        assert seqs, f"{v}: nothing drained"
        assert seqs[0] == tail_seq[v] + 1, f"{v}: gap at the outage start"
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), f"{v}: gap or reorder"
        assert len(seqs) >= grown // 2
    for svc in ("simulator", "ingest-1", "api"):
        wait_healthy(svc)
