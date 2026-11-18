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

Verdict (1 epoch, 0 lost, 0 overflow, 0 false raises, no chattering): **PASS**
