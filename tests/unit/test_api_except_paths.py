"""The failure handlers in the API and the archiver loop (D-ENG-21): each is run once against a
fake whose dependency fails, and must degrade the way its comment says."""

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
import redis.exceptions
from psycopg_pool import PoolTimeout

from gs.api import auth, commands, dbguard
from gs.api.bodies import CommandRequest
from gs.api.deps import ApiError
from gs.api.hub import Hub
from gs.archiver.main import _sleep


class _DeadPool:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    @asynccontextmanager
    async def connection(self, timeout: float | None = None) -> Any:
        raise self.exc
        yield  # pragma: no cover


def _req(pool: Any = None, r: Any = None) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pool=pool, redis=r)),
        url=SimpleNamespace(path="/api/x"),
    )


def _body(**kw: Any) -> CommandRequest:
    return CommandRequest(vehicle="NYX-T1", name="PING", args={}, **kw)


async def test_auth_audit_database_down_is_503() -> None:
    with pytest.raises(ApiError) as e:
        await auth._audit(_req(_DeadPool(PoolTimeout("t"))), "u", "LOGIN")
    assert e.value.status == 503


async def test_commands_audit_database_down_is_503() -> None:
    with pytest.raises(ApiError) as e:
        await commands._audit(
            _req(_DeadPool(psycopg.OperationalError("x"))), "u", "CMD_REJECTED", None, {}
        )
    assert e.value.status == 503


async def test_release_lock_lost_to_a_new_holder_is_silent() -> None:
    pipe = AsyncMock()
    pipe.get.return_value = "mine"
    pipe.execute.side_effect = redis.exceptions.WatchError()
    pipe.multi = lambda: None  # sync in redis-py
    pipe.delete = lambda k: None
    r = SimpleNamespace(pipeline=lambda transaction: _CM(pipe))
    await commands.release_lock(r, "k", "mine")  # type: ignore[arg-type]
    pipe.execute.assert_awaited_once()


class _CM:
    def __init__(self, v: Any) -> None:
        self.v = v

    async def __aenter__(self) -> Any:
        return self.v

    async def __aexit__(self, *a: object) -> None:
        return None


async def test_confirm_broker_down_both_calls_503() -> None:
    boom = redis.exceptions.ConnectionError("x")
    r = SimpleNamespace(set=AsyncMock(side_effect=boom), getdel=AsyncMock(side_effect=boom))
    for body in (_body(), _body(confirm_token="t", reason="a long enough reason")):
        with pytest.raises(ApiError) as e:
            await commands._confirm(_req(r=r), body, "u", "h", "boom")
        assert e.value.error == "BROKER_DOWN"


async def test_take_lock_holder_vanished_still_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "_audit", AsyncMock())
    r = SimpleNamespace(set=AsyncMock(return_value=None), get=AsyncMock(return_value=None))
    with pytest.raises(ApiError) as e:  # holder expired between SET and GET: no cmd_id to look up
        await commands._take_lock(_req(r=r), _body(), "u", "k", "o")
    assert e.value.error == "DUPLICATE_COMMAND"


async def test_take_lock_database_down_is_503() -> None:
    r = SimpleNamespace(
        set=AsyncMock(return_value=None), get=AsyncMock(return_value=f"{uuid4()}|bob")
    )
    pool = _DeadPool(psycopg.OperationalError("x"))
    with pytest.raises(ApiError) as e:
        await commands._take_lock(_req(pool, r), _body(), "u", "k", "o")
    assert e.value.error == "UNAVAILABLE"


async def test_send_error_database_down_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tc = SimpleNamespace(cmd_id=uuid4(), issued_by="u")
    with caplog.at_level(logging.ERROR):
        await commands._send_error(_req(_DeadPool(psycopg.OperationalError("x"))), tc, "NYX-T1")  # type: ignore[arg-type]
    assert "could not record SEND_ERROR" in caplog.text


async def test_release_broker_error_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        commands, "release_lock", AsyncMock(side_effect=redis.exceptions.ConnectionError("x"))
    )
    with caplog.at_level(logging.WARNING):
        await commands._release(_req(r=object()), "k", "o")
    assert "could not release k" in caplog.text


async def test_hub_reject_still_dead_letters_when_the_database_is_down() -> None:
    r = SimpleNamespace(xadd=AsyncMock())
    hub = Hub(r, _DeadPool(PoolTimeout("t")), ["NYX-T1"], lambda: [])  # type: ignore[arg-type]
    await hub._reject("1-0", "{}", "bad")
    assert hub.degraded
    r.xadd.assert_awaited_once()


async def test_archiver_sleep_returns_on_timeout() -> None:
    await _sleep(asyncio.Event(), 0.01)


@pytest.mark.parametrize(
    ("exc", "status", "error"),
    [
        (psycopg.DataError("string contains NUL"), 422, "VALIDATION_ERROR"),
        (psycopg.OperationalError("down"), 503, "UNAVAILABLE"),
        (psycopg.errors.UndefinedTable("x"), 503, "UNAVAILABLE"),
        (PoolTimeout("t"), 503, "UNAVAILABLE"),
    ],
)
def test_db_503_maps_bad_value_to_422_and_the_rest_to_503(
    exc: Exception, status: int, error: str
) -> None:
    req = SimpleNamespace(url=SimpleNamespace(path="/api/x"))
    with pytest.raises(ApiError) as e, dbguard.db_503(req):  # type: ignore[arg-type]
        raise exc
    assert (e.value.status, e.value.error) == (status, error)
    assert "NUL" not in e.value.message  # no echo of the raw value


def test_db_503_lets_other_errors_through() -> None:
    req = SimpleNamespace(url=SimpleNamespace(path="/api/x"))
    with pytest.raises(ValueError), dbguard.db_503(req):  # type: ignore[arg-type]
        raise ValueError("not a database error")
