"""The action command builders (no Docker): the right verbs, in order, and every undo runs even
when the hold is interrupted. Plus the compose overlay's network topology."""

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import psycopg
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
        stderr = ""
        returncode = 0

    def compose(*a: str, check: bool = True, env: Any = None) -> Done:
        seen.append(("compose", *a))
        return Done()

    def docker(*a: str, check: bool = True) -> Done:
        seen.append(("docker", *a))
        return Done()

    monkeypatch.setattr(actions, "compose", compose)
    monkeypatch.setattr(actions, "_docker", docker)
    monkeypatch.setattr(actions, "compose_ps", lambda: {})
    monkeypatch.setattr(actions, "_read_only_on", lambda: False)
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


def test_a_stop_ends_the_hold_and_the_undo_runs(calls: Calls) -> None:
    stop = threading.Event()
    stop.set()  # the soak ended mid-action: wait() returns at once
    t0 = time.monotonic()
    actions.run_action(Action(0, "pg_readonly", "", 600), stop.wait)
    assert time.monotonic() - t0 < 5 and calls == [("ro", "True"), ("ro", "False")]


def test_restore_runs_every_step_even_when_one_fails(
    calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_orig = vars(actions)["compose"]  # the recording fake

    def compose(*a: str, check: bool = True, env: Any = None) -> Any:
        if a[0] == "up":
            raise subprocess.CalledProcessError(1, "docker compose up")
        return compose_orig(*a, check=check)

    monkeypatch.setattr(actions, "compose", compose)
    failures = actions.restore_stack().failures
    assert len(failures) == 1 and failures[0].startswith("up:")
    assert ("docker", "network", "connect", "proj_redisnet", "cid123") in calls
    assert ("docker", "network", "connect", "proj_dbnet", "cid123") in calls
    assert calls[-1] == ("ro", "False")


def test_restore_reports_a_failed_read_only_reset(
    calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(on: bool) -> None:
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(actions, "set_read_only", down)
    r = actions.restore_stack()
    (failure,) = r.failures
    assert failure.startswith("read-only reset:") and r.lines()[-1].startswith("FAILED ")


def test_restore_reports_each_repair_it_made(calls: Calls, monkeypatch: pytest.MonkeyPatch) -> None:
    ps = {
        "redis": {"State": "exited"},
        "ingest-1": {"State": "paused"},
        "migrate": {"State": "exited"},  # a one-shot: not a repair
        "db": {"State": "running"},
    }
    monkeypatch.setattr(actions, "compose_ps", lambda: ps)
    monkeypatch.setattr(actions, "_read_only_on", lambda: True)
    assert actions.restore_stack().repairs == [
        "ingest-1 was paused: started",
        "redis was exited: started",
        "ingest-1 was paused: unpaused",
        "network reconnect ingest-1 from redis",
        "network reconnect ingest-1 from db",
        "network reconnect ingest-2 from redis",
        "network reconnect ingest-2 from db",
        "read-only was on: reset",
    ]


def test_restore_reports_a_failed_reconnect_but_not_an_existing_connection(
    calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Res:
        def __init__(self, rc: int, err: str) -> None:
            self.stdout, self.stderr, self.returncode = "cid123\n", err, rc

    answers = iter([Res(1, "endpoint already exists in network"), Res(1, "no such network")])
    monkeypatch.setattr(actions, "_docker", lambda *a, **_: next(answers, Res(1, "already exists")))
    r = actions.restore_stack()
    assert r.repairs == []
    (failure,) = r.failures
    assert failure.startswith("networks: CalledProcessError")


def test_restore_up_uses_the_soaks_pins(calls: Calls, monkeypatch: pytest.MonkeyPatch) -> None:
    envs: list[Any] = []

    class Done:
        stdout = "cid123\n"

    def compose(*a: str, check: bool = True, env: Any = None) -> Done:
        if a[0] == "up":
            envs.append((a, env))
        return Done()

    monkeypatch.setattr(actions, "compose", compose)
    actions.restore_stack()
    assert envs == [(("up", "-d"), {"STEAL_IDLE_MS": "30000", "RECLAIM_IDLE_MS": "30000"})]


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


def test_summary_lists_repairs_and_marks_a_failed_end_restore_not_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    import chaos.report as report

    monkeypatch.setattr(report, "image_digests", lambda: [])
    text = report.render(
        1,
        1,
        datetime.now(UTC),
        report.Tally(),
        {},
        None,
        None,
        [],
        (["redis was exited: started"], ["FAILED networks: boom"], True),
    )
    assert "## Start-up repairs\n\n- redis was exited: started" in text
    assert "## End repairs\n\n- FAILED networks: boom" in text
    assert "NOT CLEAN" in text and "**FAIL**" in text


def test_operator_ack_survives_an_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from chaos.operator import Operator

    monkeypatch.setattr("chaos.operator.api_url", lambda: "http://x")
    op = Operator()
    op.http = httpx.Client(
        base_url="http://x",
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"code": "DB_DOWN"})),
    )
    op.ack_reds()
    assert op.failed == 1
