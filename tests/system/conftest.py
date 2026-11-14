"""System tests need the full stack; `make up` first."""

import urllib.request

import pytest

from tests.support.compose import api_url


@pytest.fixture(scope="session", autouse=True)
def stack_is_up() -> None:
    try:
        ok = urllib.request.urlopen(f"{api_url()}/healthz", timeout=3).status == 200
    except OSError:
        ok = False
    if not ok:
        pytest.fail("stack is not up: run make up", pytrace=False)
