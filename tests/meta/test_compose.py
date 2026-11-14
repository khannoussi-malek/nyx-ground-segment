"""Static compose rules (D-ENG-03); they read `docker compose config --format json`."""

from typing import Any

import pytest

from tests.support.compose import ONE_SHOTS, compose_config
from tests.support.env import dotenv

APP = {"migrate", "simulator", "ingest-1", "api"}
MINIO_IMAGE = (
    "bitnamilegacy/minio:2025.5.24"
    "@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c"
)
ENV = {
    "redis": {"REDIS_HEALTH_PW"},
    "db": {
        "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD", "GS_OWNER_PASSWORD", "GS_APP_PASSWORD",
    },
    "minio": {"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"},
    "minio-init": {"MC_CONFIG_DIR", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"},
    "migrate": {"DATABASE_URL", "MISSION_DB"},
    "simulator": {"REDIS_URL", "SIM_VEHICLES", "SIM_SEED", "BIND_HOST", "HEALTH_PORT"},
    "ingest-1": {
        "REDIS_URL", "DATABASE_URL", "CONSUMER_NAME", "VEHICLES", "MISSION_DB",
        "BIND_HOST", "HEALTH_PORT",
    },
    "api": {
        "REDIS_URL", "DATABASE_URL", "SESSION_SECRET", "USERS_FILE", "MISSION_DB", "VEHICLES",
        "UI_DIST", "BIND_HOST", "API_PORT",
    },
}  # fmt: skip


@pytest.fixture(scope="module")
def services() -> dict[str, dict[str, Any]]:
    svcs: dict[str, dict[str, Any]] = compose_config()["services"]
    return svcs


def test_ports_bound_to_localhost(services: dict[str, dict[str, Any]]) -> None:
    ports = [p for s in services.values() for p in s.get("ports", [])]
    assert ports
    assert all(p.get("host_ip") == "127.0.0.1" for p in ports)


def test_app_services_init_grace_restart(services: dict[str, dict[str, Any]]) -> None:
    for name in APP:
        s = services[name]
        want = "no" if name == "migrate" else "unless-stopped"
        assert (s["init"], s["stop_grace_period"], s["restart"]) == (True, "5s", want), name


def test_per_service_environment(services: dict[str, dict[str, Any]]) -> None:
    assert not [n for n, s in services.items() if "env_file" in s]
    assert {n: set(s.get("environment", {})) for n, s in services.items()} == ENV


def test_secrets_not_shared(services: dict[str, dict[str, Any]]) -> None:
    secret_keys = [k for k in dotenv() if k.endswith(("_PW", "_PASSWORD", "_SECRET", "_KEY"))]
    assert secret_keys
    for key in secret_keys:
        value = dotenv()[key]
        holders = {n for n, s in services.items() if value in str(s.get("environment", {}))}
        wanted = {
            n
            for n, s in services.items()
            if value in str(s.get("environment", {})) and _needs(n, key)
        }
        assert holders == wanted, f"{key} reaches {holders - wanted}"


def _needs(svc: str, key: str) -> bool:
    """A service may hold secret `key` when its environment entries are derived from it."""
    uses = {
        "redis": {"REDIS_HEALTH_PW"},
        "db": {"POSTGRES_PASSWORD", "GS_OWNER_PASSWORD", "GS_APP_PASSWORD"},
        "minio": {"S3_ACCESS_KEY", "S3_SECRET_KEY"},
        "minio-init": {"S3_ACCESS_KEY", "S3_SECRET_KEY"},
        "migrate": {"GS_OWNER_PASSWORD"},
        "simulator": {"REDIS_SIM_PW"},
        "ingest-1": {"REDIS_INGEST_PW", "GS_APP_PASSWORD"},
        "api": {"REDIS_API_PW", "GS_APP_PASSWORD", "SESSION_SECRET"},
    }
    return key in uses[svc]


@pytest.mark.parametrize("one_shot", ONE_SHOTS)
def test_one_shots_have_completed_dependent(
    services: dict[str, dict[str, Any]], one_shot: str
) -> None:
    dependents = [
        n
        for n, s in services.items()
        if s.get("depends_on", {}).get(one_shot, {}).get("condition")
        == "service_completed_successfully"
        and s.get("restart") == "unless-stopped"
    ]
    assert dependents, f"{one_shot} has no long-running service_completed_successfully dependent"


def test_db_healthcheck_tcp_sentinel(services: dict[str, dict[str, Any]]) -> None:
    test = " ".join(services["db"]["healthcheck"]["test"])
    assert "pg_isready -h 127.0.0.1" in test
    assert "select 1 from audit_log limit 0" in test


def test_images_pinned(services: dict[str, dict[str, Any]]) -> None:
    assert services["redis"]["image"] == "redis:8.10.3"
    assert services["db"]["image"] == "timescale/timescaledb:2.32.0-pg17"
    assert services["minio"]["image"] == services["minio-init"]["image"] == MINIO_IMAGE
