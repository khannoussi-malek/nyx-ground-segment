"""EX-25: the mission database reaches services read-only."""

from typing import Any

import pytest

from tests.support.compose import compose_config


@pytest.mark.parametrize("svc", ["api", "ingest-1", "migrate"])
def test_mission_mounted_read_only(svc: str) -> None:
    s: dict[str, Any] = compose_config()["services"][svc]
    mounts = [v for v in s["volumes"] if v["target"] == "/mission"]
    assert mounts and all(v["type"] == "bind" and v.get("read_only") is True for v in mounts)
