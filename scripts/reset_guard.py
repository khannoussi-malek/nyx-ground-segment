"""`make reset` guard: refuse while the MinIO archive holds archived hours (R3-02)."""

import os
import subprocess
import sys

COUNT = ["docker", "compose", "exec", "-T", "db", "psql", "-U", "gs_owner", "-d", "gs", "-Atc"]


def main(rows: int, force: bool) -> int:
    if rows and not force:
        print(
            f"make reset deletes the database, Redis and the MinIO archive ({rows} archived"
            " hours). Run FORCE=1 make reset to confirm."
        )
        return 1
    return 0


if __name__ == "__main__":
    force = bool(os.environ.get("FORCE"))
    out = subprocess.run(
        [*COUNT, "select count(*) from archive_object"], capture_output=True, text=True
    )
    # no db container (never started, or already reset): nothing to lose
    sys.exit(main(int(out.stdout.strip() or 0) if out.returncode == 0 else 0, force))
