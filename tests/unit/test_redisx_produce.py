"""G14: produce() stamps frame times from one base per call, so XADD latency can't skew them."""

import json
import time

import pytest

from tests.support import redisx


def test_produce_spaces_t_onboard_exactly_100ms_despite_slow_xadd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    def slow_xadd(v: str, raw: str | bytes) -> str:
        time.sleep(0.02)  # 20 ms per XADD: 5 frames would drift 100 ms with per-frame now()
        sent.append(str(raw))
        return "0-0"

    monkeypatch.setattr(redisx, "xadd_raw", slow_xadd)
    redisx.produce("NYX-TA1", 5)
    ts = [json.loads(raw)["t_onboard"] for raw in sent]
    from datetime import datetime

    secs = [datetime.fromisoformat(t).timestamp() for t in ts]
    assert [round(b - a, 6) for a, b in zip(secs, secs[1:], strict=False)] == [0.1] * 4
