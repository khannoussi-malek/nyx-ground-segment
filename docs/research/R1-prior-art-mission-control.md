# R1 – Prior art: open-source mission control systems

**Drives:** GS-ALM-01, 02, 03, 06, 07, GS-CMD-01..05, GS-UI-02, 03, 05, 06, GS-DAT-02, GS-DAT-06   **Answers review Q:** 1, 3, 4, 15

Handoffs: PUS/CCSDS/XTCE vocabulary and command-safety detail → **R2**. Hysteresis, persistence maths and the ISA-18.2 state machine → **R8**. Server vs client staleness clock, WebSocket/SSE, chart library → **R9**. Parquet replay engine → **R7**.

## Question

How do Yamcs, OpenC3 COSMOS and NASA Open MCT model parameters, limits, staleness, command verification, replay and
operator display? Which of their concepts should a 25 h, one-engineer build copy, change or leave out? And why build at all
instead of deploying one of them (Q15)?

Versions checked (2026-11-13): Yamcs **5.14.0** (2026-10-31, AGPL-3.0, Java) ([releases](https://github.com/yamcs/yamcs/releases)),
OpenC3 COSMOS **v7.5.1** (2026-10-30, "OpenC3 Builder's License V2") ([releases](https://github.com/OpenC3/cosmos/releases), [LICENSE.md](https://github.com/OpenC3/cosmos/blob/main/LICENSE.md)),
Open MCT **4.3.1** (2026-08-31, Apache-2.0, Vue 3.4) ([releases](https://github.com/nasa/openmct/releases), [package.json](https://github.com/nasa/openmct/blob/master/package.json)).
F´ GDS (Apache-2.0, Python) was looked at only for its licence and is **not covered**. It is tied to F´ flight software dictionaries, so it is a poor fit for a JSON-frame test bench.
No PaperLab paper was needed: the official docs and source answer every question.

## Findings

### 1. Architecture

**Yamcs**
- Runs as "a single Java process" with an embedded Netty HTTP server. One server hosts several **instances**, one per payload or satellite — [architecture](https://docs.yamcs.org/yamcs-server-manual/general/architecture/)
- **Data links** connect to targets. Internal **streams** carry tuples from producers to consumers. **Processors** turn packets into parameters, check limits and handle commands. One processor runs realtime, and others are "created on demand for replays" — [architecture](https://docs.yamcs.org/yamcs-server-manual/general/architecture/)
- Storage is a **Stream Archive** written in realtime, plus a **Parameter Archive** that is "not realtime" and is built by regular replays from the Stream Archive. **Buckets** hold general objects — [architecture](https://docs.yamcs.org/yamcs-server-manual/general/architecture/)
- The storage engine is RocksDB "tablespaces", with optional time partitions in sub-directories — [RdbStorageEngine javadoc](https://docs.yamcs.org/javadoc/yamcs/latest/org/yamcs/yarch/rocksdb/RdbStorageEngine.html), [Tablespace javadoc](https://docs.yamcs.org/javadoc/yamcs/latest/org/yamcs/yarch/rocksdb/Tablespace.html)
- There is no external broker. Everything is in-process.

**OpenC3 COSMOS (Core)**
- A Docker Compose system with these services: `operator` (runs interface and target microservices as processes and restarts them if they die), two Rails APIs, `traefik`, and two Valkey/Redis stores. **redis** holds config and the current value table; **redis-ephemeral** holds "real-time data streams". `buckets` (S3-compatible, Versitygw) holds "text logs, and binary logs of all raw data". `tsdb` (QuestDB) holds "all decommutated command and telemetry data" — [architecture](https://docs.openc3.com/docs/getting-started/architecture) ([source](https://github.com/OpenC3/cosmos/blob/main/docs.openc3.com/docs/getting-started/architecture.md))
- Release v7.5.0 migrated object storage from MinIO to Versitygw — [v7.5.0 notes](https://newreleases.io/project/github/OpenC3/cosmos/release/v7.5.0)
- **This is almost exactly our stack:** broker = Redis Streams, raw archive = S3 buckets, hot queries = a time-series DB, frontend = Vue + Astro UX.

**NASA Open MCT**
- A browser **framework**, not a ground system. It has no ingest, no storage and no command path. You plug in "telemetry providers" that supply "historical and real-time telemetry" from your own backend — [API.md › Telemetry Providers](https://github.com/nasa/openmct/blob/master/API.md#telemetry-providers)

### 2. Parameter model, limits, staleness

- **Yamcs keeps two separate fields on every value.** `AcquisitionStatus` is `ACQUIRED | NOT_RECEIVED | INVALID | EXPIRED`, where EXPIRED means "coming from a packet which has not since updated although it should have been". `MonitoringResult` is `DISABLED | IN_LIMITS | WATCH | WARNING | DISTRESS | CRITICAL | SEVERE`, and `RangeCondition` is `LOW | HIGH`. Each value also carries `expireMillis` — [pvalue.proto](https://github.com/yamcs/yamcs/blob/master/yamcs-api/src/main/proto/yamcs/protobuf/pvalue/pvalue.proto)
- Yamcs on expiry: "parameters are expected to be sampled regularly, they also have an expiration time … After the time is exceeded, the parameters become expired … the state of the remote device is considered unknown" — [model](https://docs.yamcs.org/yamcs-server-manual/general/model/)
- How Yamcs computes expiry: from the MDB "expected rate in stream" of the packet, times `expirationTolerance` (example value 1.9) in `processor.yaml` — [processor configuration](https://docs.yamcs.org/yamcs-server-manual/processors/processor-configuration). **VERIFIED**: "rate in stream defined in the Mission Database multiplied by this configuration option … Default: `1.9`" (yamcs `processor-configuration.rst` l. 124–129); code `expireMillis = expirationTolerance * ris.getMaxInterval()` (`SequenceContainerProcessor.java:39`).
- Yamcs alarm ranges: 5 levels (watch, warning, distress, critical, severe) with low/high triggers that can be inclusive or exclusive. `minViolations` (default 1) only controls **event generation**, not the monitoring result. Context-dependent alarm sets are evaluated top to bottom — [MDB alarms](https://docs.yamcs.org/yamcs-server-manual/mdb/loaders/sheet/alarms/)
- COSMOS `LIMITS <set> <persistence> <ENABLED|DISABLED> red_low yellow_low yellow_high red_high [green_low green_high]`. Persistence is the "number of consecutive times the telemetry item must be within a different limits range before changing limits state". On boundaries: "A value exactly equal to a limit is placed in the outer (more severe) state". A `DEFAULT` set is mandatory, and only one set is active at a time — [telemetry config](https://docs.openc3.com/docs/configuration/telemetry), [Limits Monitor](https://docs.openc3.com/docs/tools/limits-monitor)
- COSMOS has a single limits enum that includes `STALE`: `RED, RED_HIGH, RED_LOW, YELLOW…, GREEN…, BLUE, STALE, None` — [packet_item_limits.py](https://github.com/OpenC3/cosmos/blob/main/openc3/python/openc3/packets/packet_item_limits.py). A packet is marked stale when it has not been received for `System.staleness_seconds` — [OpenC3 5.0.10 rubydoc](https://www.rubydoc.info/gems/openc3/5.0.10/OpenC3/Telemetry:check_stale). **CONTRADICTED** (setting name): `System.staleness_seconds` is Ball COSMOS v4. OpenC3 v7.5.1 checks staleness when data is read, with a per-call `stale_time:` (default 30 s) (`cvt_model.rb:151,187-188`, `tlm_api.rb`).
- Open MCT limits: `getLimitEvaluator(domainObject)` returns an evaluator with `evaluate(datum)` → `{cssClass, name}` (e.g. `'is-limit--upr is-limit--red'`, `'Red High'`) and `getLimits()` — [API.md](https://github.com/nasa/openmct/blob/master/API.md), [SinewaveLimitProvider.js](https://github.com/nasa/openmct/blob/master/example/generator/SinewaveLimitProvider.js)
- Open MCT staleness is a separate provider API. `supportsStaleness`, `isStale(obj)` and `subscribeToStaleness(obj, cb)` deliver `{isStale, timestamp}` — [TelemetryAPI.js](https://github.com/nasa/openmct/blob/master/src/api/telemetry/TelemetryAPI.js). The example provider computes staleness from the last received datum, with `stalenessInterval: 3000` ms — [ExampleStalenessProvider.js](https://github.com/nasa/openmct/blob/master/example/exampleStalenessProvider/ExampleStalenessProvider.js)

**How stale values are rendered**
- **Yamcs web:** the value turns *italic*, gets a clock icon with an "expired" tooltip, and shows ↓/↑ glyphs for low/high range — [parameter-status component html](https://github.com/yamcs/yamcs/blob/master/yamcs-web/src/main/webapp/projects/webapp-sdk/src/lib/components/parameter-status/parameter-status.component.html) / [css](https://github.com/yamcs/yamcs/blob/master/yamcs-web/src/main/webapp/projects/webapp-sdk/src/lib/components/parameter-status/parameter-status.component.css)
- **Open MCT:** a stale cell gets the stale background plus *italic*. A stale frame (plot or layout) gets a 2 px stale-coloured border plus a stale glyph — [_mixins.scss](https://github.com/nasa/openmct/blob/master/src/styles/_mixins.scss) (`isStaleElement`, `isStaleHolder`)
- **COSMOS:** a dashboard health banner shows Nominal / Caution / Critical / **Stale** "when telemetry stops arriving" — [dashboards](https://github.com/OpenC3/cosmos/blob/main/docs.openc3.com/docs/tools/dashboards.md). Packet Viewer has a configurable "refresh and stale interval" and colours values blue/green/yellow/red — [Packet Viewer](https://docs.openc3.com/docs/tools/packet-viewer)

### 3. Command model

- **Yamcs flow:** queue selection by permission, significance and name pattern. Queues are DISABLED, BLOCKED or ENABLED. Command-history acks are 'Q' queued, 'R' released and 'S' sent (the link writes 'S') — [command processing](https://docs.yamcs.org/yamcs-server-manual/processors/command-processing/)
- **Yamcs verifiers:** stage names are free text (XTCE examples: Received, Accepted, Execution, Complete…). Each verifier has a check window `start,stop` ms, measured from **LastVerifier** (the default) or **CommandRelease**. "If the previous verifier timed out, this one also times out without checking." Each verifier has `OnSuccess`, `OnFail` and `OnTimeout` set to `SUCCESS | FAIL | none`. A container-type verifier can only return OK or timeout — [command verification](https://docs.yamcs.org/yamcs-server-manual/mdb/loaders/sheet/command-verification/)
- Yamcs on significance: "Some commands can have an elevated significance, which may mean that a special privilege or an extra confirmation is required" — [model](https://docs.yamcs.org/yamcs-server-manual/general/model/)
- **COSMOS hazardous commands:** `HAZARDOUS` on a command, or on one of its parameter states, makes Command Sender ask for confirmation before sending — [command config](https://docs.openc3.com/docs/configuration/command), [Command Sender](https://docs.openc3.com/docs/tools/cmd-sender). Enterprise **Critical Commanding** "requires a different user to approve" HAZARDOUS and RESTRICTED commands — [command config](https://docs.openc3.com/docs/configuration/command)
- **COSMOS validators:** a `VALIDATOR` class with `pre_check` and `post_check`, both given the sent packet. Command History (Enterprise) lists Time, User, Command, Result (from the validator) and Description. It shows the last hour and then streams new commands — [command config](https://docs.openc3.com/docs/configuration/command), [Command History](https://github.com/OpenC3/cosmos/blob/main/docs.openc3.com/docs/tools/command-history.md)
- COSMOS command queues (Enterprise) have Hold, Release and Disable modes — [Command Queue](https://github.com/OpenC3/cosmos/blob/main/docs.openc3.com/docs/tools/command-queue.md)

### 4. Replay and archive retrieval

- **Yamcs** replays with a separate on-demand processor next to the realtime one. Packets go through the same processor pipeline, which "monitor[s] limits" — [architecture](https://docs.yamcs.org/yamcs-server-manual/general/architecture/), [replay service](https://docs.yamcs.org/yamcs-server-manual/processors/replay-service/). Yamcs Studio's speed button cycles through 5 steps from 1× up to 16× — [Studio archive](https://docs.yamcs.org/yamcs-studio/display-runner/archive/). **VERIFIED** that they are **not** guaranteed (from source, not docs): each replay runs its own processor that loads the current MDB (`Processor.java:158`); runtime limit changes stay in one processor (`ProcessorData.java:77`). Limits come from the current MDB, not a stored version.
- **COSMOS Data Viewer** plays logged data from Start to End, then "continues in real time if the End time is in the future or is blank" — [Data Viewer](https://github.com/OpenC3/cosmos/blob/main/docs.openc3.com/docs/tools/data-viewer.md). **Data Extractor** writes chosen items over any time range to CSV — [Data Extractor](https://github.com/OpenC3/cosmos/blob/main/docs.openc3.com/docs/tools/data-extractor.md)
- **Open MCT** historical access is `request(obj, {start, end, domain})`, a promise for an array of data. Realtime is `subscribe(obj, cb)`, which returns an unsubscribe function. On a `boundsChanged` event with `tick=true` "it's unlikely that you would need to perform a historical data query"; with `tick=false` a re-query may be needed — [API.md](https://github.com/nasa/openmct/blob/master/API.md)

### 5. UI patterns worth copying

- **Text as well as colour:** Yamcs pairs italic, a clock icon and ↑/↓ glyphs; Open MCT pairs italic with a glyph. Both give a non-colour cue, which is what GS-UI-03 asks for.
- **Alarm list** (COSMOS Limits Monitor): out-of-limit items "continue to be shown until explicitly dismissed", next to a searchable log of limits events — [Limits Monitor](https://docs.openc3.com/docs/tools/limits-monitor)
- **Command history:** a table of time, user, command, result and reason, showing the last hour and then live. Yamcs adds one ack column per stage (Q/R/S plus verifier stages), which is the per-state timeline GS-UI-06 asks for.
- **Open MCT provider split:** realtime, historical, limits and staleness are four separate small interfaces. A React console can copy this shape as hooks without using Open MCT.

## Decision candidate

| Concept (source) | Verdict | Why (25 h lens) | Req |
| --- | --- | --- | --- |
| Separate **acquisition status** from **monitoring result** (Yamcs `AcquisitionStatus` vs `MonitoringResult`) | **Adopt** | A value can be RED *and* STALE. One merged enum (COSMOS puts STALE among the colours) loses the last known limit state during LOS. Use two fields: `freshness: FRESH/STALE/NO_DATA` and `limit: NOMINAL/YELLOW_LOW/…/RED_HIGH` | UI-02, ALM-01, Q1 |
| Expiry from expected rate × tolerance (Yamcs) | **Adapt** | The spec fixes 2 s, which is 20 frame periods at 10 Hz. Keep one config value `stale_after_s: 2.0`, not per-packet rates | UI-02 |
| Boundary goes to the outer, more severe state (COSMOS) | **Adopt** | Gives the "exactly on limit" test of ALM-01 a published precedent | ALM-01 |
| `red_low, yellow_low, yellow_high, red_high, persistence` per item (COSMOS LIMITS) | **Adopt** (+ hysteresis %, rate-of-change from spec) | Same shape as `limits.yaml` in spec §3. Note the difference: Yamcs `minViolations` gates only the event, while COSMOS persistence gates the state. R8 decides | ALM-01, 03 |
| Limit **sets** (COSMOS) / context-dependent alarms (Yamcs) | **Skip** | One set, versioned by file hash, is enough for a test bench | ALM-08 |
| 5 severity levels (Yamcs) | **Skip** | The spec has two levels (yellow/red) | ALM-01 |
| Alarm stays until **acked** even after return to normal; ack + RTN → cleared (Yamcs, ISA-18.2) | **Adapt** | Fits ACTIVE→ACKED→CLEARED. The "RTN before ack" state goes to R8 | ALM-06, Q2 |
| Shelving / ignore / hide (COSMOS, Yamcs) | **Skip** | Nice to have, but adds UI and audit paths. List it as a next step | – |
| Check-type registry (COSMOS `LIMITS_RESPONSE` class, Open MCT `addProvider`) | **Adopt** pattern | Register a check class by name, so the worker never changes | ALM-07 |
| Verifier stages with check windows; LastVerifier chaining; OnTimeout → FAIL (Yamcs) | **Adapt** | Fixed two-stage chain: RECEIVED (window 5 s from SENT) then VERIFIED (15 s). A timeout of either → FAILED with the stage name in the reason. No user-defined stages | CMD-01, 02, Q3 |
| Command-history acks per stage with timestamps (Yamcs Q/R/S) | **Adopt** | One append-only row per transition. It drives both the audit log and the timeline | CMD-01, 04, UI-06 |
| `HAZARDOUS` flag → confirmation dialog (COSMOS) | **Adopt** + add mandatory reason | Matches `commands.yaml` `hazardous: true`. COSMOS only asks yes/no; the spec also wants a reason | CMD-03 |
| Second-user approval, Critical Commanding (COSMOS Enterprise) | **Skip** | Needs roles. Document as a production next step (R2) | CMD-03, Q14 |
| `pre_check` / `post_check` validator (COSMOS) | **Adapt** | `pre_check` = one guard function (hazardous reason + 2 s double-command interlock). `post_check` = verifier above. No plugin class | CMD-03, 05 |
| Command queues hold/release (Yamcs, COSMOS) | **Skip** | Not in spec. The FIFO stream `tc:<vehicle>` is the queue | – |
| Redis streams + S3 log + TSDB split (COSMOS) | **Adopt** as evidence | An independent production system chose the same three tiers. Cite it in design.md | ING-01, DAT-03/04, Q7 |
| Current value table in Redis (COSMOS) | **Adapt** | Latest value per parameter gives a snapshot on WebSocket (re)connect before deltas. R9 decides | UI-07 |
| Replay through the **same processor** as live (Yamcs replay processor) | **Adopt** | Same alarm code + **stored limits version** (Yamcs uses the current MDB, so we go one step further) → identical alarms | DAT-06, Q4 |
| Replay that continues into live (COSMOS Data Viewer) | **Skip** | Replay is a separate, clearly marked REPLAY session | DAT-06 |
| CSV export of items over a range (COSMOS Data Extractor) | **Adopt** (minimal) | Session report CSV | DAT-02 |
| Open MCT provider API: `request(start,end)` + `subscribe(cb)→unsubscribe` + `subscribeToStaleness` + limit evaluator `{cssClass,name}` | **Adapt** the shape, **skip** the framework | Open MCT is Vue 3 and the spec mandates React (ENG-10). It also brings a plugin and object model that costs hours. Copy the contract as React hooks (`useTelemetry`, `useStaleness`) and the `tick` rule (no re-query on live ticks) | UI-01, 02, 04 |
| Stale render = italic + icon + "STALE" text; ↑/↓ for high/low (Yamcs, Open MCT) | **Adopt** | Non-colour cue for UI-03. Do **not** copy COSMOS green-for-nominal: the spec says grey when nominal | UI-02, 03 |
| Alarm list sticky until acked + searchable event log (COSMOS Limits Monitor) | **Adopt** | Directly GS-UI-05 | UI-05 |

**Chosen:** a custom build that borrows Yamcs's *vocabulary and value model* (acquisition vs monitoring, verifier stages and
windows, ack-before-clear), COSMOS's *configuration shape and stack layout* (LIMITS fields, HAZARDOUS, Redis/S3/TSDB) and
Open MCT's *client contract* (historical request + live subscribe + staleness), without running any of them.
**Rejected:** (a) deploy Yamcs. JVM + RocksDB + XTCE MDB replaces our Redis/Postgres/MinIO path, so most ING/DAT requirements would become "configure Yamcs" and nothing would be demonstrated. (b) Deploy COSMOS. Its licence limits hosted and commercial redistribution, it is Ruby/Rails + Vue (the spec mandates Python + React), and history, queues and critical commanding are Enterprise. (c) Embed Open MCT as the console. It is Vue-based, contradicts ENG-10, and costs hours learning the framework.

### Q15 – "Why build this instead of deploying Yamcs or COSMOS?" (honest answer)

> This is a practice exercise that mirrors the job posting, so building the core is the point: it shows ingest semantics,
> alarm logic, command verification and archive integrity, and these are the things the role asks for. On a real
> programme I would **start with a make-or-buy trade**: Yamcs (AGPL, mature, ESA-style vocabulary) and COSMOS (Redis +
> buckets + TSDB, the same shape as ours) both cover most of this spec out of the box, and the licence, support and
> qualification cost would decide. I studied both and copied their concepts on purpose, not by accident. What the custom
> build gives: it fits the existing Redis/Postgres/MinIO stack and Python skill set, it has a small surface I can explain
> line by line in this review, every requirement maps to my own test, and I learn the domain properly. What it costs:
> no XTCE/CCSDS, no scripting, no mature UI. I would not fly with it without that trade study.

## Risks / what would make us switch

- **Scope creep from prior art.** Each "adopt" row must map to a requirement ID. Shelving, limit sets, queues and multi-level severity stay out.
- **Real CCSDS/XTCE arrives.** If the team brings an XTCE MDB, Yamcs's MDB loader becomes worth more than our YAML. Switch trigger: more than 1 packet type or more than ~100 parameters (R2).
- **Licence assumptions.** The COSMOS licence was read only from LICENSE.md; get a legal read before any reuse. Yamcs is AGPL, so modifying and hosting it means publishing the source.
- **Mapping verifier windows wrongly.** Whether 15 s is measured from SENT or from RECEIVED changes FAILED behaviour (see open question).

## Open questions → assumptions.md

1. Is the CMD-02 15 s verification window measured from SENT (Yamcs `CommandRelease`) or from RECEIVED (Yamcs `LastVerifier`, the default)? Proposal: from SENT, so the worst case is 15 s total.
2. If RECEIVED times out, is VERIFIED still watched (late echo in `last_cmd_id`)? Yamcs says no ("also times out without checking"). Proposal: FAILED is final, but a late echo is logged as a `LATE_VERIFICATION` event for Q3.
3. During STALE, does the row keep its last limit colour (Yamcs and Open MCT keep it and add stale styling) or go neutral? Proposal: keep the limit state as text, muted, with a STALE tag (R9).
4. Alarm that returns to normal before ack: keep ACTIVE-unacked (Yamcs) or add an RTN_UNACKED state? → R8.
5. Is per-vehicle LOSS OF SIGNAL "all parameters stale", or "no frame for 2 s"? These are the same for one frame type; R9 confirms.


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Yamcs expiry = rate in stream × `expirationTolerance` | VERIFIED | docs + source | D-UI-05/06 (prior art) |
| COSMOS stale setting `System.staleness_seconds` in v7 | CONTRADICTED (name) | OpenC3 source: per-call `stale_time`, default 30 s | none (prior art only) |
| Yamcs replay alarms not guaranteed identical to live | VERIFIED | Yamcs source | D-REP-01/05 (why we record) |

CONTRADICTED: COSMOS stale setting `System.staleness_seconds` in v7.
