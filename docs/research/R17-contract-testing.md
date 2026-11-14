# R17 – Schema-first interfaces and contract testing: what the literature changes

**Drives:** GS-EXT-04, GS-CMD-01, GS-ING-02, GS-ENG-*   **Answers review Q:** 13, 18

PaperLab workspace: **Nyx ground segment design**. Builds on §11.6 (D-EXT-16..20). Passages were found with `plab.py find` in the full text unless marked UNVERIFIED or "abstract only".

## Question

The design has one pydantic source per interface, exported schemas, a stale check, and contract tests that validate real producer output against the exported schemas. What does it still miss, compared with the API-testing and schema-compatibility literature?

## Findings

### F1 – Version skew on Redis streams has no rule (gap, medium)

- Stream entries outlive the code that wrote them. They sit in the stream or a consumer's PEL across `docker compose up -d --build <one service>`, and across a full restart. Every stream model except `FrameHeader` is `extra="forbid"` (D-EXT-18). So **adding even an optional field** to `Tc`, `TcAck`, `UiEvent` or `DlqRecord` makes the consumer still running the other version reject the entry:
  - `tc` written by the new API, read by the old simulator (and a real bench later);
  - `tcack` written by the new simulator, read by the old ingest worker's tracker;
  - `ui:events` written by the new worker, read by the old API;
  - pending entries of the old shape, read by the new code after a restart.
