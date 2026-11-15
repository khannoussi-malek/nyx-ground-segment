"""Shared helpers for tests that talk to MinIO through its own `mc` binary."""

import json
import subprocess
from typing import Literal

from minio import Minio

from tests.support.env import REPO, dotenv

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
        ["docker", "compose", "ps", "-q", service],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout.strip()
    out = subprocess.run(
        ["docker", "inspect", cid, "--format", "{{json .State.Health.Status}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    status: str = json.loads(out)
    return status


def s3_client(user: Literal["root", "archiver", "replay"] = "archiver") -> Minio:
    """A MinIO client on the host port, as root or as a least-privilege user (archiver, replay)."""
    env = dotenv()
    access, secret = {
        "root": (env["S3_ACCESS_KEY"], env["S3_SECRET_KEY"]),
        "archiver": ("archiver", env["S3_ARCHIVER_SECRET"]),
        "replay": ("replay", env["S3_REPLAY_SECRET"]),
    }[user]
    # a fixed region spares the client the GetBucketLocation call the archiver policy does not grant
    return Minio(
        f"127.0.0.1:{env['MINIO_HOST_PORT']}", access, secret, secure=False, region="us-east-1"
    )
