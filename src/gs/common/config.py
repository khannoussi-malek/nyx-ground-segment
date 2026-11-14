import os
import re

VEHICLE_ID = re.compile(r"[A-Z0-9-]{1,32}")  # same pattern as interfaces.base.VehicleId


class ConfigError(Exception):
    """A required environment variable is missing, empty or malformed."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable {name}")
    return value


def require_int(name: str) -> int:
    raw = require(name)
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"environment variable {name} must be an integer, got {raw!r}") from None


def vehicle_list(raw: str, name: str = "VEHICLES") -> tuple[str, ...]:
    names = tuple(v for v in (p.strip() for p in raw.split(",")) if v)
    if not names:
        raise ConfigError(f"{name} lists no vehicle")
    for v in names:
        if not VEHICLE_ID.fullmatch(v):
            raise ConfigError(f"{name} has invalid vehicle name {v!r} (want {VEHICLE_ID.pattern})")
    return names
