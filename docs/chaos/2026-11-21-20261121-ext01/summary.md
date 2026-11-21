# Chaos soak, seed 20261121, 30 min, started 2026-11-21 12:29:56Z

**PASS**

Properties: P1-P7 run in `tests/property` (P6, the derived evaluator, included).

I4 (console watcher): PASS (7473 samples, 136809 OK rows, longest stretch with no parameter row on screen 0.0 s)

| Invariant | Result | Samples | Skipped | First failure |
|---|---|---|---|---|
| I1 | PASS | 113 | 0 |  |
| I2 | PASS | 113 | 0 |  |
| I3 | PASS | 361 | 0 |  |
| I5 | PASS | 16 | 0 |  |
| I6 | PASS | 113 | 0 |  |
| I7 | PASS | 360 | 0 |  |
| I8 | PASS | 360 | 0 |  |

- Recovery after each action (I5): max 8.2 s over 16 actions
- RAISEs 12, false raises 0, chattering: none
- Peak new alarms in 10 min: {'all': 5, 'NYX-TA1': 4, 'NYX-TA2': 2}; time in flood: 0 s

- faults: {'dropout': 6, 'duplicate': 7, 'freeze': 7, 'hold_exec': 6, 'leak': 6, 'malformed': 6, 'overheat': 6, 'pause': 6, 'seq_jump': 7}
- actions: {'api_restart': 2, 'broker_restart': 2, 'partition_db': 2, 'partition_redis': 2, 'pg_readonly': 2, 'sim_restart': 2, 'worker_hang': 2, 'worker_kill': 2}
- operator: {'acks': 25, 'failed': 14, 'isolate_valve sent': 56}

Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64

## Start-up repairs

- none

## End repairs

- none

Images:
  - api sha256:77e4d74897976ad1ab5290d513855d7965a38f91eebacbf5e4bc0cbef4b25ce4
  - archiver sha256:77e4d74897976ad1ab5290d513855d7965a38f91eebacbf5e4bc0cbef4b25ce4
  - db sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - ingest-1 sha256:77e4d74897976ad1ab5290d513855d7965a38f91eebacbf5e4bc0cbef4b25ce4
  - ingest-2 sha256:77e4d74897976ad1ab5290d513855d7965a38f91eebacbf5e4bc0cbef4b25ce4
  - minio sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - redis sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - simulator sha256:77e4d74897976ad1ab5290d513855d7965a38f91eebacbf5e4bc0cbef4b25ce4
