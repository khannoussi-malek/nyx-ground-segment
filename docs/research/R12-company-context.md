# R12 – Company and role context

**Drives:** spec §1 (background), README framing, GS-ENG-06, GS-ENG-08, demo script   **Answers review Q:** 15, 17, 18

## Question

What exactly does the job posting ask for, and which requirement groups show each point? What is publicly known about The Exploration Company (TEC), Nyx, its test campaigns and its ground software? Given that, what should the README and demo put first, which words should they use, and how do I answer hiring-panel Q15, Q17 and Q18?

Method: public web pages only, read 2026-11-13. The devjobs.de link from the spec returns HTTP 403 to automated fetches, so I read the same posting on mirrors. Three mirrors give the same title, intro and bullet lists, so I treat them as one text.

## Findings

### 1. The posting

- Title: "Software Engineer – Mission Control Software". Munich, on-site, full time. Arbeitnow lists it under "Digital Systems" with posting date 2026-10-23, valid through 2026-12-06 — [arbeitnow](https://www.arbeitnow.com/jobs/companies/the-exploration-company/software-engineer-mission-control-software-munich-247496). An older copy lists the team as "Software Engineering" and shows it as closed — [NGP mirror](https://jobs.ngpcap.com/companies/the-exploration-company/jobs/76187038-software-engineer-mission-control-software). The role has been posted more than once (a builtin.com copy was removed 2026-06-29) — [builtin](https://builtin.com/job/software-engineer-mission-control-software/8703518)
- Purpose: build "mission-critical software that powers our integrated testing and mission operations", mainly "the Mission Control Software" and tools "used by flight controllers and engineering teams" — same three mirrors. The responsibilities heading calls the role "Software Engineer - Ground Segment" — [arbeitnow](https://www.arbeitnow.com/jobs/companies/the-exploration-company/software-engineer-mission-control-software-munich-247496)
- Responsibilities: robust software for testing and mission operations; full lifecycle from design to deployment; scalable systems for growing mission activity; work with testing, engineering and operations teams; maintainable, well-documented, performant code; reliability through automated testing and operational validation; support users and improve development workflows; mentor junior engineers.
- Must-have: Python and/or Ruby on Rails backend; a modern JS framework (React, Vue.js); OOP and design principles; message brokers or event streaming (Redis Streams, Kafka); real-time data processing and ingestion; object storage for large data (MinIO, AWS S3); Git, automated testing frameworks, agile; scalable distributed systems; design patterns and best practices; analytical skills, attention to detail, quality balanced with delivery speed.
- Bonus: relational or time-series databases; AWS or Azure; containers and CI/CD; space or other safety-critical experience.
- Related posting, "Principal Engineer – Mission Control Software" (closed): works with "Mission Operations, AIT, and Flight Software teams"; "safe commanding and reliable telemetry reception"; scalability, observability, robustness; TypeScript, Vue/React, REST, WebSockets, Docker, Kubernetes; bonus CCSDS, AWS/GCP — [b2venture mirror](https://jobs.b2venture.vc/companies/the-exploration-company/jobs/70186874-principal-engineer-mission-control-software)
- Related posting, "Ground Segment Engineer", Bordeaux (closed 2025-07-19): build the mission control center (voice loops, video, backup power) and its interfaces to the ISS control center (MCC-H); coordinate ground station and relay partners; marine telemetry with the recovery team — [builtin](https://builtin.com/job/ground-segment-engineer/6376095)

| Posting keyword | Must / bonus | Spec groups that show it |
| --- | --- | --- |
| Python backend | must | GS-ENG-09, GS-ENG-10 (all backend services) |
| Modern JS framework (React/Vue) | must | GS-UI-01..08, GS-ENG-10 (React + TS) |
| OOP, design patterns | must | GS-ALM-07 (check registry), §3 pluggable decoder |
| Message broker / event streaming (Redis Streams, Kafka) | must | GS-ING-01, 06, 07, 08; GS-ENG-02 |
| Real-time processing and ingestion | must | GS-ING-02..05, 09; GS-ALM-05; GS-UI-01 |
| Object storage (MinIO, S3) | must | GS-DAT-04, 05, 06 |
| Scalable distributed systems | must | GS-ING-06, 10; review Q11 |
| Git, agile | must | GS-ENG-07 |
| Automated testing, operational validation | must | GS-ENG-03, GS-UI-05, §5 ATP report |
| Well-documented code | must | GS-ENG-08, §6 docs |
| Tools for flight controllers | role text | GS-UI-01..08, GS-ALM-06, GS-CMD-01 |
| Integrated testing | role text | GS-DAT-01, 02 (sessions, reports) |
| Safe commanding (Principal post) | related | GS-CMD-01..05 |
| Mentor juniors | resp. | GS-ENG-06 (add-a-parameter guide) |
| Time-series / relational DB | bonus | GS-DAT-03, GS-UI-04 (R6) |
| Containers, CI/CD | bonus | GS-ENG-01, GS-ENG-04 |
| Space / safety-critical | bonus | GS-ALM-02, 03; GS-CMD-02..04 |
| Cloud (AWS/Azure) | bonus | not covered; MinIO is S3-compatible (say so, don't claim cloud work) |
| Ruby on Rails | must (or Python) | not covered; choose Python, see Risks |

### 2. TEC and Nyx, dated

- TEC founded 2021 — [Wikipedia: Nyx](https://en.wikipedia.org/wiki/Nyx_(spacecraft)). Offices in Munich, Bordeaux, Turin and Houston — [Hogan Lovells, Series B](https://hlc.com/en/news/hogan-lovells-conseille-the-exploration-company-dans-le-cadre-de-sa-serie-b-de-160-millions-de-dollars). TEC's own ALADDIN post says Nyx is developed "across sites in Munich, Bordeaux, and Torino" — [TEC blog](https://www.exploration.space/blog/the-exploration-company-wins-esa-aladdin-cargo-capsule-contract). New HQ in Oberpfaffenhofen from Oct 2025: UNVERIFIED (only a Wikipedia mirror).
- 2024-05-22: ESA LEO Cargo Return phase 1, €25 M each to TEC and Thales Alenia Space — [TechCrunch](https://techcrunch.com/2024/05/23/esa-prepares-for-the-post-iss-era-selects-the-exploration-company-thales-alenia-to-develop-cargo-spacecraft)
- 2025-06-23: Mission Possible launched on Transporter-14. In orbit it powered 25 payloads, kept communications and attitude control, and made a controlled re-entry from about 550 km. Communications were lost at about 26 km, just before parachute opening, and the capsule was lost. TEC called it a partial success. The parachute was not drop-tested, and critical systems were single-string — [TEC blog, 2025-06-30](https://exploration.space/blog/mission-possible-re-entry-milestone-achieved-for-europes-reusable-spaceflight). No root cause had been published as of 2025-10-02 — [SpaceNews via SLS](https://www.spacelaunchschedule.com/space-news/the-exploration-company-still-studying-mission-possible-reentry). I found no later root-cause statement.
- 2025-08: Nyx passes phase 1 of the ISS safety review — [European Spaceflight](https://europeanspaceflight.com/?p=5044)
- 2025-10 / 2025-11: wind-tunnel tests at DLR Cologne; MMOD shield test campaign — [Wikipedia: Nyx](https://en.wikipedia.org/wiki/Nyx_(spacecraft)) (citing European Spaceflight)
- 2026-01: 20 splashdown tests with a 1:4 model in Rome — [Wikipedia: Nyx](https://en.wikipedia.org/wiki/Nyx_(spacecraft))
- 2026-03-31: structural test model (STM) pressure tests done, with displacement sensors and strain gauges — [European Spaceflight](https://europeanspaceflight.com/?p=6280)
- 2026-04-02 to ~05: STM vibration campaign on ESA ESTEC's HYDRA shaker, 69 tests across payload configurations — [European Spaceflight](https://europeanspaceflight.com/the-exploration-company-completes-nyx-test-model-vibration-testing/)
- 2026-05-19: recovery-system drop test in the Mojave. The drop test vehicle was released from 2.8 km and the drogue-to-main handover was nominal. It was "instrumented to capture deployment timing, loads, and vehicle behavior" — [TEC blog, 2026-06-04](https://exploration.space/blog/nyx-recovery-drop-test)
- 2026-09-10: ESA ALADDIN contract (LEO Cargo Return phase 2), €760 M: €310 M for the ISS demo and €450 M for two optional missions. Launch on Ariane 6 contracted with Arianespace. Nyx is in the NASA ISS certification process — [TEC blog](https://www.exploration.space/blog/the-exploration-company-wins-esa-aladdin-cargo-capsule-contract). First flight: "2028" — [TEC drop-test blog](https://exploration.space/blog/nyx-recovery-drop-test); "no later than 2029" per the Ariane 6 deal — [Wikipedia: Nyx](https://en.wikipedia.org/wiki/Nyx_(spacecraft))
- Thermal vacuum testing of Nyx: I found no public report. Spec §1 lists "thermal" as a test type, which is fine as an assumption, but don't present it as a TEC fact.

### 3. Ground segment and software stack

- What is public: TEC builds its own Mission Control Software. The 2025 Mission Possible flight used in-house flight software and an in-house onboard computer — [TEC blog](https://exploration.space/blog/mission-possible-re-entry-milestone-achieved-for-europes-reusable-spaceflight). It is building a mission control center that interfaces to MCC-H — [Ground Segment Engineer post](https://builtin.com/job/ground-segment-engineer/6376095)
- Tools named in public postings: Python or Ruby (on Rails), TypeScript, Vue or React, REST, WebSockets, Redis Streams or Kafka, MinIO or S3, Docker, Kubernetes, CCSDS (bonus) — postings above.
- GitHub org `The-Exploration-Company` exists, with 0 public repos (`gh api orgs/The-Exploration-Company`, 2026-11-13).
- I found nothing public tying TEC to Yamcs, OpenC3 COSMOS or Open MCT, and no talk or engineering blog on its ground software. That Ruby on Rails is in their current stack is UNVERIFIED: the posting lists it as an alternative to Python, which suggests it but does not say so.

## Decision candidate

**Chosen: emphasise the trust path and the evidence package, in TEC's own words.**
1. **README opener:** "Mission control software for integrated testing (AIT) that grows into mission operations." Then name the users: flight controllers, test conductors, engineering teams.
2. **Demo order (5 min):** (a) two test articles live, then a link dropout: STALE → LOS → GAP event with the lost frame count → automatic recovery (GS-UI-02, GS-ING-04). (b) leak → yellow → red → ack → clear, with who/when (GS-ALM-06, GS-UI-05). (c) hazardous command: two-step confirmation and reason → VERIFIED, or FAILED with the reason (GS-CMD-01..03). (d) kill a worker: 0 frames lost (GS-ING-07). (e) replay the dropout window from MinIO with the same alarms (GS-DAT-06).
3. **Put the session report and replay forward.** TEC's public test record is campaign-based and data-heavy: pressure tests with strain gauges, 69 vibration runs across configurations, an instrumented drop test. The "compare this run with the last one" story (GS-DAT-01/02/06) matches it.
4. **Vocabulary:** posting words first (integrated testing, mission operations, flight controllers, Mission Control Software, safe commanding, telemetry reception, automated testing and operational validation). Add AIT and MCC, which TEC's own postings use. "Test conductor" is my spec's word, not TEC's, so use it as a role name only. Avoid "CCSDS-compliant": I don't decode CCSDS (§1 out of scope). Say "decoder is a plug-in point".
5. **Mission Possible:** don't mention it in the README. If asked why LOS and replay matter, say that loss of signal is the normal case a ground tool has to handle well. Don't comment on their flight.

**Rejected:** leading with throughput, cloud or Kubernetes. Those are bonus or Principal-level items, and 25 h won't produce credible cloud evidence. **Rejected:** a Ruby on Rails backend to match a guess about their stack. Python is equally listed, and I'd be weaker in Rails.

### Draft answers (first person, < 1 min each; update with real facts after Gate 5)

- **Q15 – what I cut:** "I kept every must and cut from the shoulds first. [List the actual NCRs, e.g. GS-ING-10 at 100 Hz, GS-ALM-08 hot reload.] I cut those because no flight controller loses trust in the screen without them, whereas a frozen value, a lost frame or an unverified command breaks that trust straight away. Each cut has an NCR with what's missing and how I'd add it."
- **Q17 – AI tools:** "I used an AI assistant to research (each claim in docs/research has a source link), to draft tests and to write first versions of code. I didn't accept a performance claim without measuring it myself. The TimescaleDB layout came from a spike with raw numbers in R6. Code had to pass mypy, the tests and CI, and I read every diff. Where it was wrong, [one concrete example from the build log], a test caught it."
- **Q18 – junior adds a parameter:** "They follow docs/dev-guide.md: add the field to the frame schema, its limits to limits.yaml, and the column in a migration. They add a boundary test at exactly on, just inside and just outside. CI runs schema validation, the limit tests and the end-to-end check, so a typo fails the build and never reaches the console. GS-ENG-06 says it takes under 15 minutes, and I timed it [n min]."

## Risks / what would make us switch

- Their backend may be Rails (UNVERIFIED). Mitigation: the frame and command contracts are JSON on Redis Streams plus REST/WebSocket, so the README can show that any language can sit on either side.
- The posting may change or close before submission. Re-read it on arbeitnow before the final README pass.
- If a TEC talk or post naming their ground tools appears, update section 3 and the vocabulary.

## Open questions → assumptions.md

- A-R12-1: Is the target user at the bench an AIT test conductor, a flight controller, or both? (Assume both, as in spec §2.)
- A-R12-2: Should "thermal" stay in spec §1? There is no public TEC thermal test report. (Assume yes, as a generic test type.)
- A-R12-3: Is it worth a README line that the console could sit next to a Rails backend? (Default no, unless asked.)
