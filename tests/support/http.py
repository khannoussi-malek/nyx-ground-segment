"""API test helpers: a client that carries its app, and the login call."""

import httpx

CLIENT_HEADERS = {"X-GS-Client": "console"}


async def login(client: httpx.AsyncClient, user: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/login", json={"user": user, "password": password}, headers=CLIENT_HEADERS
    )
