# R2 – Space standards vocabulary + command safety

**Drives:** GS-CMD-01..05, GS-UI-06, GS-ALM-01..06 (vocabulary only), §3 command/telemetry/decoder, OPS-2, OPS-5   **Answers review Q:** 3, 13

Handoffs: survey of Yamcs/COSMOS features and licences → R1 (not redone here). Alarm logic → R8. Audit identity / login → R11. Seq/epoch key → R5 (its UNVERIFIED PUS item is answered below).

## Question

1. How do PUS-C service 1 reports and Yamcs command-history stages map onto QUEUED → SENT → RECEIVED → VERIFIED / FAILED? Is "VERIFIED when `cmd_id` shows up in `last_cmd_id`" a telemetry-based verifier? What does each timestamp mean?
2. What does FAILED mean for the operator (Q3)? How do we tell "rejected" from "no answer, outcome unknown"? What happens if verification arrives after FAILED?
3. What do PUS service 5 (events) and service 12 (on-board monitoring) give us as vocabulary for GAP / SEQ_RESET and ground limits?
4. CCSDS Space Packet header, time in the secondary header (R5 item), XTCE vs our YAML files, a pluggable decoder (Q13).
5. Command safety: two-step confirm + reason, enforced on the server; double-command interlock; audit log content.

## Findings

