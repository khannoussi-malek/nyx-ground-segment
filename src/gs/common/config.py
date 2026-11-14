import os


class ConfigError(Exception):
    """A required environment variable is missing or empty."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable {name}")
    return value


def vehicle_list(raw: str) -> tuple[str, ...]:
    names = tuple(v for v in (p.strip() for p in raw.split(",")) if v)
    if not names:
        raise ConfigError("VEHICLES lists no vehicle")
    return names
