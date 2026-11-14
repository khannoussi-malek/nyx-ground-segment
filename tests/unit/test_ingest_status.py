from gs.ingest.main import STALE_S, ingest_status

V = ("NYX-A", "NYX-B")


def test_ok_when_every_loop_is_fresh() -> None:
    state, checks = ingest_status({"NYX-A": 99.0, "NYX-B": 95.0}, V, 100.0)
    assert state == "ok" and checks == {"loop_age_s": {"NYX-A": 1.0, "NYX-B": 5.0}}


def test_down_when_one_loop_is_stale() -> None:
    state, _ = ingest_status({"NYX-A": 99.0, "NYX-B": 100.0 - STALE_S - 1}, V, 100.0)
    assert state == "down"


def test_down_when_a_vehicle_never_ran() -> None:
    state, checks = ingest_status({"NYX-A": 99.0}, V, 100.0)
    assert state == "down" and checks["loop_age_s"]["NYX-B"] is None  # type: ignore[index]
