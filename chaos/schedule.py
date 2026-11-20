"""The seeded chaos schedules (D-EXT-15, EX-17, A-EXR-13). Pure: the same seed gives the same two
schedules. Simulator faults come every 20-40 s; infrastructure actions every 90-120 s, start to
start, and an action plus the 20 s quiet period always fits in the shortest gap (60 + 20 < 90),
so at most one is in flight. The runner holds the next start back if a recovery ran long."""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

VEHICLES = ("NYX-TA1", "NYX-TA2")
INGEST = ("ingest-1", "ingest-2")
FAULT_GAP_S = (20.0, 40.0)
INFRA_GAP_S = (90.0, 120.0)
QUIET_S = 20.0  # after an action has recovered
TAIL_S = 60.0  # nothing starts in the last minute: its recovery would not be judged

# (kind, duration range in s); a fixed duration has equal bounds
INFRA_KINDS: Mapping[str, tuple[float, float]] = {
    "worker_kill": (20, 60),
    "worker_hang": (35, 35),
    "api_restart": (0, 0),
    "broker_restart": (10, 10),
    "sim_restart": (0, 0),
    "partition_redis": (20, 40),
    "partition_db": (20, 40),
    "pg_readonly": (10, 10),
}
FAULT_KINDS = (
    "leak", "overheat", "dropout", "freeze", "seq_jump", "duplicate", "malformed", "pause",
    "hold_exec",
)  # fmt: skip
WORKER_KINDS = frozenset({"worker_kill", "worker_hang", "partition_redis", "partition_db"})


@dataclass(frozen=True)
class Fault:
    at_s: float
    vehicle: str
    fault: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class Action:
    at_s: float
    kind: str
    target: str  # an ingest service for the worker kinds, else ""
    duration_s: float

    @property
    def end_s(self) -> float:
        return self.at_s + self.duration_s


def _deck(rng: random.Random, items: tuple[str, ...]) -> list[str]:
    """One shuffled pass over every kind: no kind is missing from a long enough run."""
    deck = list(items)
    rng.shuffle(deck)
    return deck


def _params(rng: random.Random, fault: str) -> dict[str, Any]:
    def r(lo: float, hi: float) -> float:
        return round(rng.uniform(lo, hi), 1)

    match fault:
        case "leak" | "overheat":
            return {"duration_s": r(30, 45)}  # bounded, so its alarm window is known
        case "dropout":
            return {"duration_s": r(2, 10)}
        case "freeze":
            return {"duration_s": r(3, 10)}
        case "duplicate":
            return {"count": rng.randint(1, 3)}
        case "malformed":
            return {"kind": rng.choice(["json", "schema"])}
        case "pause":
            return {"duration_s": 6}
        case "hold_exec":
            return {"duration_s": 16}
        case _:
            return {}  # seq_jump: the default jump


def fault_schedule(seed: int, minutes: float) -> list[Fault]:
    rng, total = random.Random(f"{seed}:faults"), minutes * 60
    out: list[Fault] = []
    deck: list[str] = []
    t = rng.uniform(*FAULT_GAP_S)
    while t < total - TAIL_S / 2:
        deck = deck or _deck(rng, FAULT_KINDS)
        kind = deck.pop()
        out.append(Fault(round(t, 1), rng.choice(VEHICLES), kind, _params(rng, kind)))
        t += rng.uniform(*FAULT_GAP_S)
    return out


def infra_schedule(seed: int, minutes: float) -> list[Action]:
    rng, total = random.Random(f"{seed}:infra"), minutes * 60
    out: list[Action] = []
    deck: list[str] = []
    t = rng.uniform(*INFRA_GAP_S)
    while t < total - TAIL_S:
        deck = deck or _deck(rng, tuple(INFRA_KINDS))
        kind = deck.pop()
        lo, hi = INFRA_KINDS[kind]
        target = rng.choice(INGEST) if kind in WORKER_KINDS else ""
        out.append(Action(round(t, 1), kind, target, round(rng.uniform(lo, hi), 1)))
        t += rng.uniform(*INFRA_GAP_S)
    return out
