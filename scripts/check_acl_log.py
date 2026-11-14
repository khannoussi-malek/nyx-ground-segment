"""Exit 0 if Redis ACL LOG (read as admin) is empty, else print entries and exit 1 ."""

import sys
from pathlib import Path

import redis


def _read_env(path: Path) -> dict[str, str]:
    return dict(
        ln.split("=", 1) for ln in path.read_text().splitlines() if "=" in ln and ln[0] != "#"
    )


def main() -> int:
    env = _read_env(Path(__file__).resolve().parents[1] / ".env")
    r = redis.Redis(
        host="127.0.0.1",
        port=int(env.get("REDIS_HOST_PORT", "6379")),
        username="admin",
        password=env["REDIS_ADMIN_PW"],
        decode_responses=True,
    )
    entries = r.acl_log()
    for e in entries:
        print(e)
    return 1 if entries else 0


if __name__ == "__main__":
    sys.exit(main())
