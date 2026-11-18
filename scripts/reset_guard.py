"""`make reset` guard: refuse while the MinIO archive holds archived hours (R3-02)."""

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping

COUNT = ["docker", "compose", "exec", "-T", "db", "psql", "-U", "gs_owner", "-d", "gs", "-Atc"]
COUNT_SQL = "select count(*) from archive_object"
UNKNOWN_MESSAGE = (
    "make reset could not count the archived hours (is the stack up? try make up)."
    " Run FORCE=1 make reset to delete anyway."
)
Run = Callable[..., subprocess.CompletedProcess[str]]


def count_rows(run: Run = subprocess.run) -> int | None:
    """Archived-hour count, or None when it cannot be known (exec failed, no docker, odd output)."""
    try:
        out = run([*COUNT, COUNT_SQL], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    text = out.stdout.strip()
    if out.returncode != 0 or not re.fullmatch(r"[0-9]+", text):
        return None
    return int(text)


def force_requested(env: Mapping[str, str]) -> bool:
    return env.get("FORCE") == "1"


def main(rows: int | None, force: bool) -> int:
    if force:
        return 0
    if rows is None:
        print(UNKNOWN_MESSAGE)
        return 1
    if rows > 0:
        print(
            f"make reset deletes the database, Redis and the MinIO archive ({rows} archived"
            " hours). Run FORCE=1 make reset to confirm."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(count_rows(), force_requested(os.environ)))
