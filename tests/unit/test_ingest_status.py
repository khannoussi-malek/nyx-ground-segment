from gs.ingest.liveness import STALE_S, ingest_status


def test_standby_task_is_not_down() -> None:
    assert ingest_status({}, [], 100.0, 1) == (
        "ok",
        {"owned": [], "loop_age_s": {}, "redis_used_memory": 1},
    )


def test_ok_when_every_owned_loop_is_fresh() -> None:
    state, checks = ingest_status({"NYX-A": 99.0, "NYX-B": 10.0}, ["NYX-A"], 100.0, 5)
    assert state == "ok" and checks["loop_age_s"] == {"NYX-A": 1.0}  # B is not owned
    assert checks["owned"] == ["NYX-A"]


def test_owned_stale_loop_down() -> None:
    seen = {"NYX-A": 99.0, "NYX-B": 100.0 - STALE_S}
    assert ingest_status(seen, ["NYX-B", "NYX-A"], 100.0, 5)[0] == "down"
    assert ingest_status({}, ["NYX-A"], 100.0, 5)[0] == "down"  # owned, never passed


def test_redis_unknown_degraded() -> None:
    assert ingest_status({"NYX-A": 99.0}, ["NYX-A"], 100.0, None)[0] == "degraded"
    assert ingest_status({}, ["NYX-A"], 100.0, None)[0] == "down"  # down wins
