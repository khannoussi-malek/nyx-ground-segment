"""The system-test guard names every long-running service a coverage run may have stopped."""

from tests.support.compose import STACK_SERVICES, stack_problems

UP = {s: {"Service": s, "State": "running", "Health": "healthy"} for s in STACK_SERVICES}


def test_all_up_is_no_problem() -> None:
    assert stack_problems(api_ok=True, ps=UP) == []


def test_stopped_sim_and_ingest_are_named() -> None:
    ps = {
        **UP,
        "simulator": {"State": "exited", "Health": ""},  # `docker compose stop` by a module fixture
        "ingest-1": {"State": "running", "Health": "starting"},
    }
    del ps["ingest-2"]  # never created
    assert stack_problems(api_ok=False, ps=ps) == ["api", "simulator", "ingest-1", "ingest-2"]


def test_guard_covers_sim_and_both_workers() -> None:
    assert {"simulator", "ingest-1", "ingest-2"} <= set(STACK_SERVICES)
