"""MinIO connection settings, apart from gs.archiver.s3 so that reading them never imports minio
(the api loads minio only when a replay needs it: UI-07 restart budget)."""

from dataclasses import dataclass

from gs.common.config import require


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
