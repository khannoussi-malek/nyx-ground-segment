import subprocess
from typing import Any

import pytest
import redis
import redis.exceptions

from tests.support.env import dotenv, vid
from tests.support.redisx import client, run_check_acl_log


def test_tm_nyx_scoping() -> None:  # A-R2R-9, design 3.7
    v = vid()
    sim, ing = client("sim"), client("ingest")
    with pytest.raises(redis.exceptions.NoPermissionError):
        sim.xadd("tm:dlq", {"data": "{}"})
    assert sim.xadd(f"tm:{v}", {"data": "{}"})
    ing.xgroup_create(f"tm:{v}", "ingest", id="0", mkstream=True)
    got: Any = ing.xreadgroup("ingest", "ingest-1", {f"tm:{v}": ">"}, count=1)
    entry_id = got[0][1][0][0]
    with ing.pipeline(transaction=True) as p:
        p.xadd("tm:dlq", {"data": "{}"})
        p.xack(f"tm:{v}", "ingest", str(entry_id))
        assert p.execute()[1] == 1
    consumers: Any = ing.xinfo_consumers(f"tm:{v}", "ingest")
    assert consumers[0]["name"] == "ingest-1"


def test_api_may_dlq_but_not_frames() -> None:  # D-ING-27: schema-rejected ui:events entry
    api = client("api")
    assert api.xadd("tm:dlq", {"data": "{}"})
    with pytest.raises(redis.exceptions.NoPermissionError):
        api.xadd("tm:NYX-TA1", {"data": "{}"})


def test_default_user_is_off() -> None:
    port = int(dotenv()["REDIS_HOST_PORT"])
    with pytest.raises(redis.exceptions.AuthenticationError):
        redis.Redis(host="127.0.0.1", port=port).ping()


@pytest.mark.parametrize(("pw", "ok"), [("$REDIS_HEALTH_PW", True), ("wrong", False)])
def test_healthcheck_command(pw: str, ok: bool) -> None:  # redis-cli exits 0 on WRONGPASS (S3)
    cmd = f'redis-cli --no-auth-warning --user health --pass "{pw}" ping | grep -q PONG'
    rc = subprocess.run(["docker", "compose", "exec", "-T", "redis", "sh", "-c", cmd]).returncode
    assert (rc == 0) is ok


def test_acl_log_check_fails_on_denial_then_passes_after_reset() -> None:
    with pytest.raises(redis.exceptions.NoPermissionError):
        client("sim").xadd("ui:events", {"data": "{}"})
    assert run_check_acl_log() == 1
    client("admin").acl_log_reset()
    assert run_check_acl_log() == 0
