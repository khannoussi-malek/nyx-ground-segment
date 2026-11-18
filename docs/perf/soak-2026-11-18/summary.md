# ATP-01 soak 2026-11-18 (ING-01, D-ALM-15)

2 h, sessions on NYX-TA1, NYX-TA2, nothing injected (every RAISE is a false raise). Reports: `report-<vehicle>.csv`.

| Vehicle | Session | Frames stored | Lost (sent - stored) | GAP events | Epochs | SEQ_RESET |
|---|---|---|---|---|---|---|
| NYX-TA1 | 1 | 72000 | 0 | 0 | 1 | 0 |
| NYX-TA2 | 2 | 72000 | 0 | 0 | 1 | 0 |

- Simulator overflow (frames dropped during the soak): 0
- RAISEs: 0, false raises: 0
- Chattering (psi >= 0.05 or alarm < 20 s): none
- Peak new alarms in 10 min: {'all': 0}, time in flood: 0 s

Context: same laptop as the other runs (Apple M4 Pro, 12 cores). The soak started at 17:45:08Z and ended about 19:45Z, right after the host-load run P (1-min load average 6.81 after that run; the soak itself recorded no load). Its pass checks do not depend on latency.

- Link events: NYX-TA2 LOS 17:56:31.16Z, AOS 17:56:33.39Z (2.2 s, 0 frames lost); NYX-TA1 LOS 17:56:28.45Z, AOS 17:56:28.55Z (0.1 s) and LOS 17:56:31.16Z, AOS 17:56:33.40Z (2.2 s, 0 frames lost). A host stall: the simulator's Redis reads timed out in the same minute (NCR-01). The console showed LOSS OF SIGNAL; the verdict below counts frames, not link state.
- `expected` in the report CSVs (TA1 71877, TA2 71900) is the duration divided by the measured period, so `n` 72000 above it is not an excess of frames.

Verdict (1 epoch, 0 lost, 0 overflow, 0 false raises, no chattering): **PASS**
