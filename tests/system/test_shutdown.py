"""R2-07: init: true plus a SIGTERM handler stops each app service inside the 5 s grace period."""

import time

import pytest

from tests.support.compose import compose, container_exit_code, recreate, wait_healthy


@pytest.mark.parametrize("svc", ["simulator", "ingest-1", "ingest-2", "api"])
def test_stop_within_grace(svc: str) -> None:
    if svc == "ingest-2":
        recreate([svc])  # opt-in service (G1): may not be running
    t = time.monotonic()
    compose("stop", svc)
    took = time.monotonic() - t
    print(f"{svc} stopped in {took:.2f} s")
    try:
        assert took < 5.0
        assert container_exit_code(svc) == 0
    finally:
        if svc != "ingest-2":  # it stays stopped (G1)
            compose("start", svc)
            wait_healthy(svc)