- The design names a rejection path for frames only (schema → DLQ). For `tcack`, a validation error inside the tracker falls under D-ING-24 (3): roll back, log, carry on. The entry is then never XACKed and fails again on every pass. The command times out as **NOT_RECEIVED although the vehicle acknowledged it**, so the operator sees a wrong outcome. For `ui:events` and `tc`, the design doesn't state what happens.
- Literature: Habib et al. (ISSTA 2021) frame this as a **data-compatibility** problem: the new producer schema must be a subschema of what the old consumer accepts, and the reverse for entries already stored. They check it automatically (abstract only; full text pending). Web-API evolution studies report that interface changes routinely break clients (Li et al. ICWS 2013; Sohan et al. 2015; **STILL UNVERIFIED**: metadata only, no free PDF).
- **Keep `forbid`.** RFC 9413 *Maintaining Robust Protocols* (IETF 2023, https://www.rfc-editor.org/rfc/rfc9413) argues that tolerating unexpected input makes protocols decay, and recommends strictness plus explicit extension points. That is D-EXT-18's choice. The gap is only the missing rule for what a consumer does on rejection, and for how upgrades happen.
- **Change:**
  1. **One rejection rule for every stream consumer.** It is the frame rule, extended: an entry that fails its model goes to `tm:dlq` with `reason = schema`, `stream`, and the validation message; it is XACKed; an `INTERFACE_MISMATCH` event is raised once per stream and consumer. Applies to `tcack` in the tracker, `ui:events` in the API, and `tc` in the simulator. Tests: `test_invalid_tcack_dlq_not_retried`, `test_invalid_ui_event_dlq`.
  2. **Upgrade rule, stated in the dev guide:** a change to any stream model means restarting all services together (`make up`). CI step: when an exported `interfaces/schemas/*.schema.json` for a stream message differs from `main`, print "stream interface changed: restart all services together", for information only.
  - Optional, if the owner wants it automatic: classify the change with a subschema check as in Habib et al. (the `jsonsubschema` package). Not needed for one laptop.

### F2 – Fuzz the REST API from its own OpenAPI document (gap, medium)

- `test_rest_responses_match_openapi` is informational (EX-24). It checks the responses that the hand-written tests happen to provoke.
- Schemathesis derives property-based tests from an OpenAPI document. Across 16 containerised open-source services, it reported "755 bugs across fourteen out of our sixteen targets, including 111 HTTP 500 responses, 436 unexpected status codes, 52 non-schema-conforming responses", 1.4× to 4.5× more than the next-best fuzzer (Hatfield-Dodds & Dygalo, ICSE-SEIP 2022, preprint p. 6). Kim et al. (ISSTA 2022) compare 10 REST test generators on 20 services by coverage and unique failures (p. 11), which is useful if the owner wants an alternative.
- **Change:** one CI step that runs Schemathesis **in-process against the ASGI app** (no server), with a login hook for the session cookie. Checks: no 5xx, response schema conformance, status-code conformance and content type. Command-sending routes run against the test stack only. This replaces the informational test with a check that can fail CI.
- **Spike first (≈ 30 min):** Nyx exports OpenAPI 3.1 / JSON Schema 2020-12. The paper's `hypothesis-jsonschema` supported "draft-04 to draft-07" when it was written (p. 4). Confirm that the pinned Schemathesis handles 3.1 and pydantic's `$defs` before committing to it. If it doesn't, keep EX-24 as it is.

### F3 – Generate frames from the frame schema for the ingest validator (low, optional)

- `hypothesis-jsonschema` (`from_schema`, same paper, p. 4) can generate valid frames from the per-mission frame schema (D-EXT-19); mutating them gives invalid ones. Property: every valid frame is stored, every invalid one reaches the DLQ with a reason, and the worker never exits. Hypothesis is already in the stack (D-EXT-14), so this adds one property, P7. Same 2020-12 caveat as F2.

### Confirmations (no change)

- **One hand-written source per interface, `make gen` with a stale check, contract tests that validate producer output against the exported schemas rather than the models** (D-EXT-16..18): this catches the producer that serializes by hand, the export that drops a constraint, and the consumer that drifts from the producer. That is the goal of consumer-driven contract testing (Lehvä et al., PROFES 2019; metadata only), without a broker such as Pact, which one repository doesn't need.
- **Strict models (`strict`, `allow_inf_nan=False`, `forbid`)** follow RFC 9413. Keep them, with F1's rejection rule.

## Decision candidate

Chosen: F1 (rejection rule plus upgrade rule; no automatic compatibility classifier) and F2 (Schemathesis in CI, after the spike). F3 only if F2's spike shows 2020-12 support. Because: F1 fixes an operator-visible wrong outcome (NOT_RECEIVED for an acknowledged command) after any interface change; F2 turns an informational test into one backed by evidence that it finds 5xx and schema bugs. Rejected:
- `extra="ignore"` on stream readers: tolerance hides typos and drifts slowly (RFC 9413), and EX-07 chose `forbid` for that reason.
- A version field in every entry with readers for several versions: more code than restarting a single-host stack together.
- Pact or a contract broker: one repository, one team.

## Risks / what would make us switch

- When a real bench (a second producer, not the simulator) talks to Nyx, restarting everything together no longer works. Then add an interface version to the stream key or entry and the F1 subschema check (A-R17-2).
- If the Schemathesis spike fails on OpenAPI 3.1, F2 waits.

## Open questions → assumptions.md

- A-R17-1: Should `INTERFACE_MISMATCH` show a console banner, or only an event and `/healthz` degraded? Default: event plus degraded, no banner (same as SR-02 for the archiver).
- A-R17-2: When does a second producer appear (a real bench, §3.5)? That is the trigger for versioned stream interfaces.

## Sources (PaperLab: Nyx ground segment design)

Hatfield-Dodds & Dygalo 2022 Deriving Semantics-Aware Fuzzers from Web API Schemas, 10.1109/icse-companion55297.2022.9793781 (arXiv:2112.10328) · Kim et al. 2022 Automated Test Generation for REST APIs: No Time to REST Yet, 10.1145/3533767.3534401 · Atlidakis et al. 2019 RESTler, 10.1109/icse.2019.00083 · Habib et al. 2021 Finding Data Compatibility Bugs with JSON Subschema Checking, 10.1145/3460319.3464796 · Attouche et al. 2024 Validation of Modern JSON Schema, 10.1145/3632891 · Li et al. 2013 How Does Web Service API Evolution Affect Clients?, 10.1109/icws.2013.48 · Sohan et al. 2015 A Case Study of Web API Evolution, 10.1109/services.2015.43 · Lehvä et al. 2019 Consumer-Driven Contract Tests for Microservices, 10.1007/978-3-030-35333-9_35 · RFC 9413 Maintaining Robust Protocols (IETF, 2023)


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Web-API evolution breaks clients | STILL UNVERIFIED | metadata only | F1 (context only) |

CONTRADICTED: none.
