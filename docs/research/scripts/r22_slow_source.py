from gs.alarms.checks import Sample
from gs.alarms.checks.limit import Limit, LimitCheck
from gs.alarms.contract import Level
from gs.alarms.engine import Slot, _slot_step
from gs.ingest.link import STALE_S
chk = Limit(LimitCheck(type="limit", yellow_high=27.0, red_high=30.0, red_low=15.0, yellow_low=18.0, hysteresis_pct=3))
for period in (0.1, 1.0, 3.0):
    slot, prev_t = Slot("limit", Level.NOMINAL, None, (), chk.initial()), None
    for i in range(20):  # 20 frames, all at 35.0 (RED)
        t = i * period
        reset = prev_t is not None and t - prev_t > STALE_S  # link.on_frame's data-STALE rule
        slot, _ = _slot_step(slot, chk, 3, Sample(i, t, 35.0, period), reset); prev_t = t
    print(f"period {period:>4} s: 20 frames at 35.0 (RED) -> debounced level {slot.level.name}")
