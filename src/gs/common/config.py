import os


class ConfigError(Exception):
    """A required environment variable is missing or empty."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable {name}")
    return value
