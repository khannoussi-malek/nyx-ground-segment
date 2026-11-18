import random
from datetime import UTC, datetime

import pytest

from gs.alarms.checks import CHECKS
from gs.alarms.checks.limit import LimitCheck
from gs.alarms.contract import Level
from gs.alarms.engine import CompiledSet, Slot, initial_state, step
from perf.alarm_tuning import (
    far_and_raise_rate,
    frontier,
    grid,
    margins,
    render,
    run_cell,
    study,
    z_n,
)

XU = [
    (1, 0.2, 0.2000, 0.16000),
    (3, 0.2, 0.0298, 0.00626),
    (3, 0.5, 0.5000, 0.03571),
    (5, 0.4, 0.1554, 0.00524),
]


@pytest.mark.parametrize("n,p,far,rate", XU)
def test_chain_matches_xu_table(n: int, p: float, far: float, rate: float) -> None:
    f, r = far_and_raise_rate(n, p)
    assert f == pytest.approx(far, abs=0.001) and r == pytest.approx(rate, rel=0.01)


@pytest.mark.parametrize("n,p,far,rate", XU)
def test_engine_matches_xu_chain(n: int, p: float, far: float, rate: float) -> None:
    cfg = LimitCheck(
        type="limit", yellow_high=0.5, red_high=9.0, hysteresis_abs=1e-9, persistence=n
    )
    cset = CompiledSet("s", "l", {"x": ((CHECKS["limit"](cfg), n),)})
    rng = random.Random(f"xu-{n}-{p}")
    st, in_alarm, raises, t0, samples = (
        initial_state(cset),
        0,
        0,
        datetime(2026, 1, 1, tzinfo=UTC),
        400_000,
    )
    for i in range(samples):
        v = 1.0 if rng.random() < p else 0.0
        st, trs = step(st, cset, "V", t0, i, t0, 0.1, {"x": v}, False)
        slot: Slot = st.slots[("x", "limit")]
        in_alarm += slot.level > Level.NOMINAL
        raises += sum(t.kind == "RAISE" for t in trs)
    assert in_alarm / samples == pytest.approx(far, abs=0.01)
    assert raises / samples == pytest.approx(rate, rel=0.05)


def test_grid_enumerated_as_specified() -> None:
    assert len(grid()) == 5 * 7 + 5 * 5 and grid()[0] == ("deadband", 1, 2)
    assert grid()[35] == ("roc", 1, 0.05)


def test_z_n_values() -> None:
    assert z_n(1) == pytest.approx(4.50, abs=0.01) and z_n(3) == pytest.approx(2.17, abs=0.01)


def test_bus_current_chatter_margin() -> None:
    assert margins("bus_current_a").chatter_sigma == pytest.approx(0.092, abs=0.002)


def test_same_seed_same_table() -> None:
    assert render(*study("1", hours=0.02)) == render(*study("1", hours=0.02))


def test_frontier_on_hand_built_points() -> None:
    assert frontier([(0, 3), (0, 2), (1, 1), (2, 2)]) == [1, 2]


def test_shipped_cell_meets_acceptance() -> None:
    c = run_cell("deadband", 3, 15, "1", hours=0.02)
    assert c.spike_rejected and c.roc_margin_s >= 5
    assert c.rtn_after_isolate_s == pytest.approx(13.5, abs=0.5)
    assert not run_cell("deadband", 1, 15, "1", hours=0.02).spike_rejected


def test_persistence_delay_is_n_minus_one_frames() -> None:
    assert run_cell("deadband", 3, 15, "1", hours=0.02).leak_delay_s == pytest.approx(0.2)
    assert run_cell("deadband", 2, 15, "1", hours=0.02).leak_delay_s == pytest.approx(0.1)
