from datetime import UTC, datetime, timedelta

from gs.ingest.events import GroundEvent, frozen_key
from gs.ingest.link import FROZEN_S, FrozenState, frozen_step

V = "NYX-TA1"
T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
VALS: tuple[float | None, ...] = (101.3, 22.0, 28.0, 0.0, 0.0, 0.0)


def t_at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def run(
    frames: list[tuple[float | None, ...]], hz: float = 10.0, s: FrozenState | None = None
) -> list[tuple[int, GroundEvent]]:
    s = s or FrozenState()
    out: list[tuple[int, GroundEvent]] = []
    for i, vals in enumerate(frames):
        s, ev = frozen_step(s, V, T0, i, t_at(i / hz), vals)
        if ev is not None:
            out.append((i, ev))
    return out


def test_all_identical_2s_frozen_once() -> None:
    assert FROZEN_S == 2.0
    ((i, ev),) = run([VALS] * 25)
    assert i == 20  # t − t_from = 2.0 exactly
    assert (ev.kind, ev.key, ev.vehicle) == ("FROZEN", frozen_key(V, T0, 0), V)
    assert ev.details == {"first_seq": 0, "t_from": T0} and ev.t_onboard == t_at(2.0)


def test_one_value_changing_not_frozen() -> None:
    frames = [(*VALS[:3], 0.001 * (i % 2), *VALS[4:]) for i in range(60)]  # only rate_x varies
    assert run(frames) == []


def test_100hz_needs_2s_not_20_frames() -> None:  # RV-24
    assert run([VALS] * 25, hz=100.0) == []
    ((i, _),) = run([VALS] * 250, hz=100.0)
    assert i == 200


def test_new_run_after_change_reports_again() -> None:
    other = (101.4, *VALS[1:])
    got = run([VALS] * 25 + [other] * 25)
    assert [(i, e.details["first_seq"]) for i, e in got] == [(20, 0), (45, 25)]
    assert got[1][1].key == frozen_key(V, T0, 25)


def test_negative_zero_counts_as_frozen_and_none_compares() -> None:
    a = (*VALS[:3], 0.0, None, 0.0)
    b = (*VALS[:3], -0.0, None, 0.0)
    assert len(run([a, b] * 15)) == 1
