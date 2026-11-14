import json

import httpx
import pytest
from fastapi.routing import APIRoute
from itsdangerous import TimestampSigner

from gs.api.app import ROUTER
from tests.integration.conftest import FC1_PW, FC2_PW
from tests.support.http import CLIENT_HEADERS as H
from tests.support.http import login
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
    keys = list(redis_client("admin").keys("sess:*"))
    mine = [k for k in keys if redis_client("admin").get(k) == "fc1"]
    assert mine and 0 < int(redis_client("admin").ttl(mine[0])) <= 43200


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
    sid = str(next(iter(redis_client("admin").keys("sess:*")))).removeprefix("sess:")
    forged = TimestampSigner("other").sign(json.dumps({"sid": sid}).encode())
    # right sid but unsigned / signed with another secret / garbage: must not authenticate
    for bad in (sid, forged.decode(), "x.y.z"):
        api_client.cookies.clear()
        api_client.cookies.set("session", bad)
        assert (await api_client.get("/api/me")).status_code == 401


async def test_expired_session_in_redis_is_401(api_client: httpx.AsyncClient) -> None:
    await login(api_client, "fc1", FC1_PW)
    for k in redis_client("admin").keys("sess:*"):
        redis_client("admin").delete(k)
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


async def test_login_503_when_redis_unreachable(users_file: object) -> None:
    from gs.api.app import create_app
    from tests.integration.conftest import api_settings

    app = create_app(api_settings(users_file, redis_url="redis://127.0.0.1:1/0"))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            r = await login(c, "fc1", FC1_PW)
    assert r.status_code == 503 and r.json()["error"] == "UNAVAILABLE"
