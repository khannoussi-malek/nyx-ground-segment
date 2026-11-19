"""R20 F1-F2: Xu et al. 2012 n-sample on/off-delay Markov chain vs the real Nyx engine, and noise margins.

Run from the repo root: PYTHONPATH=. uv run python docs/research/scripts/r20_xu_chain.py
"""

import random
from statistics import NormalDist

from gs.alarms.checks import Sample
from gs.alarms.checks.limit import Limit, LimitCheck
from gs.alarms.contract import Level
from gs.alarms.engine import Slot, _slot_step

PHI = NormalDist()
HZ = 10.0
SHIFT_H = 8


def stationary(n: int, p: float, iters: int = 3000) -> list[float]:
    """Xu 2012 Figs. 3-4: states 0..n-1 no-alarm with i exceedances in a row, n..2n-1 alarm."""
    k = 2 * n
    T = [[0.0] * k for _ in range(k)]
    for i in range(n):
        T[i][i + 1 if i + 1 < n else n] += p
        T[i][0] += 1 - p
        T[n + i][n + i + 1 if i + 1 < n else 0] += 1 - p
        T[n + i][n] += p
    pi = [1.0 / k] * k
    for _ in range(iters):
        pi = [sum(pi[a] * T[a][b] for a in range(k)) for b in range(k)]
    return pi


def far_and_raise_rate(n: int, p: float) -> tuple[float, float]:
    pi = stationary(n, p)
    return sum(pi[n:]), pi[n - 1] * p  # time in alarm (Xu's FAR), raises per sample


def engine_monte_carlo(n: int, p: float, samples: int, seed: int) -> tuple[float, float]:
    """Real Limit check + real persistence ring; one high trip point, deadband ~0 (Xu's model)."""
    chk = Limit(LimitCheck(type="limit", yellow_high=0.0, hysteresis_abs=1e-12))
    mu = -PHI.inv_cdf(1 - p)  # P(x >= 0) = p for x ~ N(mu, 1)
    rng = random.Random(seed)
    slot = Slot("limit", Level.NOMINAL, None, (), chk.initial())
    in_alarm = raises = 0
    for i in range(samples):
        before = slot.level
        slot, _ = _slot_step(slot, chk, n, Sample(i, i / HZ, rng.gauss(mu, 1.0), 1 / HZ), False)
        in_alarm += slot.level > Level.NOMINAL
        raises += before == Level.NOMINAL and slot.level > Level.NOMINAL
    return in_alarm / samples, raises / samples


NYX = {  # param: (sim sigma, nearest yellow distance), from simulator/nominals.py and mission/limits.yaml
    "cabin_pressure_kpa": (0.02, 2.7),
    "cabin_temp_c": (0.05, 5.0),
    "bus_voltage_v": (0.05, 2.0),
    "bus_current_a": (0.05, 3.0),
    "tank_pressure_bar": (0.02, 3.0),
    "rate_x_dps": (0.01, 2.0),
}


def sigma_crit(dist: float, n: int, two_sided: bool) -> float:
    """Largest sigma with <= 1 nuisance raise per shift (i.i.d. Gaussian)."""
    target = 1 / (SHIFT_H * 3600 * HZ) / (2 if two_sided else 1)
    lo, hi = 1e-6, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if far_and_raise_rate(n, mid)[1] < target else (lo, mid)
    return dist / PHI.inv_cdf(1 - lo)


if __name__ == "__main__":
    for n, p in [(1, 0.2), (3, 0.2), (3, 0.5), (5, 0.4)]:
        far_t, rr_t = far_and_raise_rate(n, p)
        far_m, rr_m = engine_monte_carlo(n, p, 400_000, seed=n * 100 + int(p * 10))
        print(f"N={n} p={p}: time in alarm chain {far_t:.4f} engine {far_m:.4f} | raises/sample chain {rr_t:.5f} engine {rr_m:.5f}")
        assert abs(far_t - far_m) < 0.01 and abs(rr_t - rr_m) / rr_t < 0.05
    print("\nsigma for <= 1 nuisance raise per 8 h shift, N=1 / N=3 (x sim sigma)")
    for name, (sig, dist) in NYX.items():
        c1, c3 = (sigma_crit(dist, n, two_sided=name != "bus_current_a") for n in (1, 3))
        print(f"{name}: z={dist / sig:.0f} | N=1 {c1:.3g} ({c1 / sig:.0f}x)  N=3 {c3:.3g} ({c3 / sig:.0f}x)")
