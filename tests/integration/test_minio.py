import subprocess
import urllib.request

from tests.support.env import dotenv
from tests.support.s3 import inspect_health, mc


def test_minio_healthy_via_curl_in_image() -> None:  # S1: curl is in the image
    assert inspect_health("minio") == "healthy"


def test_published_on_env_port() -> None:
    url = f"http://127.0.0.1:{dotenv()['MINIO_HOST_PORT']}/minio/health/live"
    assert urllib.request.urlopen(url, timeout=5).status == 200


def test_bucket_created() -> None:
    assert "archive/" in mc("mc ls s3").stdout


def test_missing_bucket_is_an_error() -> None:  # negative control for the probe above
    assert mc("mc ls s3/no-such-bucket").returncode != 0


def test_minio_init_is_idempotent() -> None:
    assert subprocess.run(["docker", "compose", "run", "--rm", "minio-init"]).returncode == 0
