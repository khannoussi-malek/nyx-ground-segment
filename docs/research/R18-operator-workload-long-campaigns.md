# R18 – Operator workload in long test campaigns: what the literature changes

**Drives:** GS-UI-02, GS-UI-05, GS-DAT-01, OPS-1, OPS-3, OPS-4   **Answers review Q:** 1

PaperLab workspace: **Nyx ground segment design**. Complements R14 (b) (displays, stale data, grey HMI) and R14 (d) (command safety); alarm rates and floods are R14 (a) / D-ALM-15. Most human-factors papers here have no free PDF: they are cited **abstract only** unless a page is given. The owner can upload PDFs to raise them to full text.

## Question

OPS-1 is two hours of all-green monitoring, and real campaigns (thermal vacuum, integrated tests) run for days in shifts. What does long, mostly uneventful monitoring do to operators, and does the console support it?

## Findings

### F1 – When the system loses the ability to monitor, it is silent (gap, medium)

- Only a RED limit transition makes a sound (D-ALM-09, D-UI-08). The states in which **no RED can be raised at all** are shown only visually:
  - LOS (OPS-3), FROZEN and STALE (D-ING-21, D-UI-06);
  - GROUND PROCESSING DOWN and its BROKER variant (D-UI-15);
  - NO LIVE CONNECTION (D-UI-04).
- After two hours of a grey screen and no beeps, an operator reads silence as "nothing abnormal". In those states silence means "nothing can be known". The console then fails exactly when the operator has stopped looking.
- Literature:
  - Vigilance: detection of rare signals drops with time on watch, and monitoring is effortful and stressful rather than undemanding (Warm, Parasuraman & Matthews 2008, abstract only; Mackworth 1948, abstract only).
  - Complacency: an operator who relies on automation processes information less attentively, which "has no consequences as long as the automation works" (Parasuraman & Manzey 2010, p. 24). The consequences come when it fails.
  - Auditory warnings: perceived urgency can be set through the sound's parameters, so a second, less urgent sound can be told apart from the RED beep (Edworthy et al. 1991, abstract only).
- **Change:**
  - **One loss-of-monitoring tone**, distinct from and less urgent than the RED beep. It plays once at the onset of any of these states for a vehicle: LOS, FROZEN, GROUND PROCESSING DOWN (incl. BROKER), NO LIVE CONNECTION.
  - It is played **by the browser**, so NO LIVE CONNECTION still sounds when the server is unreachable. It doesn't repeat, and the banner stays as today.
  - It uses the same armed `AudioContext` and SOUND OFF badge as D-UI-08, and the same once-per-key rule, keyed by `(vehicle, state, onset)`, so a reload doesn't replay it.
- **Test:** a Playwright test stops `ingest-1` and expects one tone (exposed as `data-last-tone` on the page) within the D-UI-15 detection time, and no second tone. Stopping the API gives the same result for NO LIVE CONNECTION.
- STALE alone (a single parameter) stays silent. It is often short and local, and sounding it would add nuisance, the problem R14 (a) measures.

### F2 – No "what happened since…" view for a shift change or a break (gap, low-medium)

- The console shows the **open** alarms (sticky until acked, D-UI-09), the **newest** events (D-UI-19) and the command timeline (D-UI-10). An alarm that was raised, acknowledged and cleared during the previous shift has left the alarm list. The incoming controller has to reconstruct it from the event strip and the audit log.
- Literature: handoffs in high-consequence work, including NASA Johnson Space Center mission control, were studied for 21 strategies (Patterson et al. 2004, abstract only). Shift changes in Space Shuttle mission control are built around **updates on what changed** since the last shift (Patterson & Woods 2001, metadata only; the title and venue are the claim, full text needed for specifics).
- **Change:** a **"Since…" summary**, `GET /api/summary?vehicle=&since=`. It lists, in time order, the alarms raised, acked (who, note) and cleared, the commands with their outcome, LOS/AOS with lost frames, and mission-db changes. It reuses the session report queries (D-DB-10) over a time window instead of a session, so no new table is needed. The console button defaults `since` to the user's last LOGOUT in `audit_log`. The ACK note (D-UI-09, required) is the written handover content that already exists.
- **Test:** an integration test raises, acks and clears an alarm, sends a command and causes a dropout. The summary since before those actions lists all of them, in order, with the ack note.

### Optional – Measure workload in the dry run (evaluation, not a build item)

- If the owner wants evidence for review-board Q1 beyond requirement tests: run NASA-TLX (Hart & Staveland 1988, abstract only) with the two demo controllers after an OPS-2 + OPS-3 dry run. It is a 6-item questionnaire and needs no code.

### Confirmations (no change)

- **Only RED sounds for limit alarms** (D-ALM-09): right for limit alarms. F1 adds a separate class (loss of monitoring) and doesn't change it.
- **Sticky alarm list with a required ACK note, and grey when nominal** (D-UI-07, D-UI-09): these reduce what a tired operator must notice unaided. Grey-when-nominal also makes F1's banners stand out more, but a banner still needs eyes on the screen; the tone doesn't.

## Decision candidate

Chosen: F1 (loss-of-monitoring tone, played by the browser, once per onset) and F2 (Since… summary from the report queries). Because: F1 closes the one case where the console is silent while unable to detect a RED. F2 makes a shift change or a break recoverable with a query that already exists. Rejected:
- Sounding every STALE parameter: nuisance, against R14 (a).
- A repeating tone until acknowledged: a second ACK workflow for a state the operator can't fix from the console.
- A free-text shift log table: the ACK notes, command reasons and events already hold that content, and a log nobody is required to write stays empty.

## Risks / what would make us switch

- If dry runs show the tone itself becoming a nuisance (for example many short broker restarts), add a minimum duration before it sounds (e.g. 5 s), measured with D-ALM-15-style counts.
- The human-factors evidence here is mostly abstract-level. Uploading the full texts (Warm 2008, Patterson & Woods 2001, Patterson 2004, Edworthy 1991) would let `plab.py verify` check these claims page by page.

## Open questions → assumptions.md

- A-R18-1: Should a FAILED command also sound, to its sender only? Default: no. The sender is usually watching the timeline they just used, and OPS-5 needs no tone.
- A-R18-2: The default `since` for the summary: the user's last LOGOUT, or the start of the active session? Default: last LOGOUT, falling back to session start.

## Sources (PaperLab: Nyx ground segment design)

Warm, Parasuraman & Matthews 2008 Vigilance Requires Hard Mental Work and Is Stressful, 10.1518/001872008X312152 · Mackworth 1948 The Breakdown of Vigilance during Prolonged Visual Search, 10.1080/17470214808416738 · Parasuraman & Manzey 2010 Complacency and Bias in Human Use of Automation, 10.1177/0018720810376055 · Edworthy, Loxley & Dennis 1991 Improving Auditory Warning Design, 10.1177/001872089103300206 · Patterson & Woods 2001 Shift Changes, Updates, and the On-Call Architecture in Space Shuttle Mission Control, 10.1023/a:1012705926828 · Patterson et al. 2004 Handoff Strategies in Settings with High Consequences for Failure, 10.1093/intqhc/mzh026 · Hart & Staveland 1988 Development of NASA-TLX, 10.1016/s0166-4115(08)62386-9 · Woods 1995 The alarm problem and directed attention in dynamic fault management (already in workspace)

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
