import pytest

from tests.property import profiles


def pytest_report_header(config: pytest.Config) -> str:
    seed = f", seed {profiles.SEED}" if profiles.SEED is not None else ""
    return f"hypothesis profile: {profiles.NAME}{seed}"
