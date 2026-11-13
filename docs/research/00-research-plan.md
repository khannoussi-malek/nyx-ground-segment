# Nyx Ground Segment – Research Plan

Gate 1 (Requirements review) prep. Source spec: `../../Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md`.
Goal: answer every open technical question **before** design, so `docs/design.md` decisions cite evidence, not taste.
Each topic produces one note `docs/research/R<n>-<slug>.md` using the template at the bottom. After each batch: `/graphify . --update`.

## Research topics

| ID | Topic | Questions to answer | Drives requirements | Review-board Q |
| --- | --- | --- | --- | --- |
| R1 | Prior art: open-source mission control | How do Yamcs, OpenC3 COSMOS and NASA Open MCT model parameters, limits, staleness, command verification, replay? What vocabulary and patterns are worth borrowing, and what is overkill for 25 h? | ALM-01..07, CMD-01, UI-02, DAT-06 | 1, 3, 4 |
| R2 | Space standards vocabulary | ECSS PUS service 1 (request verification: acceptance/start/progress/completion), service 12 (on-board monitoring), service 5 (events); CCSDS space packet + XTCE. How do QUEUED→SENT→RECEIVED→VERIFIED map onto them? Where would a CCSDS decoder plug in? **Command safety** (gap found by graph v0): two-step confirm + reason for hazardous commands (arm/fire pattern in Yamcs/COSMOS), double-command interlock, what FAILED means for the operator. | CMD-01..05, ALM-*, §3 decoder | 3, 13 |
| R3 | Redis Streams delivery semantics | Consumer groups, XREADGROUP, XACK, PEL, XPENDING, XAUTOCLAIM (min-idle, delivery count), MAXLEN trimming, AOF/RDB persistence and what is lost on restart, client reconnect behaviour. Redis Streams vs Kafka vs NATS JetStream: when to switch. | ING-01, 06, 07, 08, ENG-02 | 7, 8, 10, 11 |
| R4 | Per-vehicle ordering with ≥ 2 workers | Consumer group gives no per-key order. Options: one stream per vehicle + vehicle-to-worker ownership (lease/lock), partition by stream, or stateless workers + ordered state in DB. Which keeps gap/limit/persistence logic correct during a worker crash and reclaim? | ING-04, 06, 07, ALM-02, 03 | 8, 9, 11 |
| R5 | Idempotency, dedup, gaps, sequence resets | Unique key design given `seq` resets on simulator restart (boot epoch? `t_onboard` jump?). Idempotent insert (`ON CONFLICT DO NOTHING`), gap counting across resets, "no alarm duplicated" after reclaim. | ING-03, 04, 05, 07, OPS-4 | 5, 8 |
| R6 | Time-series store | TimescaleDB hypertables, `time_bucket` min/max/avg downsampling, continuous aggregates, retention; vs plain Postgres, InfluxDB, QuestDB. Can one Postgres hold telemetry + alarms + commands + audit? | DAT-03, UI-04, DAT-02 | 5 |
| R7 | Archive and replay | pyarrow Parquet schema, hourly partition layout, S3/MinIO multipart upload atomicity, sha256 sidecar, verify-then-delete pattern, crash mid-upload. Replay engine: read Parquet (pyarrow/DuckDB), re-run same alarm logic at 1×/10×, same limits version. | DAT-04, 05, 06 | 4, 12 |
| R8 | Alarm logic and alarm management | Hysteresis (deadband %), persistence counters, rate-of-change over a window, alarm life cycle per ISA-18.2 / EEMUA 191 (ACTIVE, ACKED, CLEARED, return-to-normal unacked, re-alarm when worse). Strategy/registry pattern for pluggable checks. Limits versioning by file hash. | ALM-01..08 | 2, 6 |
| R9 | Operator console | Live push: WebSocket vs SSE, reconnect + resync, server vs client staleness clock; **STALE/LOS semantics** (gap found by graph v0): per-parameter vs per-vehicle staleness, `freeze` fault (repeated values look fresh), how Yamcs/Open MCT mark expired data. ISA-101 high-performance HMI ("grey when nominal"), text+colour, WCAG AA. Charting at 10 Hz and 24 h windows (uPlot vs Recharts vs ECharts). UTC display. | UI-01..08 | 1 |
| R10 | Latency measurement method | How to measure p95 onboard→stored, frame→alarm, frame→screen honestly (clock source, histogram, Playwright timing), report raw numbers + machine. | ING-09, 10, ALM-05, UI-01, DAT-03 | 11 |
| R11 | Stack and delivery tooling | FastAPI + pydantic v2, redis-py asyncio, psycopg 3 / asyncpg, docker compose healthchecks + `depends_on`, testcontainers vs compose in CI, GitHub Actions service containers, Playwright in CI, mypy strict, ruff. Simple login + audit identity. | ENG-01..10, CMD-04 | 14, 18 |
| R13 | Frame validation + sessions (gap found by graph v1: no topic reached ING-02, DAT-01) | Schema validation with pydantic v2 (strict mode, NaN/inf, unknown fields → A-R6-3), DLQ entry shape with reason; session model (one active per vehicle? overlapping sessions? conductor identity from login). Small — fold into R11 agent. | ING-02, DAT-01 | 5 |
| R12 | Context: the company and role | The Exploration Company / Nyx: what ground and test tooling they likely use, job-posting keywords to mirror in README and answers. | §1, §7 hiring panel | 15–18 |

## Order and batching

1. **Batch A (architecture-critical, parallel):** R3, R4, R5, R6. Decides the core data path.
2. **Batch B (domain, parallel):** R1, R2, R8. Decides the vocabulary and alarm/command model.
3. **Batch C (edges):** R7, R9, R10, R11, R12.
4. `/graphify . --update` after each batch; query the graph for uncovered requirement IDs before Gate 2.

## Exit check (Gate 1)

- Every requirement ID in spec §4 is reached by ≥ 1 research note **or** marked "no research needed" in `docs/assumptions.md`.
- Each note ends in a **decision candidate** with the alternative it beat.
- Open questions collected into `docs/assumptions.md`.

## Note template

```markdown
# R<n> – <topic>

**Drives:** GS-XXX-NN, ...   **Answers review Q:** n, ...

## Question
## Findings
- claim — [source](url)
## Decision candidate
Chosen: ... Because: ... Rejected: ... because ...
## Risks / what would make us switch
## Open questions → assumptions.md
```
