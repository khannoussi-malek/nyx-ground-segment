"""The OpenAPI document must describe the real responses (Task 16 generates TS types from it)."""

from typing import Any

import httpx

ERR = "#/components/schemas/ErrorBody"


def _schema(op: dict[str, Any], status: str) -> str:
    return str(op["responses"][status]["content"]["application/json"]["schema"]["$ref"])


async def test_error_statuses_are_error_body(api_client: httpx.AsyncClient) -> None:
    paths = (await api_client.get("/openapi.json")).json()["paths"]
    for path, method, codes in [
        ("/api/login", "post", ("401", "403", "422", "503")),
        ("/api/logout", "post", ("401", "403", "503")),
        ("/api/me", "get", ("401", "503")),
        ("/api/vehicles", "get", ("401", "503")),
        ("/api/mission-db", "get", ("401", "503")),
        ("/api/events", "get", ("401", "503")),
    ]:
        for code in codes:
            assert _schema(paths[path][method], code) == ERR, (path, code)
    assert "HTTPValidationError" not in str(paths)


async def test_healthz_and_events_success_responses(api_client: httpx.AsyncClient) -> None:
    paths = (await api_client.get("/openapi.json")).json()["paths"]
    health = "#/components/schemas/Health"
    assert _schema(paths["/healthz"]["get"], "200") == health
    assert _schema(paths["/healthz"]["get"], "503") == health
    assert "text/event-stream" in paths["/api/events"]["get"]["responses"]["200"]["content"]


async def test_mission_db_success_responses(api_client: httpx.AsyncClient) -> None:
    doc = (await api_client.get("/openapi.json")).json()
    paths, schemas = doc["paths"], doc["components"]["schemas"]
    assert _schema(paths["/api/vehicles"]["get"], "200") == "#/components/schemas/Vehicles"
    view = _schema(paths["/api/mission-db"]["get"], "200")
    assert view == "#/components/schemas/MissionDbView"
    # a check item is the union of the registered kinds, so the generated TS keeps red_low etc.
    assert {"LimitCheck", "RocCheck"} <= set(schemas)
