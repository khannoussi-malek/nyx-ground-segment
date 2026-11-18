"""Housekeeping for throwaway test vehicles (`vid()`): find and delete the Redis keys they leave."""

import logging
import re
import sys

import redis

TEST_VEHICLE = re.compile(r"^NYX-T[0-9A-F]{8}$")
PREFIXES = ("tm:", "tc:", "tcack:", "hb:veh:", "cmdlock:", "frame_seq:")  # keys carrying a vehicle
BATCH = 500
PURGE_MIN_IDLE_S = 600  # a test's keys are touched within seconds; a leftover is hours old
log = logging.getLogger(__name__)


def vehicle_keys(r: redis.Redis) -> list[str]:
    found: list[str] = []
    for prefix in PREFIXES:
        for key in r.scan_iter(match=f"{prefix}NYX-T*", count=1000):
            if TEST_VEHICLE.match(key[len(prefix) :].split(":", 1)[0]):
                found.append(key)
    return found


def _idle_at_least(r: redis.Redis, key: str, min_idle_s: int) -> bool:
    idle = r.object("idletime", key)  # None: the key vanished meanwhile, skip it
    return idle is not None and int(idle) >= min_idle_s


def purge_test_keys(r: redis.Redis, min_idle_s: int = PURGE_MIN_IDLE_S) -> int:
    """Delete throwaway-vehicle keys idle for `min_idle_s`+, so a running session's keys survive."""
    keys = [k for k in vehicle_keys(r) if _idle_at_least(r, k, min_idle_s)]
    for i in range(0, len(keys), BATCH):
        r.delete(*keys[i : i + BATCH])
    return len(keys)


def purge_quietly() -> None:
    """Session housekeeping with the admin client: never a test failure, even with Redis down."""
    from tests.support.redisx import client

    try:
        purge_test_keys(client("admin"))
    except (redis.RedisError, OSError, KeyError) as exc:
        log.warning("test key purge skipped: %s", exc)


def trim_if_drained(r: redis.Redis, stream: str, group: str = "ingest") -> bool:
    """Empty `stream` (exact trim) once `group` has nothing pending and no lag."""
    info = next((g for g in r.xinfo_groups(stream) if g["name"] == group), None)
    if info is None or info["pending"] != 0 or info["lag"] != 0:
        print(f"warning: {stream} not drained, left untrimmed", file=sys.stderr)
        return False
    r.xtrim(stream, maxlen=0, approximate=False)
    return True
