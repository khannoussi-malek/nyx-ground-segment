"""The chaos schedules (D-EXT-15, EX-17, A-EXR-13): seeded, one infrastructure action at a time,
quiet gaps, and the counts the acceptance seed must reach in 30 min."""

from chaos.schedule import (
    FAULT_GAP_S,
    FAULT_KINDS,
    INFRA_GAP_S,
    INFRA_KINDS,
    QUIET_S,
    WORKER_KINDS,
    fault_schedule,
    infra_schedule,
)

ACCEPTANCE_SEED = 20261120
SEEDS = range(50)


def test_same_seed_same_schedule() -> None:
    assert fault_schedule(7, 30) == fault_schedule(7, 30)
    assert infra_schedule(7, 30) == infra_schedule(7, 30)
    assert fault_schedule(7, 30) != fault_schedule(8, 30)
    assert infra_schedule(7, 30) != infra_schedule(8, 30)


def test_the_two_schedules_are_independent() -> None:
    # the infrastructure schedule does not change when the fault schedule is drawn first
    before = infra_schedule(3, 30)
    fault_schedule(3, 30)
    assert infra_schedule(3, 30) == before


def test_infrastructure_actions_never_overlap_and_leave_a_quiet_gap() -> None:
    for seed in SEEDS:
        acts = infra_schedule(seed, 30)
        for a, b in zip(acts, acts[1:], strict=False):
            assert b.at_s - a.end_s >= QUIET_S, (seed, a, b)  # one in flight, quiet after it
            assert INFRA_GAP_S[0] - 0.1 <= b.at_s - a.at_s <= INFRA_GAP_S[1] + 0.1


def test_fault_gaps_are_20_to_40_s() -> None:
    for seed in SEEDS:
        fs = fault_schedule(seed, 30)
        gaps = [b.at_s - a.at_s for a, b in zip(fs, fs[1:], strict=False)]
        assert all(FAULT_GAP_S[0] - 0.1 <= g <= FAULT_GAP_S[1] + 0.1 for g in gaps), seed


def test_acceptance_seed_reaches_15_actions_and_30_faults_in_30_min() -> None:
    assert len(infra_schedule(ACCEPTANCE_SEED, 30)) >= 15
    assert len(fault_schedule(ACCEPTANCE_SEED, 30)) >= 30


def test_every_kind_is_drawn_in_30_min() -> None:
    assert {a.kind for a in infra_schedule(ACCEPTANCE_SEED, 30)} == set(INFRA_KINDS)
    assert {f.fault for f in fault_schedule(ACCEPTANCE_SEED, 30)} == set(FAULT_KINDS)


def test_worker_actions_name_an_ingest_service_the_rest_none() -> None:
    for a in infra_schedule(ACCEPTANCE_SEED, 30):
        assert (a.target in ("ingest-1", "ingest-2")) == (a.kind in WORKER_KINDS)
        lo, hi = INFRA_KINDS[a.kind]
        assert lo <= a.duration_s <= hi


def test_nothing_starts_in_the_last_minute() -> None:
    assert all(a.at_s < 5 * 60 - 60 for a in infra_schedule(1, 5))
