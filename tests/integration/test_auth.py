import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest
import yaml
from fastapi.routing import APIRoute
from itsdangerous import TimestampSigner

from gs.api import auth
from gs.api.app import ROUTER, create_app
from gs.api.auth import hash_password
from tests.support.env import vid
from tests.support.http import CLIENT_HEADERS as H
from tests.support.http import FC1_PW, FC2_PW, api_settings, login, session_sid
from tests.support.pg import audit_rows
from tests.support.redisx import client as redis_client


async def test_login_me_logout(api_client: httpx.AsyncClient) -> None:
    r = await login(api_client, "fc1", FC1_PW)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "samesite=strict" in cookie and "max-age=43200" in cookie and "httponly" in cookie
    assert (await api_client.get("/api/me")).json() == {"user": "fc1"}
    assert (await api_client.post("/api/logout", headers=H)).status_code == 204
    assert (await api_client.get("/api/me")).status_code == 401


async def test_identity_follows_the_session_user(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc2", FC2_PW)
    assert (await api_client.get("/api/me")).json() == {"user": "fc2"}


async def test_session_lives_in_redis_with_ttl(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    admin, key = redis_client("admin"), f"sess:{session_sid(api_client)}"
    assert admin.get(key) == "fc1"
    assert 43000 < int(admin.ttl(key)) <= 43200


async def test_relogin_revokes_the_previous_session(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    old = f"sess:{session_sid(api_client)}"
    await login(api_client, "fc2", FC2_PW)
    admin = redis_client("admin")
    assert admin.get(old) is None
    assert admin.get(f"sess:{session_sid(api_client)}") == "fc2"


async def test_logout_revokes_replayed_cookie(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    saved = api_client.cookies.get("session")
    assert saved
    await api_client.post("/api/logout", headers=H)
    api_client.cookies.set("session", saved)  # the browser kept the old cookie
    replay = await api_client.get("/api/me")
    assert replay.status_code == 401 and replay.json()["error"] == "UNAUTHENTICATED"


async def test_forged_cookie_is_rejected(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    sid = session_sid(api_client)
    forged = TimestampSigner("other").sign(json.dumps({"sid": sid}).encode())
    # right sid but unsigned / signed with another secret / garbage: must not authenticate
    for bad in (sid, forged.decode(), "x.y.z"):
        api_client.cookies.clear()
        api_client.cookies.set("session", bad)
        assert (await api_client.get("/api/me")).status_code == 401


async def test_expired_session_in_redis_is_401(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    redis_client("admin").delete(f"sess:{session_sid(api_client)}")
    assert (await api_client.get("/api/me")).status_code == 401


@pytest.mark.parametrize(("user", "pw"), [("fc1", "wrong"), ("nobody", "x")])
async def test_login_failed(api_client: httpx.AsyncClient, user: str, pw: str) -> None:
    r = await login(api_client, user, pw)
    assert r.status_code == 401 and r.json()["error"] == "LOGIN_FAILED"
    assert (await api_client.get("/api/me")).status_code == 401


async def test_every_route_requires_session(
    api_client: httpx.AsyncClient,
) -> None:  # walks the route table
    checked = 0
    for route in ROUTER.routes:
        if (
            isinstance(route, APIRoute)
            and route.path.startswith("/api/")
            and route.path != "/api/login"
        ):
            for m in route.methods or ():
                r = await api_client.request(m, route.path, headers=H)
                assert r.status_code == 401, route.path
                checked += 1
    assert checked >= 2


async def test_writes_need_client_header(api_client: httpx.AsyncClient) -> None:
    body = {"user": "fc1", "password": FC1_PW}
    r = await api_client.post("/api/login", json=body)
    assert r.status_code == 403 and r.json()["error"] == "CLIENT_HEADER_REQUIRED"
    wrong = await api_client.post("/api/login", json=body, headers={"X-GS-Client": "curl"})
    assert wrong.status_code == 403
    await login(api_client, "fc1", FC1_PW)
    assert (await api_client.post("/api/logout")).status_code == 403
    assert (await api_client.get("/api/me")).status_code == 200  # still logged in, GET needs none


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": "user=fc1&password=x", "headers": {"content-type": "text/plain", **H}},
        {"data": {"user": "fc1", "password": "x"}, "headers": H},  # form-encoded
        {"json": {"user": "fc1"}, "headers": H},
        {"json": {"user": "fc1", "password": "x", "extra": 1}, "headers": H},
    ],
)
async def test_writes_are_json_only(
    api_client: httpx.AsyncClient, kwargs: dict[str, object]
) -> None:
    r = await api_client.post("/api/login", **kwargs)  # type: ignore[arg-type]
    assert r.status_code == 422 and r.json()["error"] == "VALIDATION_ERROR"


async def test_unknown_route_uses_error_body(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"] == "NOT_FOUND"


async def test_login_503_when_redis_unreachable(users_file: Path) -> None:
    from gs.api.app import create_app

    app = create_app(api_settings(users_file, redis_url="redis://127.0.0.1:1/0"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            r = await login(c, "fc1", FC1_PW)
    assert r.status_code == 503 and r.json()["error"] == "UNAVAILABLE"


async def test_redis_failure_is_logged(users_file: Path, caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(api_settings(users_file, redis_url="redis://127.0.0.1:1/0"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://a") as c:
            with caplog.at_level(logging.WARNING, logger="gs.api"):
                await login(c, "fc1", FC1_PW)
    assert any("ConnectionError" in r.getMessage() for r in caplog.records)


async def test_ui_served_without_session(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<title>Nyx</title>" in r.text


async def test_ui_mount_does_not_shadow_api(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/me")).status_code == 401  # the router, not index.html
    assert (await api_client.get("/healthz")).headers["content-type"] == "application/json"


async def test_405_keeps_the_allow_header(api_client: httpx.AsyncClient) -> None:  # RFC 9110
    r = await api_client.delete("/api/me", headers=H)
    assert r.status_code == 405 and r.json()["error"] == "METHOD_NOT_ALLOWED"
    assert "GET" in r.headers["allow"]


async def test_body_not_utf8_is_400(api_client: httpx.AsyncClient) -> None:  # R19
    r = await api_client.post(
        "/api/login", content=b"\xff\xfe{", headers={"content-type": "application/json", **H}
    )
    assert r.status_code == 400 and r.json()["error"] == "BAD_REQUEST"


# Task 20: rate limit + audit. P-pf8: per-test users t-<vid> in a test-local users file.
PW = "t-secret"


@pytest.fixture
async def lone(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, str, str]]:
    """(client, user a, user b) with two fresh users that no other test shares."""
    a, b = f"t-{vid()}", f"t-{vid()}"
    path = tmp_path / "users.yaml"
    path.write_text(yaml.safe_dump({"users": {a: hash_password(PW), b: hash_password(PW)}}))
    app = create_app(api_settings(path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
            yield c, a, b


async def test_login_rate_limited_after_5_failures(
    lone: tuple[httpx.AsyncClient, str, str],
) -> None:
    c, a, b = lone
    for _ in range(5):
        assert (await login(c, a, "wrong")).status_code == 401
    r = await login(c, a, PW)  # the right password is refused too
    assert (r.status_code, r.json()["error"]) == (429, "LOGIN_RATE_LIMITED")
    assert (await login(c, b, PW)).status_code == 204  # per user


async def test_window_passes(
    lone: tuple[httpx.AsyncClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, a, _ = lone
    monkeypatch.setattr(auth, "LOGIN_WINDOW_S", 5)  # 5 scrypt logins fit with room to spare
    for _ in range(5):
        await login(c, a, "wrong")
    assert (await login(c, a, PW)).status_code == 429
    await asyncio.sleep(5.3)
    assert (await login(c, a, PW)).status_code == 204


async def test_lockout_ends_while_hammering_429_path(
    lone: tuple[httpx.AsyncClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if the rate_limited filter is dropped: refused attempts must not extend the lock."""
    c, a, _ = lone
    monkeypatch.setattr(auth, "LOGIN_WINDOW_S", 3)
    for _ in range(5):
        await login(c, a, "wrong")
    codes: list[int] = []
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and (not codes or codes[-1] == 429):
        await asyncio.sleep(0.4)
        codes.append((await login(c, a, PW)).status_code)
    assert codes[0] == 429 and codes[-1] == 204, codes


async def test_concurrent_failures_stop_at_the_limit(
    lone: tuple[httpx.AsyncClient, str, str],
) -> None:
    c, a, _ = lone
    got = await asyncio.gather(*(login(c, a, "wrong") for _ in range(8)))
    assert sorted(r.status_code for r in got) == [401] * 5 + [429] * 3
    counted = [r for r in audit_rows(action="LOGIN_FAILED", actor=a) if not r["details"]]
    assert len(counted) == 5


async def test_login_audit_reasons(lone: tuple[httpx.AsyncClient, str, str]) -> None:
    c, a, _ = lone
    for _ in range(6):
        await login(c, a, "wrong")
    await login(c, "nobody-" + vid(), "x")
    reasons = [r["reason"] for r in audit_rows(action="LOGIN_FAILED", actor=a)]
    assert reasons == ["bad credentials"] * 5 + ["rate limited"]


async def test_nul_or_huge_user_is_422_not_503(lone: tuple[httpx.AsyncClient, str, str]) -> None:
    c, _, _ = lone
    for bad in ("a\x00b", "u" * 300):
        r = await login(c, bad, "x")
        assert (r.status_code, r.json()["error"]) == (422, "VALIDATION_ERROR")


async def test_no_session_without_audit_row(
    lone: tuple[httpx.AsyncClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, a, _ = lone

    async def boom(*_: object, **__: object) -> None:
        raise psycopg.OperationalError("audit write failed")

    monkeypatch.setattr(auth, "audit", boom)
    assert (await login(c, a, PW)).status_code == 503
    assert (await c.get("/api/me")).status_code == 401  # no session was created


async def test_unknown_user_still_runs_a_password_hash(
    lone: tuple[httpx.AsyncClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, _ = lone
    calls: list[str] = []
    real = auth.verify_password

    def spy(p: str, h: str) -> bool:
        calls.append(h)
        return real(p, h)

    monkeypatch.setattr(auth, "verify_password", spy)
    await login(c, "nobody-" + vid(), "x")
    assert len(calls) == 1


async def test_login_logout_audited(lone: tuple[httpx.AsyncClient, str, str]) -> None:
    c, a, _ = lone
    await login(c, a, "wrong")
    await login(c, a, PW)
    await c.post("/api/logout", headers=H)
    rows = audit_rows(actor=a)
    assert [r["action"] for r in rows] == ["LOGIN_FAILED", "LOGIN", "LOGOUT"]
    assert all(r["at"] is not None for r in rows)


async def test_rate_limited_attempts_audited_not_counted(
    lone: tuple[httpx.AsyncClient, str, str],
) -> None:
    c, a, _ = lone
    for _ in range(10):
        await login(c, a, "wrong")
    rows = audit_rows(action="LOGIN_FAILED", actor=a)
    assert len(rows) == 10
    assert sum(r["details"].get("rate_limited") is True for r in rows) == 5


async def test_unknown_user_failure_audited_truncated(
    lone: tuple[httpx.AsyncClient, str, str],
) -> None:
    c, _, _ = lone
    name = vid() + "x" * 100
    assert (await login(c, name, "x")).status_code == 401
    assert len(audit_rows(action="LOGIN_FAILED", actor=name[:64])) == 1


async def test_login_503_when_db_unreachable(users_file: Path) -> None:
    app = create_app(api_settings(users_file, database_url="postgresql://x@127.0.0.1:1/x"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://a") as c:
            r = await login(c, "fc1", FC1_PW)
    assert r.status_code == 503 and r.json()["error"] == "UNAVAILABLE"
    assert "database" in r.json()["message"]
