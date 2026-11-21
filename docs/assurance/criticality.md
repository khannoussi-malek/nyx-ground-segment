# Software criticality record (candidate, for the owner to confirm)

**Status: CANDIDATE.** Every determination below is a proposal from research note
[R25](../research/R25-software-assurance-gap.md) (F1). None is final until the owner confirms it, and the
system hazard analysis of the test campaign that uses Nyx has the last word (open questions A-R25-1 and A-R25-2).
Later assurance items take their scope from this file.

## 1. What the software is

Nyx is ground software for a test campaign: it monitors and commands a test article (EGSE class). ECSS-E-ST-40C
§1 (p. 12) applies to "all the elements of a space system, including ... the ground segment", and ECSS-E-ST-70C
Annex K.2 (p. 123) lists EGSE and test tools among ground software. Its worst function is `vent_cabin`
("Irreversible loss of cabin atmosphere", `mission/commands.yaml`). In a test campaign this means loss or damage
of the test article, not of crew.

## 2. ECSS-Q-ST-80C category: candidate C

- Basis: ECSS-Q-ST-80C Rev.1, Annex D.1, Table D-1 (pp. 90-91). The category follows the severity of the
  functions the software is involved in (I to IV, defined in ECSS-Q-ST-30 and -40, not read here) and the
  compensating provisions.
  - Category B: software involved in a category I function with at least one compensating provision (a hardware
    implementation or an operational procedure), or in a category II function with none.
  - Category C: the same pattern one level down.
- Reasoning: loss of the test article is a critical or major consequence, so the severity is II or III. The
  compensating provisions in the Table D-1 sense are outside the software: the test conductor's written
  procedure and the bench's own interlocks, which Nyx does not control. **Assumption, not confirmed:** no bench
  ICD is on file (R26: not frozen), so the owner must confirm that such a procedure and interlocks exist. Nyx's own
  two-step dialog, typed ID, reason and audit row are software mitigations (section 5) and do not count here.
- **Candidate: C.** It becomes **category B** if the system hazard analysis rates the vent as category II
  and no external provision is confirmed (ECSS-Q-ST-80C Table D-1, as R25 quotes it). Category B requires 100 %
  statement and decision coverage (E-40C §5.8.3.5b, R25 F2 table; MC/DC stays agreed with the customer). MC/DC
  comes separately from NPR SWE-219 if the commanding path is safety-critical (section 4). The owner and the
  customer's safety team decide.
- Coverage consequence (ECSS-E-ST-40C §5.8.3.5b, p. 64): for C, statement, decision and MC/DC coverage are
  "agreed with the customer" (AM). Proposed value (A-R25-3): 80 % overall, 100 % on the safety-critical slice
  of section 4.

## 3. NASA class (NPR 7150.2D): candidate C

Appendix D (pp. 82-83): Class C includes "(d) Software used for the testing of space assets", for example
"software that supports prelaunch integration and test". Class B excludes simulators, emulators and facilities
used in testing Class B systems in a development environment. Nyx is a test-support tool, so Class C.

## 4. Safety-critical determination (SWE-205): candidates

SWE-205 (§3.7.1, p. 26) asks the project manager to decide, per component, whether it is safety-critical under
NASA-STD-8739.8. If it is, SWE-134 (prerequisite checks, no single event may start a hazard) and SWE-219 (100 %
MC/DC) apply. **To be confirmed by the owner / system hazard analysis.** Hazard IDs refer to
[hazards.md](hazards.md).

| Component | Hazard it can cause or fail to prevent | Candidate safety-critical | Why |
| --- | --- | --- | --- |
| `gs.commanding.args` (argument check) | H-1: a wrong or out-of-range argument reaches a hazardous command | Yes | It is the first gate before a command is written to `tc:`. |
| `gs.commanding.decide` and `outcome` | H-4: a command shown as done when it only started, or as failed when it ran | Yes | They decide the state and outcome text the operator acts on. |
| `gs.commanding.tracker` | H-4: timeouts and late evidence missed | Yes | It applies the 5 s and 15 s windows and writes the audit rows. |
| `gs.api.commands` (confirm token, interlock, audit) | H-1, H-9: a hazardous command sent without the second step, twice, or by a replayed token | Yes | It is the only ground enforcement of the two-step rule (D-CMD-06) and the double-command interlock (D-CMD-07). |
| `gs.alarms` (engine and checks) | H-2: a RED condition is missed or late | Yes | An unraised RED leaves the test conductor unaware of a limit violation. |
| `gs.ingest` (sequence tracker, stale and frozen detection, ownership) | H-3, H-5, H-10: a false LOS, a hidden gap, lost or duplicated frames | No (candidate) | It feeds the alarm engine, but a fault shows as GAP, STALE or LOS rather than silently. Revisit if the hazard analysis relies on it. |
| Limits and commands loading (`gs.common.missiondb`) | H-6: a wrong limit set or command definition applied | No (candidate) | Validated at load and hashed onto every alarm. Revisit with the alarm engine. |
| `gs.archiver`, `gs.replay`, `gs.api` (reports, export, UI feeds) | H-7, H-8, H-11: a corrupt archive replayed as truth | No | They do not act on the vehicle and corruption is detected (D-ARC-03, D-ARC-11). |
| `gs.simulator` | None on the vehicle | No | A test double, never connected to a real vehicle. |
| UI (`ui/`) | H-4 (display of outcome), H-3 (staleness colour) | No (candidate) | It only shows what the safety-critical parts decided. Display tests are listed in hazards.md. |

Components not named here were not assessed; the owner decides whether they are safety-critical.

## 5. Software controls inside Nyx (not compensating provisions in the Table D-1 sense)

- Two-step hazardous command with a single-use, user-bound, argument-bound server token and a reason (D-CMD-06).
- Double-command interlock (D-CMD-07) and append-only audit log (D-CMD-08).
- Commands cannot be unsent by a late echo: FAILED is terminal and late evidence is recorded beside it (D-CMD-04).
- Staleness, frozen-value and loss-of-signal detection shown on every value (D-ING-21, D-ALM-04).

## 6. Open items for the owner

1. Confirm ECSS category C (or B) and NASA class C.
2. Confirm the safety-critical list in section 4, or let the system hazard analysis decide it.
3. Confirm with the bench ICD (or the test conductor) that a written procedure and bench interlocks exist outside Nyx;
   without them the category is B.
4. Agree the coverage value for category C with the customer, if there is one.
