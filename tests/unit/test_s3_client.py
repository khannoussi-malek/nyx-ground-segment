"""The archiver's MinIO client pools enough connections for the pass and its health probes."""

from gs.archiver.s3 import client
from gs.common.s3_settings import S3Settings


def test_client_pool_fits_the_pass_and_two_s3_health_probes() -> None:
    c = client(S3Settings("127.0.0.1:9000", "ak", "sk", "bucket"))
    assert c._http.connection_pool_kw.get("maxsize", 1) >= 4  # urllib3 default is 1
