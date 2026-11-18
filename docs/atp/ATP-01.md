# ATP-01: nominal run (OPS-1), 2 h, zero frames lost
Covers ING-01, ING-09, UI-01, DAT-01, DAT-02, ALM-02, ALM-03. Nothing is injected: every alarm raised during the run is a false raise.

1. `make up`. Leave the console alone for the 2 h; no one starts a session by hand, because a vehicle has one active session and `make soak` needs both.
2. Run `make soak`. It logs in as `conductor` (password from `.env`, never printed), starts a session on NYX-TA1 and one on NYX-TA2, waits 2 h, stops both, waits for **Report pending** to clear and downloads both CSVs. It then compares, per vehicle and `(epoch, seq)`, frames sent against frames stored, counts GAP events and the simulator `overflow` counter (from its `/healthz`), and runs the alarm-quality metrics over the window with no fault windows.
3. Read `docs/perf/soak-<UTC date>/summary.md`. Pass: 0 lost frames for both vehicles, 0 false raises, no chattering (no alarm raising and clearing repeatedly), simulator overflow 0, both reports complete (no "incomplete: n of m frames"), every time ending in `Z`.
4. A simulator restart during the soak shows as a new epoch (SEQ_RESET) in the summary: treat that run as INCOMPLETE (its simulator overflow count is unreliable) and rerun it.
5. Record the result and the CSVs in `docs/atp-report.md` (Gate 5).
