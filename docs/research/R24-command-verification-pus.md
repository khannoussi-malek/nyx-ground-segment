# R24 – Command verification and hazardous commanding: what PUS, ECSS and NASA practice say about Nyx

**Drives:** D-CMD-01..11, D-EXT-08, D-SIM-06/10, `mission/commands.yaml`, the `tcack` interface (D-EXT-16)   **Asked by:** coordinator (nyx-c3), 2026-11-21   **Builds on:** [R2](R2-standards-and-command-safety.md) (vocabulary, two-step, interlock); this note corrects R2's VERIFIED mapping (F1)

**Evidence levels** used in each finding:
- **[S]** standard text, quoted with clause and page;
- **[D]** vendor documentation, quoted;
- **[C]** Nyx code or design read on `main` at 70f478c;
- **[A]** analysis, mine.

The documents were fetched on 2026-11-21 and saved in `papers/web/` (git-ignored; URLs in [SOURCES.md](SOURCES.md)). PaperLab was unavailable, and no paper was needed: the standards are the primary sources here.

## Question

How do ECSS-E-ST-70-41C service 1 and the command lifecycle used in flight operations map onto Nyx's command states and outcomes? What do the standards and tools require for hazardous and critical commands, and for checks before transmission? Which gaps would a flight operations team flag, ranked?

## Mapping: Nyx command states vs PUS-C ST[01] and the ground stages

| Nyx (D-CMD-01/02/04) | What Nyx actually observes [C] | PUS-C / ground stage | Corrected reading |
| --- | --- | --- | --- |
| QUEUED | API checks passed; row written | Yamcs `Queued` | same |
| SENT | `XADD tc:<v>` returned | Yamcs `Released` + `Sent`; COP-1 link stages not modelled | same, with no uplink or link-level stage (F8) |
| RECEIVED | `tcack accepted=true` (args and schema checked on board) | TM[1,1] successful **acceptance** | same |
| REJECTED (FAILED) | `tcack accepted=false`, code SCHEMA / INVALID_ARGS | TM[1,2] failed acceptance | same |
| **VERIFIED** | first frame with `last_cmd_id == cmd_id`. The simulator sets it **when the command starts** (`commands.py:83-84`), and `vent_cabin` then runs for `duration_s` (1–10 s) | R2 said "≈ TM[1,7] completion" | **TM[1,3] start of execution** for durational commands. Nothing reports completion or the effect (F1) |
| – | no start/progress/completion failure path in `tcack` | TM[1,4], [1,6], [1,8] failure reports, **always reported** | missing (F4) |
| outcome UNKNOWN / EXECUTED_LATE / … | computed from late evidence | – (ground analysis) | Nyx-specific and sound. PUS makes failures always reported, so a PUS vehicle reduces the UNKNOWN cases |

PUS-C basis [S], ECSS-E-ST-70-41C (15 April 2016):
- **Flags and reporting rule** (§5.4.11.2.2, p. 50). Every request carries four flags: acceptance, start, progress and completion. A **successful** stage "is only reported if that flag indicates such reporting need". A **failed** acceptance, start or completion "is reported" regardless of the flags.
- **Execution** (§5.4.11.2.3, p. 51): failed **pre-conditions** → failed start; each step → progress; at the end, **post-conditions** → completion.
- **Meaning of completion** (pp. 51–52): "A successful completion of execution notification means only that the subservice provider has checked all post-conditions defined in the execution verification profile of that request."
- **Acceptance checks** (§6.1.4.1b, p. 59) "shall be declared when specifying that subservice", for example "that the request has not been corrupted", "the availability of the service".

## Findings, ranked as a flight operations team would

### F1 – VERIFIED means "started", not "done": no completion or effect verification (gap, high)

