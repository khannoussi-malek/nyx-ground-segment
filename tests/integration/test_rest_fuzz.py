"""Schemathesis (D-EXT-18, R17 F2): fuzz the ASGI app against its own OpenAPI document.
Part of the CI `test` job; alone: `uv run pytest tests/integration/test_rest_fuzz.py -q -s`."""

import collections
import dataclasses
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import schemathesis
import schemathesis.python.asgi
import yaml
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_schema_conformance,
    status_code_conformance,
)

from gs.api.app import create_app
from gs.api.auth import hash_password
from tests.support.env import vid
from tests.support.http import CLIENT_HEADERS, FC1_PW, api_settings, login

FUZZ_USER, FUZZ_PW = "fuzz", "fuzz-test-password"  # never fc1/fc2: the login limit (P-pf8, P-pf18)
_users = Path(tempfile.mkdtemp()) / "users.yaml"
_users.write_text(yaml.safe_dump({"users": {FUZZ_USER: hash_password(FUZZ_PW)}}))
app = create_app(dataclasses.replace(api_settings(_users), vehicles=(vid(),)))
# the app's lifespan is run by schemathesis itself (once per process)
schema: Any = schemathesis.openapi.from_asgi("/openapi.json", app)  # Any: stubs are partial
# POST /api/commands would reach a real vehicle path; only a vid() target is allowed on the
# test stack, and the fuzzer cannot promise that, so the check leaves the operation out
schema = schema.exclude(method="POST", path="/api/commands")
# Task 11's 501 route stubs: the track that implements one removes it here (Track RP Task 13);
# test_stubs_still_unimplemented fails once a stub stops answering 501
STUBS = {
    ("POST", "/api/replays"): {
        "vehicle": "NYX-TA1", "from": "2026-11-15T10:00:00Z", "to": "2026-11-15T11:00:00Z",
        "speed": 1,
    },
    ("DELETE", "/api/replays/{replay_id}"): None,
    ("GET", "/api/replays/{replay_id}/events"): None,
}  # fmt: skip
for _method, _path in STUBS:
    schema = schema.exclude(method=_method, path=_path)


@schema.auth()
class Session:
    def get(self, case: Any, ctx: Any) -> str:
        client = schemathesis.python.asgi.get_client(app)  # type: ignore[arg-type]
        r = client.post(
            "/api/login", json={"user": FUZZ_USER, "password": FUZZ_PW}, headers=CLIENT_HEADERS
        )
        assert r.status_code == 204, r.text
        return str(client.cookies["session"])

    def set(self, case: Any, data: str, ctx: Any) -> None:
        case.cookies = {**(case.cookies or {}), "session": data}
        case.headers = {**(case.headers or {}), **CLIENT_HEADERS}


CASES: collections.Counter[str] = collections.Counter()


@pytest.fixture(scope="module", autouse=True)
def _case_counts() -> Iterator[None]:
    yield
    for op, n in sorted(CASES.items()):  # shown with -s: generated cases per operation
        print(f"cases {n:4d}  {op}")


@schema.parametrize()  # type: ignore[untyped-decorator]
def test_schemathesis_asgi(case: Any) -> None:
    CASES[f"{case.method} {case.operation.path}"] += 1
    case.call_and_validate(
        checks=[
            not_a_server_error,
            response_schema_conformance,
            status_code_conformance,
            content_type_conformance,
        ]
    )


async def test_stubs_still_unimplemented(api_client: httpx.AsyncClient) -> None:
    assert (await login(api_client, "fc1", FC1_PW)).status_code == 204
    for (method, path), body in STUBS.items():
        url = path.format(session_id=1, replay_id=uuid.uuid4())
        got = await api_client.request(method, url, json=body, headers=CLIENT_HEADERS)
        assert got.status_code == 501, f"{method} {path} is implemented: drop it from STUBS"
