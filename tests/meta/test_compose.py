"""Static compose rules (D-ENG-03); they read `docker compose config --format json`."""

import json
import os
import re
import subprocess
from typing import Any

import pytest

from tests.support.compose import ONE_SHOTS, compose_config
from tests.support.env import REPO, dotenv

APP = {"migrate", "simulator", "ingest-1", "ingest-2", "api"}
MINIO_IMAGE = (
    "bitnamilegacy/minio:2025.5.24"
    "@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c"
)
INGEST_ENV = {
    "REDIS_URL",
    "DATABASE_URL",
    "CONSUMER_NAME",
    "VEHICLES",
    "MISSION_DB",
    "BIND_HOST",
    "HEALTH_PORT",
    "INGEST_HOMES",
    "STEAL_IDLE_MS",
    "RECLAIM_IDLE_MS",
    "GS_TEST_HOOKS",
    "CRASH_AT",
    "STOP_AT",
    "HANG_AT",
}
ENV = {
    "redis": {"REDIS_HEALTH_PW"},
    "db": {
        "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD", "GS_OWNER_PASSWORD", "GS_APP_PASSWORD",
    },
    "minio": {"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"},
    "minio-init": {
        "MC_CONFIG_DIR", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_ARCHIVER_SECRET", "S3_BUCKET",
    },
    "migrate": {"DATABASE_URL", "MISSION_DB"},
    "simulator": {
        "REDIS_URL", "SIM_VEHICLES", "SIM_SEED", "BIND_HOST", "HEALTH_PORT", "MISSION_DB",
    },
    "ingest-1": INGEST_ENV,
    "ingest-2": INGEST_ENV,
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


USES = {  # secret keys each service's environment is derived from
    "redis": {"REDIS_HEALTH_PW"},
    "db": {"POSTGRES_PASSWORD", "GS_OWNER_PASSWORD", "GS_APP_PASSWORD"},
    "minio": {"S3_ACCESS_KEY", "S3_SECRET_KEY"},
    "minio-init": {"S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_ARCHIVER_SECRET"},
    "migrate": {"GS_OWNER_PASSWORD"},
    "simulator": {"REDIS_SIM_PW"},
    "ingest-1": {"REDIS_INGEST_PW", "GS_APP_PASSWORD"},
    "ingest-2": {"REDIS_INGEST_PW", "GS_APP_PASSWORD"},
    "api": {"REDIS_API_PW", "GS_APP_PASSWORD", "SESSION_SECRET"},
}


def _tokens(svc: dict[str, Any]) -> set[str]:
    """Alphanumeric words of every environment value; a secret is held when it is one word."""
    return {w for v in svc.get("environment", {}).values() for w in re.split(r"\W+", str(v))}


def test_secrets_not_shared(services: dict[str, dict[str, Any]]) -> None:
    env = dotenv()
    secret_keys = {k for k in env if "_PW" in k or k.endswith(("_PASSWORD", "_SECRET", "_KEY"))}
    assert secret_keys
    for svc, keys in USES.items():
        missing = keys - env.keys()
        assert not missing, f"{svc} needs {missing}, absent from .env"
    for key in sorted(secret_keys):
        holders = {n for n, s in services.items() if env[key] in _tokens(s)}
        wanted = {n for n, keys in USES.items() if key in keys}
        assert holders == wanted, f"{key} held by {holders}, expected {wanted}"


@pytest.mark.parametrize(
    ("svc", "source"), [("redis", "users.acl"), ("api", "users.yaml")], ids=["acl", "users"]
)
def test_generated_file_mounts_never_create_directories(
    services: dict[str, dict[str, Any]], svc: str, source: str
) -> None:
    (mount,) = [v for v in services[svc]["volumes"] if v["source"].endswith(source)]
    assert mount["bind"].get("create_host_path", False) is False  # compose omits false


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


def test_ingest_defaults(services: dict[str, dict[str, Any]]) -> None:
    """Design defaults when the shell sets nothing."""
    for svc in ("ingest-1", "ingest-2"):
        env = services[svc]["environment"]
        assert (env["STEAL_IDLE_MS"], env["RECLAIM_IDLE_MS"], env["GS_TEST_HOOKS"]) == (
            "30000",
            "30000",
            "0",
        )
        assert env["INGEST_HOMES"] == "ingest-1=NYX-TA1,ingest-2=NYX-TA2"
        assert env["CONSUMER_NAME"] == svc


def test_both_ingest_workers_run_by_default(services: dict[str, dict[str, Any]]) -> None:
    """G1/G5: with the ownership lock both workers start with `make up`."""
    assert "profiles" not in services["ingest-2"] and "profiles" not in services["ingest-1"]


def test_image_tag_is_per_worktree() -> None:
    """G3: parallel worktrees must not share one nyx-gs:local tag."""
    assert "nyx-gs:${GS_IMAGE_TAG:-local}" in (REPO / "docker-compose.yml").read_text()
    assert "GS_IMAGE_TAG=local" in (REPO / ".env.example").read_text().splitlines()
    out = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO,
        env={**os.environ, "GS_IMAGE_TAG": "x"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    svcs = json.loads(out)["services"]
    assert {n: svcs[n]["image"] for n in APP} == {n: "nyx-gs:x" for n in APP}
