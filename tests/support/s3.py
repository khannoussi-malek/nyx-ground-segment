"""Shared helpers for tests that talk to MinIO through its own `mc` binary."""

import json
import subprocess

_MC = (
    "export MC_CONFIG_DIR=/tmp/mc; "
    'mc alias set s3 http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && '
)


def mc(cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "minio", "bash", "-c", _MC + cmd],
        capture_output=True,
        text=True,
    )


def inspect_health(service: str) -> str:
    cid = subprocess.run(
        ["docker", "compose", "ps", "-q", service], capture_output=True, text=True, check=True
    ).stdout.strip()
    out = subprocess.run(
        ["docker", "inspect", cid, "--format", "{{json .State.Health.Status}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    status: str = json.loads(out)
    return status
