# ATP-01 soak 2026-11-17 (ING-01, D-ALM-15)

2 h, sessions on NYX-TA1, NYX-TA2, nothing injected (every RAISE is a false raise). Reports: `report-<vehicle>.csv`.

| Vehicle | Session | Frames stored | Lost (sent - stored) | GAP events |
|---|---|---|---|---|
| NYX-TA1 | 354 | 72001 | 5 | 0 |
| NYX-TA2 | 355 | 72002 | 6 | 0 |

- Simulator overflow (frames dropped during the soak): 0
- RAISEs: 0, false raises: 0
- Chattering (psi >= 0.05 or alarm < 20 s): none
- Peak new alarms in 10 min: {'all': 0}, time in flood: 0 s

Verdict (0 lost, 0 overflow, 0 false raises, no chattering): **FAIL**

## Re-score with the fixed counter (2026-11-17)

The FAIL above is a counting defect, not lost frames: every frame sent (72001 on NYX-TA1, 72002 on NYX-TA2) is stored, none missing on either side. At 21:41:14Z the stack stalled for about 9 s and the onboard clock stepped back 1.6 s (seq 49289 is stamped before 49288). The counter ordered frames by `t_onboard`, read the step as a sequence reset and counted 5 and 6 stored frames as lost. It now splits epochs in send order (the stream's entry IDs only rise); `test_onboard_clock_step_back_is_not_a_reset` pins it.

| Vehicle | Session | Frames stored | Lost (re-scored) | GAP events |
|---|---|---|---|---|
| NYX-TA1 | 354 | 72001 | 0 | 0 |
| NYX-TA2 | 355 | 72002 | 0 | 0 |

- The run was not on an idle machine: other commands ran during it, and the stalls at 21:02-21:04Z and 21:41Z show as LOS/AOS pairs (8 per vehicle, nothing injected). The system stored every frame through them.
- Not an acceptance result: ATP-01 is run again at Gate 5 from a fresh clone, on an idle machine, with this counter.
- Bus current noise over the window: sigma = 0.050 A on both vehicles (A-R20-2).
