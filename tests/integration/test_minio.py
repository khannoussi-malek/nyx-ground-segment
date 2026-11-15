import io
import subprocess
import urllib.request

import pytest
from minio.error import S3Error

from tests.support.env import dotenv, vid
from tests.support.s3 import inspect_health, mc, s3_client


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


def test_archiver_can_put_and_get() -> None:
    c = s3_client("archiver")
    key = f"t/{vid()}.bin"
    c.put_object("archive", key, io.BytesIO(b"x"), 1)
    try:
        assert c.get_object("archive", key).read() == b"x"
    finally:
        s3_client("root").remove_object("archive", key)


def test_archiver_cannot_delete() -> None:
    root, c = s3_client("root"), s3_client("archiver")
    key = f"t/{vid()}.bin"
    c.put_object("archive", key, io.BytesIO(b"x"), 1)
    try:
        with pytest.raises(S3Error) as e:
            c.remove_object("archive", key)
        assert e.value.code == "AccessDenied"
        assert root.get_object("archive", key).read() == b"x"  # still there
    finally:
        root.remove_object("archive", key)


def test_archiver_cannot_use_another_bucket() -> None:
    root, c = s3_client("root"), s3_client("archiver")
    bucket = f"t-{vid().lower()}"
    root.make_bucket(bucket)
    try:
        with pytest.raises(S3Error) as e:
            c.put_object(bucket, "x", io.BytesIO(b"x"), 1)
        assert e.value.code == "AccessDenied"
    finally:
        root.remove_bucket(bucket)


def test_archiver_cannot_list_or_touch_other_buckets_and_cannot_admin() -> None:
    c = s3_client("archiver")
    with pytest.raises(S3Error) as e:
        list(c.list_objects("no-such-or-other-bucket"))
    assert e.value.code in {"AccessDenied", "NoSuchBucket"}
    with pytest.raises(S3Error):
        c.make_bucket(f"t-{vid().lower()}")


def test_init_rerun_keeps_policy() -> None:
    for _ in range(2):
        assert subprocess.run(["docker", "compose", "run", "--rm", "minio-init"]).returncode == 0
    root, c = s3_client("root"), s3_client("archiver")
    key = f"t/{vid()}.bin"
    c.put_object("archive", key, io.BytesIO(b"x"), 1)
    try:
        with pytest.raises(S3Error) as e:
            c.remove_object("archive", key)
        assert e.value.code == "AccessDenied"
        assert c.get_object("archive", key).read() == b"x"  # and it still has its grants
    finally:
        root.remove_object("archive", key)
