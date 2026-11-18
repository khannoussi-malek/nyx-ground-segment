"""UI-07: the api's start must not import what only a replay needs. minio + pyarrow cost ~150 ms
(more under CPU contention) of the restart budget; they load when the first replay needs them."""

import subprocess
import sys

HEAVY = ("minio", "pyarrow")


def test_api_start_does_not_import_the_archive_stack() -> None:
    code = f"import sys; import gs.api.server; print([m for m in {HEAVY!r} if m in sys.modules])"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"