**PUS-C service 1 (ST[01] request verification)** — all from [ECSS-E-ST-70-41C, 15 Apr 2016](https://ecss.nl/wp-content/uploads/2016/06/ECSS-E-ST-70-41C15April2016.pdf)
- ST[01] covers "checking that a request received on-board has not been corrupted", destination availability, and "reporting the success or failure of these checks". It has three subservices: routing, acceptance, and execution reporting (§6.1.1.1).
- The report types are: TM[1,1] successful / TM[1,2] failed acceptance (§6.1.4.2–3), TM[1,3]/[1,4] start of execution (§6.1.5.1), TM[1,5]/[1,6] progress (§6.1.5.2), TM[1,7]/[1,8] completion (§6.1.5.3) and TM[1,10] failed routing (§6.1.3.3).
- A failed report carries "a failure code" plus "auxiliary data … to explain the reason". A request that fails acceptance is discarded (§6.1.4.3c, f).
- **Success reports are optional and failure reports are mandatory.** Each TC has 4 acknowledgement flags (acceptance, start, progress, completion). A successful stage "is only reported if that flag indicates such reporting need", while "each failed acceptance is reported" (§5.4.11.2.2, §7.4.4.1d). So in PUS, silence is not proof of success.
- A request ID is source ID + APID + "sequence count or request name". The standard admits it can repeat "during tests or when the sequence count counter wraps around, implying the need to include timing information" (§5.4.11.2.1c note 1). Our UUID `cmd_id` avoids this problem.

**PUS service 5 and service 12** (same PDF)
- ST[05] reports "on-board failures and anomalies" and the "initiation, progress and completion of activities". The severity is in the message subtype: TM[5,1] informative, TM[5,2] low, TM[5,3] medium, TM[5,4] high severity (§6.5.1–6.5.4).
- ST[12] parameter monitoring checks on-board parameters "with respect to checks defined by the ground system" and reports "check transitions". One definition can hold "warning limits" and another "danger limits" for the same parameter (§6.12.1.2).
- An ST[12] check is a limit-check, expected-value-check or delta-check, with a "repetition number that is the number of successive and consistent checks that establishes a new checking status" (§6.12.3.3j). This is the same idea as our persistence (frames). The reports are TM[12,12] check transition and TM[12,9] parameter monitoring definitions.

**Time next to the counter (R5's UNVERIFIED item: answered, VERIFIED)**
- The PUS-C TM secondary header (Fig. 7-7, §7.4.3.1) holds: PUS version (4 b), time reference status (4 b), service type (8 b), subtype (8 b), **message type counter (16 b)**, destination ID (16 b), **time (absolute time)** and optional spare. Every TM packet has this header except the time packets themselves (§7.4.3.1a). The time format (PFC) "shall be declared when specifying the time service" (§7.4.3.1j). So yes: on-board time travels next to the 14-bit sequence count of the primary header.
- In PUS the primary-header sequence count is "incremented by 1 whenever the source application process releases a packet" and "wraps around from 2^14−1 to zero". Sequence flags are always `11` (stand-alone) (§7.4.1 overview).

**CCSDS Space Packet** — [CCSDS 133.0-B-2](https://ccsds.org/Pubs/133x0b2e2.pdf)
- Primary header (6 octets): version (3 b), type (1 b), secondary header flag (1 b), **APID (11 b)**, **sequence flags (2 b)**, **sequence count or packet name (14 b)**, data length (16 b) (§4.1.3, Fig. 4-2). The data field is 1–65 536 octets.
- Sequence flags: `00` continuation, `01` first, `10` last, `11` unsegmented (§4.1.3.4.2.2). All-ones APID = idle packet (§4.1.3.3.4.4).
- The secondary header is optional and mission-specific: time code only, ancillary data only, or time then ancillary. The choice "shall remain static" per data path (§4.1.4.2.1.5–6). The time code is a CCSDS segmented or unsegmented binary code from CCSDS 301.0-B-4, e.g. CUC or CDS (§4.1.4.2.2).

**XTCE** — [CCSDS 660.2-G-2 (Green Book, 2021)](https://public.ccsds.org/Pubs/660x2g2.pdf). The normative text is XTCE 1.2, CCSDS 660.0-B-1.2 (its ref. [1]).
- A MetaCommand has Arguments, TransmissionConstraint ("constrains the current command"), VerifierSet, **Interlock** ("constrains the next command … block successive commands until the current command has reached a certain stage"), and DefaultSignificance. The significance levels are none / watch / warning / distress / critical / severe, for systems that "require special user access or confirmations" (§2.6.3, §3.2.4.4.5–8).
- "A verifier is a conditional check on the telemetry down-linked … There are eight different verifiers": TransferredToRange, SentFromRange, Received, Accepted, Queued, Execution, Complete, Failed (§3.2.4.4.8).
- Telemetry metadata holds "Alarms (limits) and calibration information"; alarms "vary somewhat by ParameterType" (§2.6.2, §3.2.3.2).

**Yamcs command history**
- Ground acks are Q (queued), R (released) and S (sent). The link writes S. Verifiers start "just before releasing the command" and can "declare the command completion (either successfully or with failure)". Also, "Yamcs does not enforce strict handling of command completion": one verifier may fail a command that another later marks successful — [command processing](https://docs.yamcs.org/yamcs-server-manual/processors/command-processing/)
- The keys are `Acknowledge_Queued`, `Acknowledge_Released`, `Acknowledge_Sent`, `CommandComplete`, the prefix `Verifier`, and the suffixes `_Status`/`_Time`/`_Message`/`_Return` — [Yamcs constants](https://docs.yamcs.org/javadoc/yamcs/latest/constant-values.html). The statuses are NA, SCHEDULED, PENDING, OK, NOK, TIMEOUT, CANCELLED, DISABLED — [AckStatus](https://docs.yamcs.org/javadoc/yamcs/latest/org/yamcs/cmdhistory/CommandHistoryPublisher.AckStatus.html)
- Each verifier has a check window `start,stop` relative to `LastVerifier` (default) or `CommandRelease`. Each has OnSuccess/OnFail/OnTimeout = SUCCESS (`CommandComplete`), FAIL (`CommandFailed`) or empty (only a `Verification_<stage>` event). The verifier types are `container` ("verified when the container is received"; OK or timeout, never NOK) and `algorithm` — [command verification sheet](https://docs.yamcs.org/yamcs-server-manual/mdb/loaders/sheet/command-verification/)

**Yamcs / COSMOS command safety**
- Yamcs: "Currently, Yamcs Server does not check or impose anything based on the significance of the command" — [TC manual](https://docs.yamcs.org/yamcs-server-manual/tc/). The MDB sheet says that "depending on the configuration, an extra confirmation or certain privileges may be required" — [command options](https://docs.yamcs.org/yamcs-server-manual/mdb/loaders/sheet/command-options/). The server-side tool is a command queue with `minLevel` (e.g. `critical`) in state `blocked`: commands wait there for a manual release, and privileges are checked before queueing — [TC manual](https://docs.yamcs.org/yamcs-server-manual/tc/)
- Yamcs Studio: "Arming a command is a client-side notion … no communication with Yamcs during this step". A significant command shows "a confirmation dialog" at arm time — [command stack](https://docs.yamcs.org/yamcs-studio/display-runner/command-stack/)
- The significance levels follow ISO 14950: CRITICAL = "if executed at the wrong time or in the wrong configuration, could cause irreversible loss…" — [Significance.Levels](https://docs.yamcs.org/javadoc/yamcs/latest/org/yamcs/xtce/Significance.Levels.html). The Javadoc misprints the standard as "ISO 14490".
- COSMOS `HAZARDOUS` (command or parameter state, with an optional description) "causes a dialog asking for confirmation" — [command config](https://docs.openc3.com/docs/configuration/command). The check is in the **server**: the interface microservice returns `HazardousError` "if hazardous_check and hazardous", and "the error handler [can] simply re-send the command" with `cmd_no_hazardous_check` — [interface_microservice.rb](https://github.com/OpenC3/cosmos/blob/main/openc3/lib/openc3/microservices/interface_microservice.rb), [scripting API](https://docs.openc3.com/docs/guides/scripting-api). So the server enforces that a confirmation step happened, but any client can set the flag.
- COSMOS Critical Commanding (Enterprise per the docs; see R1 for the licence): "The user who sent the command can't approve it, and each approver must be a different user". It applies to HAZARDOUS and RESTRICTED in mode NORMAL — [command config](https://docs.openc3.com/docs/configuration/command)
- COSMOS `VALIDATOR.post_check` returns "true to indicate Success, false to indicate Failure, and None to indicate Unknown". Its example waits up to 10 s for `CMD_ACPT_CNT` to increase — [command config](https://docs.openc3.com/docs/configuration/command). Both COSMOS and Yamcs verify a command against telemetry and keep a three-way result: success, failure or unknown/timeout.

**NASA practice**
- NPR 7150.2 SWE-134: "Operator overrides of safety-critical software functions require at least two independent actions by an operator"; "prerequisite checks prior to the execution of safety-critical software commands"; "No single software event or action is allowed to initiate an identified hazard"; reject hazardous out-of-sequence commands. Note on (d): "Multiple independent actions by the operator help to reduce potential operator mistakes" — [SWEHB SWE-134](https://swehb.nasa.gov/display/7150/SWE-134+-+Safety+Critical+Software+Requirements)
- NHB 1700.7A (STS payload safety): for catastrophic hazards, "No combination of two failures, operator errors, or RF … signals shall result in…" injury or loss. A catastrophic function needs "a minimum of three independent inhibits" (critical hazards: two) — [NHB 1700.7A §201–202](https://ntrs.nasa.gov/api/citations/19830024564/downloads/19830024564.pdf). In this rule an operator error counts as a failure, which is why the confirm step and the interlock exist. **VERIFIED for NSTS 1700.7B** (JSC, Jan 1989; EverySpec mirror, NTRS has no copy): §200.1a critical hazards "no single failure or operator error", §200.1b catastrophic "no combination of two failures or operator errors", §201.2 "two independent inhibits", §201.3 "a minimum of three independent inhibits". SSP 51700 and the ISS Addendum: **STILL UNVERIFIED** (not read).
- SSP 50038 was not accessible (**STILL UNVERIFIED**: no accessible copy; not cited).

**Interlock primitive** — `SET key value NX PX ms`: NX = "Only set the key if it does not already exist", PX = expiry in ms. It returns null when NX blocks the write. It is atomic and O(1) — [Redis SET](https://redis.io/docs/latest/commands/set/). Redis discourages it as a *lock* in favour of Redlock. Here we only need a "seen recently" marker, not mutual exclusion.

**Python libraries (PyPI, checked 2026-11-13)**
- [`spacepackets` 0.32.0](https://pypi.org/project/spacepackets/) (Apache-2.0, 2026-05-03): implements Space Packet per 133.0-B-2, "PUS Telecommand and PUS Telemetry … ECSS-E-ST-70-41C", CDS short timestamp, CFDP and USLP — [docs](https://spacepackets.readthedocs.io/en/latest/). It is the candidate for a per-packet streaming decoder. CUC support: **VERIFIED absent** in 0.32.0 (only `spacepackets.ccsds.time.cds` / `CdsShortTimestamp`; package inspected 2026-11-14).
- [`ccsdspy` 2.1.0](https://pypi.org/project/ccsdspy/) (BSD-3, 2026-10-28): `FixedLength`/`VariableLength` layouts, vectorised decoding into arrays per field. The documented examples are file-based (`pkt.load(file)`) — [docs](https://docs.ccsdspy.org/en/latest/). Better suited to batch/archive than to per-message streaming (streaming use **VERIFIED possible**: `FixedLength.load` accepts a file-like object; one packet decoded from `io.BytesIO`, 2026-11-14. It stays array-oriented, so per-message use pays setup per call).
- [`yamcs-client` 2.2.0](https://pypi.org/project/yamcs-client/) (LGPL, 2026-10-31): a client for a Yamcs *server* (e.g. `await_acknowledgment("Acknowledge_Sent")`) — [examples](https://docs.yamcs.org/python-yamcs-client/examples/commanding/). It is not a decoder. Relevant only if Yamcs ever replaces our core.
- PaperLab was not used: the standards above already give the rationale for two-step and interlock, and no paper changed a decision.

### State mapping (spec ↔ PUS ST[01] ↔ Yamcs ↔ XTCE)

| Spec state | Our meaning (timestamp stored) | PUS-C ST[01] | Yamcs command history | XTCE verifier stage |
| --- | --- | --- | --- | --- |
| QUEUED | API passed every check (schema, hazardous confirm+reason, interlock); row + audit written. `queued_at` = ground UTC | – (ground) | `Acknowledge_Queued` | Queued |
| SENT | `XADD tc:<vehicle>` returned. `sent_at` = ground UTC; **all timeouts start here** | – (TC leaves ground) | `Acknowledge_Released` + `Acknowledge_Sent` (merged: we have no holding queue) | TransferredToRange / SentFromRange |
| RECEIVED | vehicle accepted the command (ack message, see decision). `received_at` = ground UTC, `received_onboard_at` = vehicle time if given | TM[1,1] (reject = TM[1,2]) | `Verifier_Received` / `Verifier_Accepted` OK | Received / Accepted |
| – (not tracked) | start / progress of execution | TM[1,3..6] | `Verifier_Execution` | Execution |
| VERIFIED | first frame with `last_cmd_id == cmd_id`. `verified_at` = ground UTC; `verified_onboard_at` = that frame's `t_onboard` | ≈ TM[1,7] success completion, but ours comes from housekeeping telemetry, not a report | `Verifier_Complete` OK → `CommandComplete` OK | Complete |
| FAILED | timeout or rejection, with `failure_reason` and `outcome` (below). `failed_at` = ground UTC | TM[1,2]/[1,4]/[1,8]/[1,10], **or no report at all** | `Verifier_*` TIMEOUT / NOK → `CommandComplete` NOK | Failed |

Yes, the VERIFIED rule is a telemetry-based verifier. It matches an XTCE Complete verifier with a comparison `last_cmd_id == <cmd_id>`, and a Yamcs algorithm verifier with check window `[0, 15000]` relative to `CommandRelease`, OnSuccess=SUCCESS, OnTimeout=FAIL. It proves that the on-board software *executed* the command. It does not prove the physical effect (valve really closed); that stays the operator's check on telemetry.

## Decision candidate

**1. States and timeouts.** Chosen: keep the spec names QUEUED, SENT, RECEIVED, VERIFIED and FAILED. Add two fields on FAILED, `failure_reason` and `outcome`. Both windows are measured from SENT. This **confirms A-R1-1**.

```
            API checks ok                 XADD ok
 (request) ───────────────► QUEUED ───────────────► SENT ──ack(accepted)──► RECEIVED ──last_cmd_id==cmd_id──► VERIFIED
     │ reject (no confirm,      │ XADD error              │  │                    │
     │ interlock, schema):      ▼                         │  │ ack(rejected)      │ t > sent+15 s
     │ HTTP 4xx + audit,      FAILED SEND_ERROR           │  ▼                    ▼
     │ no command row         (outcome NOT_SENT)          │ FAILED REJECTED     FAILED NOT_VERIFIED (outcome UNKNOWN)
                                                          │ (outcome NOT_EXECUTED)
                                                          │ t > sent+5 s, no ack and no echo
                                                          ▼
                                                        FAILED NOT_RECEIVED (outcome UNKNOWN)
 SENT ──last_cmd_id==cmd_id (ack lost)──► VERIFIED   (execution implies receipt; RECEIVED time left empty, flagged)
 FAILED(outcome UNKNOWN) ──later echo──► state stays FAILED; LATE_VERIFICATION event + late_verified_at set
```

- **FAILED for the operator (Q3).** `outcome` answers "did the vehicle do it?":
  - `NOT_SENT` / `NOT_EXECUTED`: the vehicle certainly did not do it, so a resend is safe once the cause is fixed. This is the PUS "failed report" case: the vehicle said no and gave a code.
  - `UNKNOWN` (both timeouts): no evidence either way. The link may be down, the ack lost, or the vehicle slow. The operator guide says: check `mode`, `last_cmd_id` and the affected parameters before resending. **Never blind-resend a hazardous command after UNKNOWN.** This follows PUS (success reports are optional, so silence ≠ failure) and COSMOS's three-way validator (Success / Failure / Unknown).
  - UI text: "FAILED – rejected by vehicle (not executed): <code>" vs "FAILED – no acknowledgement in 5 s (outcome unknown)" vs "FAILED – not verified in 15 s (outcome unknown)".
- **Late verification: confirm A-R1-2.** FAILED stays terminal, because history is append-only (CMD-04) and ATP-03 must be repeatable. A later `last_cmd_id` echo for a FAILED/UNKNOWN command writes a `LATE_VERIFICATION` event and fills `late_verified_at`. The timeline (UI-06) then shows "FAILED (timeout) – execution seen 3.2 s later". Rejected alternative: Yamcs-style overwrite to VERIFIED, because it loses the fact that the operator saw FAILED, which an investigation needs. Note: ATP-03's "paused 16 s" case **will** produce FAILED followed by LATE_VERIFICATION once the simulator resumes, and the test must expect both.
- **RECEIVED needs a source.** The §3 frame has no ack field. Chosen: the simulator writes `{cmd_id, accepted: bool, code, reason, t_onboard}` to stream `tcack:<vehicle>` when it reads a command (= TM[1,1]/[1,2]). Rejected alternatives: inferring RECEIVED from the simulator's XACK on `tc:<vehicle>` (that says the process read the message, not that the vehicle accepted it, and it couples us to Redis internals); and a new frame field (§3 says downstream must not change).
- **Rejected:** Yamcs `LastVerifier` chaining (the 15 s window starting at RECEIVED makes the worst case 20 s and is harder to explain); extra start/progress states (no source for them in our frame).

**2. Events and limits vocabulary (ST[05], ST[12]).** Chosen: one `events` table with `severity ∈ {INFO, LOW, MEDIUM, HIGH}` (= TM[5,1..4]) and `source ∈ {GROUND, ONBOARD}`. GAP, SEQ_RESET, LOS, LATE_VERIFICATION and LIMITS_RELOADED are GROUND/INFO or LOW. A future CCSDS decoder maps TM[5,x] into the same table. In docs, describe `limits.yaml` in ST[12] terms: yellow = warning limits, red = danger limits, persistence = "repetition number", rate-of-change ≈ delta-check, alarm transition ≈ check transition. Rejected: modelling ST[12] itself (that is on-board monitoring; ours is ground-side, see R8).

**3. Decoder (Q13).** Chosen: one function type, not a class hierarchy (ponytail):

```python
class Decoder(Protocol):
    def __call__(self, raw: bytes) -> Frame: ...   # raises DecodeError(reason) → tm:dlq (ING-02)
DECODERS = {"json": decode_json}                    # chosen per stream in config; "ccsds" added when needed
```

`Frame` = the §3 fields, plus optional `apid`. A CCSDS decoder would: read the 6-byte primary header (APID → vehicle, 14-bit count → `seq`); read the PUS-C secondary header (time → `t_onboard`; service/subtype routes (3,25) to housekeeping → `Frame`, (5,x) to events, (1,x) to command acks); and decode the parameters from a packet layout. `spacepackets` for streaming, `ccsdspy` for archive batch. Downstream impact: `seq` becomes 14-bit per APID (wraps every 27 min at 10 Hz; R5's wrap-safe compare must use modulus 2^14). Rejected: XTCE as our config format now (an XML schema with ~100 element types for 9 parameters and 2 commands). Instead, `limits.yaml` and `commands.yaml` keep XTCE names (alarm ranges, significance, verifier, interlock) so an export to XTCE can be written later.

**4. `commands.yaml` (draft schema, validated with pydantic at load).**

```yaml
version: 1                       # file sha256 stored with each command row, like limits hash (ALM-08)
defaults: {receive_timeout_s: 5, verify_timeout_s: 15}
commands:
  isolate_valve:
    description: Close the cabin isolation valve
    hazardous: false
    args:
      valve: {type: enum, values: [A, B], required: true}
  vent_cabin:
    description: Open cabin vent valve
    hazardous: true              # spec §3 flag; ≈ XTCE significance >= critical
    hazard: Irreversible loss of cabin atmosphere   # shown in the confirm dialog (COSMOS HAZARDOUS description)
    args:
      duration_s: {type: float, min: 1, max: 30, unit: s, required: true}
# per-command overrides allowed: receive_timeout_s / verify_timeout_s
# not in v1 (add when needed): allowed_modes (XTCE TransmissionConstraint / SWE-134 h prerequisite check),
#   significance levels beyond a bool, second-user approval
```

**5. Hazardous two-step, enforced on the server (CMD-03).** Chosen: the COSMOS pattern, plus binding to the arguments.
- Step 1: `POST /commands {vehicle, name, args}` for a hazardous command without a confirmation → **rejected** with `HAZARDOUS_CONFIRMATION_REQUIRED`, the `hazard` text and `args_hash = sha256(canonical JSON of {vehicle, name, args})`. The rejection is audited, and no command row is created.
- Step 2: the UI shows a second dialog (hazard text, the exact args, a reason field). It re-POSTs with `confirm_args_hash` (must equal the recomputed hash) and a non-empty `reason` (≥ 10 chars, trimmed). The server rejects any mismatch, so you cannot confirm A and send B.
- The rule lives in the API service, so curl, scripts and the UI all hit it. The test is an API-level test plus a Playwright test.
- Rejected: a client-only dialog (Yamcs Studio "arming is client-side"; fails "cannot be sent without both"); server-stored single-use confirm tokens (adds a store and expiry for no extra protection against a malicious client, which is out of scope with a simple login); second-user approval (COSMOS Enterprise Critical Commanding / Yamcs blocked `minLevel: critical` queue; the spec asks for a second *confirmation*, not a second *person*). The last one is listed as the upgrade path.

**6. Double-command interlock (CMD-05).** Chosen: Redis `SET cmdlock:{vehicle}:{name}:{args_hash} {cmd_id}|{user} NX PX 2000`, run as the last check before QUEUED. `nil` → reject with "Duplicate of `<name>` sent by `<user>` `<Δt>` s ago (cmd `<id>`)", and audit the rejection. The key leaves out the user, so the same operator double-clicking is also stopped. Reasons:
- one atomic O(1) call, and expiry is the window itself;
- the API already depends on Redis to send (`tc:<vehicle>`), so Redis being down blocks commanding anyway (fail closed).

If the DB insert after a successful SET fails, the key still blocks for ≤ 2 s. That is a safe-side failure.

Rejected alternatives:
- Postgres unique index on `(vehicle, name, args_hash, time_bucket(2 s))`: bucket edges let two commands 0.2 s apart pass.
- `pg_advisory_xact_lock` + "any row in the last 2 s" query: correct and keeps one transaction with the audit insert, but needs more code. This is the switch if commanding moves off Redis.
- XTCE Interlock (block *any* next command until stage X): it would block an urgent `isolate_valve` during OPS-2.

**7. Append-only audit (CMD-04).** Chosen: Postgres table `audit_log(id bigserial, at timestamptz default now(), actor, action, vehicle, cmd_id, cmd_name, args jsonb, args_hash, hazardous, reason, result, detail jsonb, commands_hash)`.
- Actions: `CMD_REQUESTED`, `CMD_CONFIRM_REQUIRED`, `CMD_REJECTED` (interlock / schema / confirm mismatch), `CMD_STATE` (every transition with its timestamp and reason), `CMD_LATE_VERIFICATION`, `ALARM_ACK` (alarm id, note), `LIMITS_RELOADED` (old → new hash), `LOGIN`.
- The app DB role gets `INSERT, SELECT` only (`REVOKE UPDATE, DELETE, TRUNCATE`). A `BEFORE UPDATE OR DELETE` trigger that raises an exception covers the table owner too — [PG trigger functions](https://www.postgresql.org/docs/current/plpgsql-trigger.html), [RAISE](https://www.postgresql.org/docs/current/plpgsql-errors-and-messages.html).
- The command state row itself may be updated (current state). The audit table is the history, and the UI-06 timeline reads from it.
- Rejected: a hash chain or WORM storage (tamper evidence against an admin is out of scope; next step).

## Risks / what would make us switch

- **`last_cmd_id` holds only the latest command.** If two commands to one vehicle execute within one frame period (100 ms), the first never shows and falsely times out as UNKNOWN. Mitigation: the simulator executes at most one command per frame tick (assumption). Switch to an executed-commands counter or ack stream (TM[1,7]) if the bench sends bursts.
- **An ack stream that the bench hardware does not provide** would leave RECEIVED unobservable, so every command would go SENT → VERIFIED. That is acceptable: the timeline shows "RECEIVED: not reported". It would breach the CMD-01 wording, though, so it needs an ICD decision.
- **Wrong `outcome` wording** could lead an operator to resend a hazardous command that did execute. This needs review by a real flight controller; it is the most safety-relevant text in the UI.
- **Interlock key with args**: if the board reads "same command" as the name only, then `vent_cabin(5)` and `vent_cabin(10)` from two operators both pass. A one-line change of the key.
- **Clock**: `sent_at` (ground) vs `verified_onboard_at` (vehicle) come from different clocks. Never subtract them for latency (R10).
- **Switch to Yamcs** if the project needs XTCE, CCSDS links, real queues with supervisor release, and multi-stage verifiers. That is the R1 decision, not repeated here.

## Open questions → assumptions.md

1. A-R2-1: RECEIVED comes from a simulator ack stream `tcack:<vehicle>` (accepted/rejected + code). This is an ICD addition to §3.
2. A-R2-2: VERIFIED seen without RECEIVED → VERIFIED (receipt implied, flagged). Confirm with the board.
3. A-R2-3: FAILED `outcome ∈ {NOT_SENT, NOT_EXECUTED, UNKNOWN}`; operator guide: no blind resend after UNKNOWN for hazardous commands. (Confirms A-R1-1 and A-R1-2.)
4. A-R2-4: "Same command" in CMD-05 = same vehicle + name + args (any user, including the same one).
5. A-R2-5: The two-step confirm is the same user. A second-person approval is out of scope and listed as a next step.
6. A-R2-6: The simulator executes ≤ 1 command per frame, so `last_cmd_id` cannot skip a command.
7. A-R2-7: If a command is SENT while the vehicle is in LOS, it still times out normally (no pause of timers during LOS).

## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources or by running the code. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| R5's PUS time-next-to-counter item | VERIFIED (earlier, §"Time next to the counter") | ECSS/CCSDS text quoted there | D-ING-13 |
| NHB 1700.7A inhibit rules unchanged in the current text | VERIFIED for NSTS 1700.7B (§200.1a/b, §201.2/201.3); SSP 51700 and the ISS Addendum STILL UNVERIFIED (not read) | EverySpec mirror of NSTS 1700.7B (Jan 1989) | D-CMD confirm / second-person rationale; no change |
| SSP 50038 | STILL UNVERIFIED (no accessible copy; not cited) | — | — |
| `spacepackets` has no CUC timestamp | VERIFIED absent in 0.32.0 | package inspected: only `ccsds.time.cds` | §3 decoder plug point (v1+) |
| `ccsdspy` usable per message (streaming) | VERIFIED possible | `FixedLength.load(io.BytesIO(...))` decoded one packet | §3 decoder plug point (v1+) |

CONTRADICTED: none.

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
