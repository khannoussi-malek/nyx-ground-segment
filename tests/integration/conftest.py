from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import yaml

from gs.api.app import create_app
from gs.api.auth import hash_password
from gs.api.settings import Settings
from tests.support.env import REPO, dotenv
from tests.support.pg import app_dsn

FC1_PW, FC2_PW = "fc1-test-password", "fc2-test-password"


def api_settings(users_file: Path, **over: str) -> Settings:
    env = dotenv()
    return Settings(
        redis_url=over.get(
            "redis_url",
            f"redis://api:{env['REDIS_API_PW']}@127.0.0.1:{env['REDIS_HOST_PORT']}/0",
        ),
        database_url=over.get("database_url", app_dsn("gs")),
        session_secret="test-secret",
        users_file=users_file,
        mission_db=REPO / "mission",
        vehicles=("NYX-TA1",),
    )


@pytest.fixture(scope="session")
def users_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api") / "users.yaml"
    users = {"fc1": hash_password(FC1_PW), "fc2": hash_password(FC2_PW)}
    path.write_text(yaml.safe_dump({"users": users}))
    return path


@pytest.fixture
async def api_client(users_file: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(api_settings(users_file))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            yield client