- [C] `vent_cabin` is declared hazardous ("Irreversible loss of cabin atmosphere") with `duration_s` 1–10. The simulator echoes `last_cmd_id` when venting **starts**, and the venting then continues. So the console shows VERIFIED while the vent valve may still be open. No check confirms that it closed again, or that pressure fell by the expected amount. For `isolate_valve`, VERIFIED does not show that the leak stopped.
- [S] In PUS, completion means the **post-conditions** were checked (p. 51–52). [D] In XTCE and Yamcs, "Once the command has been sent, it passes through a series of execution stages … Each stage is associated to a command verifier … It is also possible to specify that the stage has passed when a specific packet has been received" (Yamcs Server Manual, §Commanding introduction). A *Complete* verifier is typically a comparison on a telemetry parameter.
- [A] R2 already noted that VERIFIED "does not prove the physical effect". It mapped VERIFIED to TM[1,7], which is wrong for any command with a duration: VERIFIED is TM[1,3].
- **Change (keeps the spec's state names; CMD-01 is untouched):**
  1. Optional **`complete:` verifier per command** in `mission/commands.yaml`: a parameter comparison with a window relative to VERIFIED, for example:
     - `vent_cabin: {param: cabin_pressure_kpa, drops_by: 1.0, within_s: duration_s + 5}`;
     - `isolate_valve: {param: cabin_pressure_kpa, rate_at_least: 0.0, within_s: 10}`.
  2. The tracker records `completed_at`, or raises an `EFFECT_NOT_SEEN` event plus an audit row. These are evidence fields, like the `late_*` fields (D-CMD-04), not a new terminal state.
  3. The UI shows "VERIFIED (started) – effect confirmed / not seen".
  4. Correct R2's mapping table and D-CMD-01's rationale ("maps cleanly to PUS service 1 acceptance/completion") to say acceptance/start.
- **Tests:**
  - `test_vent_effect_confirmed`;
  - `test_isolate_effect_not_seen_raises_event` (leak fault stays on, simulator ignores the valve);
  - a property: no `completed_at` before VERIFIED.

### F2 – No prerequisite check before a safety-critical command (gap, high; designed, not built)

- [S] NPR 7150.2 SWE-134 (h), as reproduced in NASA-STD-8739.8B (approved 2022-09-08), §3.7.3, p. 31: "The software performs prerequisite checks prior to the execution of safety-critical software commands."
- [D] Yamcs: "When the [command] is set to be released from the queue … the transmission constraints are verified … conditions set on parameters that have to be met in order for the command to be released … each constraint can specify a timeout" (Server Manual, *Transmission Constraints*).
- [C] D-EXT-08 (`allowed_modes`) is fully designed. It runs args → mode → hazardous token → interlock, re-checks on the confirm POST, and gives `MODE_UNKNOWN` on stale data. `missiondb.py` lists `allowed_modes` as a key whose feature "is not built yet". `vent_cabin`, the one hazardous command, has no precondition.
- **Change:** build D-EXT-08 as designed, and give `vent_cabin` an `allowed_modes` list. Extend it with an optional **parameter constraint** (Yamcs/XTCE `Comparison`), for example `vent_cabin: requires: {cabin_pressure_kpa: {min: 50}}`, checked the same way and audited `CMD_REJECTED CONSTRAINT_NOT_MET`. This is the cheapest item with a "shall" behind it.

### F3 – A hazardous function is fired by one telecommand (gap, medium-high; needs the simulator)

- [S] ECSS-E-ST-70-11C (31 July 2008), §5.5.2b, p. 28: "At least two separate command actions shall be used for the execution of mission‐critical and safety‐critical functions." NOTE 1: "This means an arm/safe or enable/disable command followed by an execute command." NOTE 3 gives pyrotechnics as the example.
  - Definitions (§3.2.2.5, p. 11): a mission-critical function is one that "when executed in the wrong context … or wrongly executed, can cause permanent mission degradation".
- [S] SWE-134 (i), p. 31: "No single software event or action is allowed to initiate an identified hazard."
- [C][A] Nyx's two-step (D-CMD-06) is a **ground** confirmation of **one** telecommand. It protects against the operator. It doesn't protect against a wrong, duplicated or replayed `tc` entry, a ground software bug after the API, or a future script or automation path that bypasses the console. "Irreversible loss of cabin atmosphere" is a permanent-degradation hazard in 70-11C's sense.
- **Change:**
  - **Commands:** an `arm_vent` (enable) command in `commands.yaml`. The simulator keeps an **armed** flag, visible in telemetry, that clears on its own after 30 s or after one execution. `vent_cabin` is rejected on board with a new code `NOT_ARMED` unless armed. In `commands.yaml`, `vent_cabin: requires_arm: arm_vent`.
  - **Ground:** the console shows ARMED. The hazardous confirmation stays on the execute command.
  - **Tests:**
    - `test_vent_without_arm_rejected_on_board`;
    - `test_arm_expires`;
    - `test_arm_consumed_by_one_vent`.
  - **Interface:** the `tcack` code set and the ICD gain `NOT_ARMED`, using the stream upgrade rule (D-ING-27).

### F4 – The `tcack` interface can't carry an execution failure (gap, medium; cheap now, expensive after a bench exists)

- [S] PUS reports failed **start**, **progress** and **completion** with "a failure notice made of a code value … together with any number of [data]" (execution verification profile, §5.3.5.2.3g, p. 35). Those failures are always reported (p. 50).
- [C] `TcAck` has `accepted: bool, code, reason`, which is acceptance only. A real PUS vehicle's TM[1,4] or TM[1,8] has no place to land. It would be read as an acceptance, or dropped.
- **Change:**
  - **Interface:** add optional `stage ∈ {acceptance, start, completion}` to `TcAck`, default `acceptance` so today's simulator is unchanged.
  - **Outcomes:** `decide()` maps a failed start to FAILED `EXECUTION_FAILED` → outcome NOT_EXECUTED, and a failed completion → FAILED `EXECUTION_FAILED` → outcome **EXECUTED_PARTIAL**, a new outcome text: "vehicle started it and reports it did not complete: check the parameters it affects".
  - **When:** do it before a bench ICD is frozen. After that it is an interface change on both sides (R17).

### F5 – One person can fire a hazardous command; tools offer a second-person release (gap, medium)

- [S] SWE-134 (d), p. 30: "Operator overrides of software functions require at least two independent actions by an operator". This is met today by the token and the typed vehicle ID (D-CMD-06). **None of the standards read requires a second person.**
- [D] The flight-ops tools do offer one:
  - **OpenC3 COSMOS:** "Critical commanding requires a different user to approve critical commands … HAZARDOUS and RESTRICTED commands need approval … The user who sent the command can't approve it, and each approver must be a different user" (*Critical Commanding (Enterprise)*, command configuration docs).
  - **Yamcs**, open source: a queue in state `blocked` means "HOLD … Matched commands are accepted into the queue but need to be manually released", and only users with the `ControlCommandQueue` privilege change it (Server Manual, *Command Queues*). Its example config is a `supervised` queue with `state: blocked`, `minLevel: critical`.
- [C] D-CMD-10 ("No roles … second-person approval is the documented upgrade").
- **Change, the cheapest form that needs no roles:**
  - a hazardous command, once confirmed, goes to a new **HELD** state;
  - **any other** logged-in user can release it within 120 s; release re-runs F2's checks (Yamcs checks constraints at release) and the interlock;
  - every step is audited (`CMD_HELD`, `CMD_RELEASED`, `CMD_HOLD_EXPIRED`);
  - one switch in `commands.yaml` defaults: `second_person: false`, so ATP-03 is unchanged.
- **Owner decision**, because it changes D-CMD-10.

### F6 – No command authority: two consoles can command one vehicle (gap, low-medium)

- [D] OpenC3: "Once Command Authority is enabled, individual users can take and release Command Authority which enables exclusive command and script access to that target for that user. Without taking Command Authority, users can not send a command" (*Command Authority (Enterprise)*).
- [C] Nyx has only the 2 s double-command interlock (D-CMD-07). Two operators can send different commands to the same vehicle seconds apart.
- **Change, if wanted:**
  - a per-vehicle `cmdauth:<v>` key holding the user, set with take and release endpoints, with an expiry while the session is idle;
  - commands from other users get 409 `NO_COMMAND_AUTHORITY`;
  - the console shows who holds authority, and every change is audited.

### F7 – Declared acceptance checks and out-of-sequence rejection (small)

- [S] PUS §6.1.4.1b: the acceptance checks list "shall be declared". [C] The simulator checks schema and args (`SCHEMA`, `INVALID_ARGS`), but no ICD lists them. **Change:** list the `tcack` codes and the checks behind each in the operator guide and the bench ICD. That is documentation only.
- [S] SWE-134 (e): "rejects commands received out of sequence when execution of those commands out of sequence can cause a hazard". [C][A] One ordered `tc:<v>` stream and at most one command per tick keep the order. With F3, `vent_cabin` before `arm_vent` is exactly the case the on-board arm rejects. Nothing more is needed.

### F8 – Ground stages a station would add (information)

- [S] On an RF link, COP-1 accepts Type-A frames "only if they are received in strict sequential order", so that data is delivered "without omission or duplication" (CCSDS 232.1-B-2, p. 2-1). The station reports uplink and link-level (FARM/CLCW) acceptance before PUS acceptance.
- [A] Nyx's bench link is a reliable network stream, so SENT → RECEIVED is enough. A real station needs `UPLINKED` and `LINK_ACCEPTED` stages, as Yamcs stages `TransferredToRange` and `SentFromRange` show. Record this in the §3.5 bench list. No change for v1.

## Decision candidate

- **Chosen, in order:**
  1. **F2:** build D-EXT-08 plus a parameter constraint for `vent_cabin`. It has a "shall" behind it and the design is done.
  2. **F1:** the `complete:` effect verifier, with corrected PUS mapping text.
  3. **F4:** the `stage` field in `TcAck`, before any bench ICD.
  4. **F3:** arm/execute for `vent_cabin`.
  5. **F5 and F6:** owner decisions.
- **Because:** F1 and F2 close what a flight operations reviewer would see first. A hazardous command reported VERIFIED while it is still running, and sent with no precondition, is a review finding against ECSS and NASA practice. F4 is cheapest before an interface is frozen.
- **Rejected:**
  - Renaming VERIFIED: the spec fixes the state names (CMD-01). Evidence fields carry the difference.
  - Full PUS ST[01] with four flags: the simulator has no step structure, and F1's verifier plus F4's `stage` give the operator the same information.

## Open questions → assumptions.md

- A-R24-1: The `vent_cabin` effect threshold, for example a drop of ≥ 1 kPa within duration + 5 s. The owner sets it from the simulator's vent rate of 2 kPa/s.
- A-R24-2: Is a second-person release (F5) wanted, and for which commands? Default: off.
- A-R24-3: The arm window for F3. Default: 30 s, cleared after one execution.

## Sources

- ECSS-E-ST-70-41C, Telemetry and telecommand packet utilization (15 April 2016): https://ecss.nl/wp-content/uploads/2016/06/ECSS-E-ST-70-41C15April2016.pdf. Cited: §5.4.11.2.2 p. 50, §5.4.11.2.3 pp. 51–52, §5.3.5.2.3g (execution verification profile) p. 35, §6.1.4.1 p. 59.
- ECSS-E-ST-70-11C, Space segment operability (31 July 2008): https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-70-11C31July2008.pdf. Cited: §3.2.2.5 p. 11, §5.5.2 p. 28.
- NASA-STD-8739.8B, Software Assurance and Software Safety Standard (approved 2022-09-08), reproducing NPR 7150.2 SWE-134: https://standards.nasa.gov/sites/default/files/standards/NASA/B/0/NASA-STD-87398-Revision-B.pdf. Cited: §3.7.3 pp. 30–31.
- CCSDS 232.1-B-2, Communications Operation Procedure-1: https://ccsds.org/Pubs/232x1b2e2c1.pdf. Cited: pp. 2-1, 2-2.
- Yamcs Server Manual: *Commanding* (queues, significance, transmission constraints), https://docs.yamcs.org/yamcs-server-manual/tc/ ; full manual PDF https://dl.yamcs.org/docs/yamcs-server-manual.pdf (command stages and verifiers).
- OpenC3 COSMOS documentation, *Command configuration* (Critical Commanding and Command Authority, Enterprise): https://docs.openc3.com/docs/configuration/command
- Nyx: `mission/commands.yaml`, `src/gs/simulator/commands.py`, `src/gs/common/missiondb.py`, design D-CMD-01..11 and D-EXT-08.

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
