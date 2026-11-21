"""The action command builders (no Docker): the right verbs, in order, and every undo runs even
when the hold is interrupted. Plus the compose overlay's network topology."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import chaos.actions as actions
from chaos.schedule import Action
from tests.support.env import REPO

Calls = list[tuple[str, ...]]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    seen: Calls = []

    class Done:
        stdout = "cid123\n"

    def compose(*a: str, check: bool = True) -> Done:
        seen.append(("compose", *a))
        return Done()

    monkeypatch.setattr(actions, "compose", compose)
    monkeypatch.setattr(actions, "_docker", lambda *a: seen.append(("docker", *a)))
    monkeypatch.setattr(actions, "set_read_only", lambda on: seen.append(("ro", str(on))))
    monkeypatch.setattr(
        actions,
        "_networks",
        lambda svc, suffix: ("cid123", f"proj_{suffix}"),
    )
    return seen


def run(kind: str, target: str = "ingest-1", d: float = 3.0) -> list[float]:
    slept: list[float] = []
    actions.run_action(Action(0, kind, target, d), slept.append)
    return slept


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("worker_kill", [("compose", "kill", "ingest-1"), ("compose", "up", "-d", "ingest-1")]),
        ("worker_hang", [("compose", "pause", "ingest-1"), ("compose", "unpause", "ingest-1")]),
        ("broker_restart", [("compose", "stop", "redis"), ("compose", "start", "redis")]),
        (
            "partition_redis",
            [
                ("docker", "network", "disconnect", "proj_redisnet", "cid123"),
                ("docker", "network", "connect", "proj_redisnet", "cid123"),
            ],
        ),
        (
            "partition_db",
            [
                ("docker", "network", "disconnect", "proj_dbnet", "cid123"),
                ("docker", "network", "connect", "proj_dbnet", "cid123"),
            ],
        ),
        ("pg_readonly", [("ro", "True"), ("ro", "False")]),
    ],
)
def test_hold_actions_do_then_undo(calls: Calls, kind: str, expected: Calls) -> None:
    assert run(kind) == [3.0]
    assert calls == expected


def test_restarts_are_one_verb_without_a_hold(calls: Calls) -> None:
    assert run("api_restart", "", 0) == []
    assert run("sim_restart", "", 0) == []
    assert calls == [("compose", "restart", "api"), ("compose", "restart", "simulator")]


def test_the_undo_runs_when_the_hold_is_interrupted(calls: Calls) -> None:
    def boom(_: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        actions.run_action(Action(0, "partition_db", "ingest-2", 20), boom)
    assert calls[-1][:3] == ("docker", "network", "connect")


def test_unknown_action_and_worker_action_without_a_worker_are_refused(calls: Calls) -> None:
    with pytest.raises(ValueError):
        run("explode", "")
    with pytest.raises(AssertionError):
        run("worker_kill", "")
    assert calls == []


def _services(files: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for f in files:
        for name, svc in yaml.safe_load(f.read_text()).get("services", {}).items():
            merged.setdefault(name, {}).update(svc)
    return merged


def test_overlay_puts_redis_and_db_on_separate_networks() -> None:
    svc = _services([REPO / "docker-compose.chaos.yml"])
    assert svc["redis"]["networks"] == ["redisnet"] and svc["db"]["networks"] == ["dbnet"]
    for w in ("ingest-1", "ingest-2"):  # cut from one, still on the other
        assert set(svc[w]["networks"]) == {"redisnet", "dbnet"}
    assert "dbnet" not in svc["simulator"]["networks"]  # the simulator only needs Redis
    env = svc["simulator"]["environment"]
    assert env["GS_TEST_HOOKS"] == "1" and env["SIM_LEDGER"].startswith("/ledger/")


def test_core_compose_is_untouched_by_the_overlay() -> None:
    core = (REPO / "docker-compose.yml").read_text()
    assert "redisnet" not in core and "SIM_LEDGER" not in core
    assert json.dumps(yaml.safe_load(core)["services"]["redis"]).count("networks") == 0
