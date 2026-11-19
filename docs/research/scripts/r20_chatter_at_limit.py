"""R20 F3: clear-and-re-raise cycles per 8 h with the value parked on the yellow limit (real engine, N=3).

Run from the repo root: PYTHONPATH=. uv run python docs/research/scripts/r20_chatter_at_limit.py
"""

import random
from statistics import NormalDist

from gs.alarms.checks import Sample
from gs.alarms.checks.limit import Limit, LimitCheck, deadband
from gs.alarms.contract import Level
from gs.alarms.engine import Slot, _slot_step

HOURS = 8
CASES = {  # shipped mission/limits.yaml config, value parked on the limit, simulator sigma
    "bus_current_a": (dict(yellow_high=8.0, red_high=10.0, hysteresis_abs=0.2), 8.0, 0.05),
    "cabin_temp_c": (dict(red_low=15.0, yellow_low=18.0, yellow_high=27.0, red_high=30.0, hysteresis_pct=3), 27.0, 0.05),
    "cabin_pressure_kpa": (dict(red_low=95.0, yellow_low=98.0, yellow_high=104.0, red_high=106.0, hysteresis_pct=15), 104.0, 0.02),
}

if __name__ == "__main__":
    for name, (cfg, mu, sim_sig) in CASES.items():
        chk = Limit(LimitCheck(type="limit", **cfg))
        d = deadband(chk.cfg)
        pred = d / -NormalDist().inv_cdf((1 / (HOURS * 36000)) ** (1 / 3))
        for mult in (1, 2, 4):
            sig, rng, rtn = sim_sig * mult, random.Random(7), 0
            slot = Slot("limit", Level.NOMINAL, None, (), chk.initial())
            for i in range(HOURS * 36000):
                before = slot.level
                slot, _ = _slot_step(slot, chk, 3, Sample(i, i / 10, rng.gauss(mu, sig), 0.1), False)
                rtn += before > Level.NOMINAL and slot.level == Level.NOMINAL
            print(f"{name} d={d:.3g} sigma={sig:.3g} ({mult}x sim): {rtn} clears in {HOURS} h; "
                  f"chain-predicted sigma for 1 per {HOURS} h = {pred:.3g} ({pred / sim_sig:.1f}x)")
