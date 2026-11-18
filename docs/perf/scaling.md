# Scaling analysis (D-PERF-09)

Extrapolated linearly from run P (2 vehicles x 100 Hz, 300 s) to 10 vehicles x 100 Hz = 1000 frames/s. No per-stage instrumentation (SR-06).

| Quantity | At 1000 frames/s |
|---|---|
| Worker CPU | 228 % of one core (capacity 439 frames/s) |
| Redis growth | -0.8 MB/hour |
| Ingest time p95 | 16.7 ms |

## Headroom

Headroom: a restarted worker is not fast enough to catch up with the missed and ongoing load: 30 s of backlog needs never (limit 60 s). If capacity does not exceed the load the backlog never clears (metastable failure, Huang et al. 2022 p. 7): add workers.

Model: backlog = 30 s of 1000 frames/s; capacity = one worker at 100 % of a core; catch-up = backlog / (capacity - load); metastable when capacity <= load.

## First bottleneck and fix

1. The engine per vehicle is single-threaded: split it by parameter group.
2. The UI coalesces to 10 Hz renders so the browser keeps up.
3. Storage: 1 min rollups at ~22 GB/day raw.
