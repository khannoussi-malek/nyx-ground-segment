"""System tests need the full stack; `make up` first."""

import urllib.request
from collections.abc import Iterator

import pytest

from tests.support.compose import api_url, compose_ps, recreate, stack_problems


@pytest.fixture(scope="session", autouse=True)
def stack_is_up() -> None:
    try:
        ok = urllib.request.urlopen(f"{api_url()}/healthz", timeout=3).status == 200
    except OSError:
        ok = False
    problems = stack_problems(ok, compose_ps())
    if problems:  # e.g. a coverage run stopped the simulator: fail here, not with ECONNREFUSED
        pytest.fail(
            f"stack not fully up: run make up (not up: {', '.join(problems)})", pytrace=False
        )


@pytest.fixture(scope="module")
def fast_ingest() -> Iterator[None]:
    """Both workers with test hooks on and 3 s steal/reclaim thresholds (CI values)."""
    both = ["ingest-1", "ingest-2"]
    recreate(both, {"GS_TEST_HOOKS": "1", "STEAL_IDLE_MS": "3000", "RECLAIM_IDLE_MS": "3000"})
    yield
    recreate(both)  # defaults from compose
