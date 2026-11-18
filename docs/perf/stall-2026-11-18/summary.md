# Stall recovery, 2026-11-18 (NCR-01 investigation)

Question: after a stall of the whole stack, how long until the ING-10 lag is back under 1 s, and does anything in ingest slow the recovery?

## Method
Fresh stack (separate checkout of the Gate 5 branch at 6d8c9db, same machine: Apple M4 Pro, 12 cores, 24 GB, macOS 27.2.1, Docker Desktop). 2 vehicles × 100 Hz (`perf.run_p.set_sim_rate(100)`); lag sampled at 1 Hz with run P's `TIME_LAG_SQL` (max over both vehicles). Per trial: 60 s steady, `docker compose pause` of all 8 services for 10 s (every container frozen, as in a VM stall), `unpause`, 60 s of samples. A second probe sampled every 100 ms (Redis `XINFO GROUPS` lag, pending, entries-read, and the database time lag) on one vehicle.

## Result (unchanged code)
| Trial | Peak lag | Unpause → lag < 1 s and stays |
|---|---|---|
| 1 | 10.24 s | 2.48 s |
| 2 | 10.58 s | 3.53 s |
| 3 | 10.53 s | 3.46 s |

1 Hz sampling, so ± 1 s; the 100 ms probe puts the recovery at about 1.9 s after unpause. The peak is the pause itself. A first extra trial gave 10.56 s / 3.50 s.

## What the recovery is made of
- On unpause both ingest workers log `redis unavailable for tm:NYX-TA9 (Timeout reading from redis:6379); retry in 0.5s` (the 5 s socket timeout elapsed during the pause) — the same line as in the Gate 5 run P — and reconnect after the 0.5 s backoff. No ownership steal, hold-off or database pool wait appears in the logs.
- The simulator was frozen too and, by design (D-SIM-01: stalls are caught up on a fixed grid), sends the 10 s it missed as a burst of about 1000 frames per vehicle. The lag (`now − max(t_onboard)`) stays high while ingest drains that burst at about 600–800 entries/s per vehicle, then drops to about 0.
- So recovery ≈ backlog / (ingest capacity − live rate) + 0.5 s. At 100 Hz a worker uses about a third of a core; draining the burst uses about one core.
- Capacity check: with both ingest workers capped at 0.3 CPU (`docker update --cpus 0.3`, a stand-in for a starved host), the same pause took about 3.5 s, and at about 0.25 core per worker a 1000-frame backlog takes about 10 s. That matches the Gate 5 run P under load average 28 (lag still rising about 12 s after the sampler resumed).

## Conclusion
No ingest defect: on a host with free CPU the stack is back under 1 s within 2.5–3.5 s of a 10 s freeze. On an oversubscribed host the recovery is bound by the CPU ingest gets; only a cheaper per-frame path would shorten it. Not changed: the 0.5 s reconnect backoff (at most 0.4 s to gain on a free host, nothing under load).
