"""MinIO access for the archiver: one single-PUT with a server-checked sha256, and a plain GET."""

import asyncio
import base64
import hashlib
import io
from dataclasses import dataclass

import urllib3
from minio import Minio
from minio.error import MinioException

from gs.common.config import require

# refused / reset / timeout / any S3 answer: the pass treats all of them as "MinIO trouble"
S3_ERRORS: tuple[type[BaseException], ...] = (MinioException, urllib3.exceptions.HTTPError, OSError)
REGION = "us-east-1"  # the archiver policy has no GetBucketLocation: never let the client ask
CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 10.0
_MIN_PART = 5 * 1024 * 1024  # above this a client would switch to multipart; one PUT stays one PUT


@dataclass(frozen=True)
class S3Settings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str

    @classmethod
    def from_env(cls) -> S3Settings:
        return cls(
            require("S3_ENDPOINT"), require("S3_ACCESS_KEY"), require("S3_SECRET_KEY"),
            require("S3_BUCKET"),
        )  # fmt: skip


def client(s: S3Settings) -> Minio:
    """Short timeouts and one retry: a black-holed MinIO must fail a pass, not freeze it."""
    http = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
        retries=urllib3.Retry(total=1, backoff_factor=0.2, status_forcelist=(500, 502, 503, 504)),
    )
    return Minio(
        s.endpoint, s.access_key, s.secret_key, secure=False, region=REGION, http_client=http
    )


def checksum_headers(digest: bytes) -> dict[str, str]:
    """The header MinIO checks against the body it received (a mismatch is refused, BadDigest)."""
    return {"x-amz-checksum-sha256": base64.b64encode(digest).decode()}


async def put(c: Minio, bucket: str, key: str, data: bytes) -> str:
    """Single PUT of `data`; returns its sha256 hex, which the server has verified on receipt."""
    digest = hashlib.sha256(data).digest()

    def upload() -> None:
        c.put_object(
            bucket, key, io.BytesIO(data), len(data),
            metadata=checksum_headers(digest),  # type: ignore[arg-type]
            part_size=max(_MIN_PART, len(data)),
        )  # fmt: skip

    await asyncio.to_thread(upload)
    return digest.hex()


async def get(c: Minio, bucket: str, key: str) -> bytes:
    def download() -> bytes:
        r = c.get_object(bucket, key)
        try:
            return r.read()
        finally:
            r.close()
            r.release_conn()

    return await asyncio.to_thread(download)
