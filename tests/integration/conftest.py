from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import yaml

from gs.api.app import create_app
from gs.api.auth import hash_password
from tests.support.http import FC1_PW, FC2_PW, api_settings


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
