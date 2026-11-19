"""Drain policies vs process-killing frames, with Redis 8.10.3 delivery-count semantics (probed).

Model: a `>` read or XAUTOCLAIM adds 1 to each entry it returns; XPENDING is read-only. A batch is one
transaction: a killer anywhere in it kills the process before commit, so nothing in the batch is acked.
Before processing, an entry with count > 3 goes to the DLQ (D-ING-04). Restart -> drain the PEL, then
read `>`.
"""

import itertools
import random

LIMIT, B = 3, 100


def run(n: int, killers: set[int], policy: str) -> tuple[int, int, int, int]:
    count = {i: 0 for i in range(n)}
    pending: list[int] = []  # PEL, in ID order
    fresh = list(range(n))  # never delivered
    done: set[int] = set()
    dlq: set[int] = set()
    crashes = passes = 0
    draining = False
    while pending or fresh:
        passes += 1
        if draining and pending:
            window = pending[:B]
            if policy == "single":
                take = window[:1]
            elif policy == "batch":
                take = window
            else:  # adaptive: batch while no entry in the window has crash evidence (count <= 1)
                take = window if all(count[i] <= 1 for i in window) else window[:1]
        else:
            draining = False
            take, fresh = fresh[:B], fresh[B:]
            pending += take
        for i in take:
            count[i] += 1
        batch = []
        for i in take:
            if count[i] > LIMIT:
                dlq.add(i)
                pending.remove(i)
            else:
                batch.append(i)
        if any(i in killers for i in batch):
            crashes += 1
            draining = True  # restart: drain the PEL first
            continue
        for i in batch:
            done.add(i)
            pending.remove(i)
    collateral = len(dlq - killers)
    missed = len(killers - dlq)
    return crashes, collateral, missed, passes


def main() -> None:
    rng = random.Random(1)
    cases = [(500, {k}) for k in (0, 1, 50, 99, 100, 250, 499)]
    cases += [(500, set(rng.sample(range(500), k))) for k in (2, 3, 5) for _ in range(40)]
    cases += [(500, {10, 11}), (500, {10, 60}), (500, {0, 99, 100})]
    for policy in ("single", "batch", "adaptive"):
        worst = [0, 0, 0, 0]
        for n, ks in cases:
            res = run(n, ks, policy)
            worst = [max(a, b) for a, b in zip(worst, res)]
        tot = [sum(run(n, ks, policy)[3] for n, ks in cases)]
        print(f"{policy:9s} worst crashes {worst[0]:2d} | worst collateral DLQ {worst[1]:3d} | "
              f"killers missed {worst[2]} | total passes {tot[0]}")
    # drain cost after one crash with no killer left (e.g. OOM kill): passes to empty a 100-entry PEL
    for policy in ("single", "adaptive"):
        print(policy, "passes to drain a clean 100-entry PEL:", 100 if policy == "single" else 1)


if __name__ == "__main__":
    main()
