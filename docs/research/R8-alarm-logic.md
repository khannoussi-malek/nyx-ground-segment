# R8 – Alarm logic and alarm management

**Drives:** GS-ALM-01..08, GS-UI-03, GS-UI-05, GS-DAT-06 (same alarms in replay), OPS-2   **Answers review Q:** 2, 6 (and 4 for replay)

Handoffs: per-vehicle single owner and the takeover protocol → [R4](R4-per-vehicle-ordering.md). Transition keys `(vehicle, param, epoch, trigger_seq, transition)` and "redelivered frames never reach the alarm engine" → [R5](R5-idempotency-gaps-resets.md). Prior art for limits and boundaries → [R1](R1-prior-art-mission-control.md). STALE display → R9. Replay engine → R7.

## Question

1. Is a value exactly on a limit in or out (ALM-01)? 2. Hysteresis "%" of what (ALM-02), and what goes wrong near 0? 3. Does persistence N (ALM-03) gate clearing and escalation too, and what does a gap do to it? 4. How do we compute rate of change at 10 Hz with noise (ALM-04)? 5. What alarm life cycle (ALM-06, Q2, UI-05 "single alert for new red", OPS-2 "sounds once")? 6. How do we make check types pluggable (ALM-07) and keep their state rebuildable for takeover (R4) and replay (DAT-06)? 7. How does hot reload work, and what happens to in-flight state (ALM-08, Q6)? 8. Which limits apply in replay?

## Findings

**Boundaries: the reference systems disagree**
- EPICS `ai` record: `val >= hihi`, `val >= high`, `val <= low`, `val <= lolo`. A value on the limit **is in alarm** — [aiRecord.c `checkAlarms`](https://github.com/epics-base/epics-base/blob/7.0/modules/database/src/std/rec/aiRecord.c)
- OpenC3 COSMOS: `if value > yellow_low … else # value <= yellow_low`, then `if value > red_low → YELLOW_LOW else RED_LOW`, with the same pattern using `<` on the high side. A value on the limit goes to the **more severe** state — [packet.rb L1487-1513](https://github.com/OpenC3/cosmos/blob/main/openc3/lib/openc3/packets/packet.rb#L1487-L1513); R1 A-R1-4.
- Yamcs/XTCE: an alarm range is the *in-limits* range, and each end can be inclusive or exclusive. `inRange` returns out-of-limits for `v < min` when min is inclusive and for `v <= min` when it is exclusive. With the usual `minInclusive`/`maxInclusive`, a value on the limit is **nominal**. So the answer is a configuration choice — [DoubleRange.java L94-104](https://github.com/yamcs/yamcs/blob/master/yamcs-xtce/src/main/java/org/yamcs/xtce/util/DoubleRange.java#L94-L104), [XtceStaxReader `fromXtceComplement`](https://github.com/yamcs/yamcs/blob/master/yamcs-xtce/src/main/java/org/yamcs/xtce/xml/XtceStaxReader.java)
- Yamcs checks the most severe range first and stops at the first match ("giving priority to highest severity") — [ParameterAlarmChecker.java L245-268](https://github.com/yamcs/yamcs/blob/master/yamcs-core/src/main/java/org/yamcs/mdb/ParameterAlarmChecker.java#L245-L268)

**Hysteresis / deadband**
- EPICS `HYST` is an absolute value in engineering units. An alarm that was raised at limit `alev` stays raised while `val >= alev - hyst` on the high side, or `val <= alev + hyst` on the low side. `LALM` remembers which limit raised it. Hysteresis only delays the *return*. — [aiRecord.c](https://github.com/epics-base/epics-base/blob/7.0/modules/database/src/std/rec/aiRecord.c)
- In ISA-18.2 practice, a deadband is "a defined increment of percentage of the range" that the value must clear before the alarm returns to normal. ISA-18.2 §10.5.5.2: "The control system shall provide the capability for implementing the deadband functionality." Starting values in the EEMUA 191 table are flow 5 %, level 2 %, pressure 2 %, temperature 1 % (all **% of range**). Secondary source: the standards are paywalled — [exida blog](https://www.exida.com/Blog/why-should-i-use-an-alarm-deadband)
- COSMOS LIMITS has no hysteresis field, only persistence — [COSMOS telemetry config](https://docs.openc3.com/docs/configuration/telemetry). Yamcs alarm definitions have no deadband either (**VERIFIED**: GitHub code search of `yamcs/yamcs` for "hysteresis" and "deadband" returns nothing, while persistence exists as `AlarmType.minViolations`; 2026-11-14).
- Chattering is the main cause of alarm overload. Wang et al. list "chattering alarms due to noise and disturbance" first of four causes — [Wang, Yang, Chen, Shah 2016, IEEE T-ASE, doi:10.1109/TASE.2015.2464234](https://doi.org/10.1109/TASE.2015.2464234) (abstract)
- A deadband is a trade-off. It removes nuisance alarms but adds "delay in detecting abnormal conditions". Wang et al. pick the optimal width as a "weighted balance" between the two — [Wang, Bai, Wang, Yang 2019, IEEE TIE, doi:10.1109/TIE.2018.2885718](https://doi.org/10.1109/TIE.2018.2885718) (abstract)
- Deadbands and delay timers each remove only one type of false alarm: deadbands handle small deviations, delay timers handle short durations. A deadband followed in series by a delay timer handles both — [Gyasi & Wang 2022, PSEP, doi:10.1016/j.psep.2022.05.006](https://doi.org/10.1016/j.psep.2022.05.006) (abstract)

**Persistence (delay timers)**
- COSMOS persistence is the "Number of consecutive times the telemetry item must be within a different limits range before changing limits state". In code, the counter grows on every frame whose state differs from the current state (any different state), resets on a frame equal to the current one, and gates **every** transition, including back to green. The comment says "going back to green … persistence is ignored", but the code does not do that — [packet.rb L1515-1535](https://github.com/OpenC3/cosmos/blob/main/openc3/lib/openc3/packets/packet.rb#L1515-L1535)
- Yamcs `minViolations` gates only *raising* the alarm, through a `TRIGGERED_PENDING` state. A single in-limits value while pending drops it as a "glitch", and one in-limits value after triggering is already RTN. There is no off-delay — [AlarmServer.java L239-310](https://github.com/yamcs/yamcs/blob/master/yamcs-core/src/main/java/org/yamcs/alarms/AlarmServer.java#L239-L310); R1 notes that it does not change the monitoring result.
- OPC UA (which maps to IEC 62682) defines both directions. "The OnDelay is used to avoid unnecessary Alarms when a signal temporarily overshoots its setpoint". "The OffDelay is used to reduce chattering Alarms…"; the alarm "shall stay active for the OffDelay time and shall not regenerate if it returns to active in that period" — [OPC 10000-9 §5.8.2](https://reference.opcfoundation.org/Core/Part9/v105/docs/5.8.2)
- Delay timers and deadbands lower false and nuisance alarms "significantly". Their cost is detection delay, which Adnan et al. compute with Markov chains and trade against false and missed alarm rates (**VERIFIED**, full text: "can significantly reduce these false and nuisance alarms" p. 1; Markov models of deadband and delay timer pp. 3–4; the trade-off against false and missed alarms p. 5) — [Adnan, Izadi, Chen 2011, J. Process Control, doi:10.1016/j.jprocont.2011.06.019](https://doi.org/10.1016/j.jprocont.2011.06.019); [Izadi et al. 2009, IFAC, doi:10.3182/20090630-4-ES-2003.00108](https://doi.org/10.3182/20090630-4-ES-2003.00108) (abstracts). Chatter can be measured from run-length distributions — [Kondaveeti et al. 2010, CDC, doi:10.1109/CDC.2010.5718086](https://doi.org/10.1109/CDC.2010.5718086) (abstract). An online method removes chattering alarms using alarm durations and intervals — [Wang & Chen 2014, doi:10.1016/j.compchemeng.2014.03.018](https://doi.org/10.1016/j.compchemeng.2014.03.018) (title only, not read). All seven papers are in PaperLab workspace "Nyx alarm management", abstract only; no free PDFs.
- EPICS offers a different tool: `AFTC`, a first-order time-constant filter on the alarm *severity*. It is not an N-frame counter — [aiRecord.c](https://github.com/epics-base/epics-base/blob/7.0/modules/database/src/std/rec/aiRecord.c)

**Rate of change**
- OPC UA has an `ExclusiveRateOfChangeAlarmType` next to level alarms, so rate of change is a normal limit alarm on a derived value — [OPC 10000-9 §5.8.19](https://reference.opcfoundation.org/Core/Part9/v105/docs/5.8.19)
- The OLS slope over k samples has variance σ²/Σ(tᵢ−t̄)² — [simple linear regression](https://en.wikipedia.org/wiki/Simple_linear_regression#Normality_assumption). At dt = 0.1 s that gives the standard deviation of the slope as a multiple of the sample noise σ (own calculation): k = 10 (0.9 s): **OLS 1.10 σ/s**, end-point difference 1.57 σ/s, adjacent-frame difference **14.1 σ/s**. k = 20: OLS 0.39, end-point 0.74.
- Detection lag (own simulation, OLS over 10 samples, ramp of −0.4 kPa/s): the estimated slope is 0.17 at 0.4 s, 0.34 at 0.7 s and the full 0.40 from 0.9 s. With a ROC limit of 0.15 kPa/s and N = 3, the alarm comes at about 0.6 s. A yellow_low of 98.0 kPa is reached after 8.25 s. So ALM-04 "before the absolute limit" has about 7.5 s of margin.
- Windows that overlap give strongly correlated slopes, so N consecutive ROC violations are **not** independent trials. Persistence filters ROC noise less than it filters level noise (**VERIFIED by simulation**, 2026-11-14: white noise at 10 Hz, LS slope over 1 s, thresholds set for the same 5 % per-sample exceedance; 3-in-a-row survives 5.2e-3 for ROC vs 1.3e-4 for level, 39×; i.i.d. p³ = 1.25e-4).
- Python ≥ 3.10 has `statistics.linear_regression(x, y) → (slope, intercept)`, so no numpy is needed — [Python docs](https://docs.python.org/3/library/statistics.html#statistics.linear_regression)

**Life cycle**
- ISA-18.2 states (secondary sources): Normal, Unacknowledged, Acknowledged, **RTN Unacknowledged**, plus Latched, Shelved, Suppressed-by-design and Out-of-service. RTN-unacked goes back to Unacknowledged if the condition recurs — [pyautomation ISA states](https://pyautomation.readthedocs.io/en/latest/Developments_Guide/core/alarms/), [Iconics ISA 18.2 diagram](https://documentation.iconics.com/v10.97.3/Content/Alarming/Alarm%20Server/Alarm%20References/alarm-state-transition-diagram.htm)
- Yamcs: "an alarm clears if the operator has acknowledged it and the parameter returned to normal". Latching and auto-ack are optional — [Yamcs alarms](https://docs.yamcs.org/yamcs-server-manual/processors/alarms/). There is one active alarm per parameter. A severity increase calls `notifySeverityIncrease`, but `trigger()` only acts when the alarm is not yet triggered, so **an acked alarm that gets worse stays acked** in Yamcs — [AlarmServer.java L299-302](https://github.com/yamcs/yamcs/blob/master/yamcs-core/src/main/java/org/yamcs/alarms/AlarmServer.java#L299-L302), [ActiveAlarm.java `trigger`](https://github.com/yamcs/yamcs/blob/master/yamcs-core/src/main/java/org/yamcs/alarms/ActiveAlarm.java)
- OPC UA: High→HighHigh is a modelled transition (`HighToHighHigh`) with a `HasEffect` reference to AlarmConditionType. That means it emits an event. Whether ack is reset is not stated (**VERIFIED**: Part 9 v1.05 §4.3 "The specific state changes that result in a change to the state depend on a Server's implementation"; only ReAlarmTime mandates a return to unacknowledged. D-ALM-08's re-ack on escalation is a permitted server choice) — [§5.8.19](https://reference.opcfoundation.org/Core/Part9/v105/docs/5.8.19). OPC UA `ReAlarmTime`: "if it has been acknowledged it shall return to unacknowledged" and "generate a new Alarm for it (as if it just went into alarm)" — [§5.8.2](https://reference.opcfoundation.org/Core/Part9/v105/docs/5.8.2)
- Shelving is an operator action, suppression comes from the system, and both are separate from Disabled — [OPC 10000-9 §4.8](https://reference.opcfoundation.org/Core/Part9/v105/docs/4.8)
- Flood: more than 10 new alarms per 10 min per operator, ending when an interval has fewer than 5; target < 1 % of time in flood (**PARTLY VERIFIED**, secondary sources only: the start/end rule (>10 new in 10 min, ends below 5) is credited to EEMUA 191 by VanCamp, an ISA-18.2 committee member; "<1 % of time in flood" and "≤10 per 10 min" appear in Siemens' reproduction of the ISA-18.2 metrics table (processonline.com.au). "Per operator": **STILL UNVERIFIED**; the standards themselves are paywalled) — [Chemical Engineering, "Alarm management by the numbers"](https://www.chemengonline.com/alarm-management-numbers/)

**Hot reload / replay**
- `watchfiles` falls back to polling when notifications are unavailable, can be forced with `WATCHFILES_FORCE_POLLING`, and polls every 300 ms by default — [watchfiles API](https://watchfiles.helpmanual.io/api/watch/). Inotify losing a file that editors replace by rename: **VERIFIED** (inotify(7): IN_DELETE_SELF / IN_IGNORED when the watched inode goes; notify-rs: some editors "create a new one and replace the old one"). Docker Desktop bind mounts missing events: **STILL UNVERIFIED** (nothing documented for VirtioFS). Moot for the design: D-ALM-12 hashes `mission/` every 1 s instead of watching.
- Yamcs replay re-runs the same processor but uses the *current* MDB limits, not the ones that applied at the time — R1 §4. Pinning limits by hash is our improvement.

## Decision candidate

**1. Boundary rule (ALM-01).** *A value on a limit belongs to the more severe band* (`v <= low`, `v >= high`), as in EPICS and COSMOS. The test checks the classification with a fresh state, N = 1, `cabin_pressure_kpa` (red 95 / yellow 98 / 104 / red 106), hysteresis 2 % of span (d = 0.22):

| value | case | expected | | value | case | expected |
|---|---|---|---|---|---|---|
| 94.99 | just outside red_low | RED | | 103.99 | just inside yellow_high | NOMINAL |
| 95.00 | on red_low | RED | | 104.00 | on yellow_high | YELLOW |
| 95.01 | just inside red_low | YELLOW | | 104.01 | just outside yellow_high | YELLOW |
| 97.99 | just outside yellow_low | YELLOW | | 105.99 | just inside red_high | YELLOW |
| 98.00 | on yellow_low | YELLOW | | 106.00 | on red_high | RED |
| 98.01 | just inside yellow_low | NOMINAL | | 106.01 | just outside red_high | RED |
| 98.21 | from YELLOW, inside deadband | YELLOW | | 95.21 | from RED, inside deadband | RED |
| 98.23 | from YELLOW, past deadband | NOMINAL | | 95.23 | from RED, past deadband | YELLOW |

Compare raw floats. Limits in the table are exact literals. Never test exactly on `limit ± d`, because float addition makes that edge fragile. Load-time validation requires `red_low < yellow_low < yellow_high < red_high`. NaN or inf is rejected by schema validation (ING-02).

**2. Hysteresis (ALM-02).** *`hysteresis_pct` is a percentage of the span `red_high − red_low`, turned once into an absolute deadband `d`* (ISA/EEMUA "% of range"; EPICS applies it in absolute units). It only delays a move to a *less severe* band: a low alarm at limit L returns when `v > L + d`. **Rejected: % of the limit value.** It gives d = 0 at a limit of 0 (rates in dps, temperatures in °C), flips sign for negative limits, and changes meaning with the unit offset (°C vs K). A one-sided parameter has no span, so the schema then requires `hysteresis_abs`. ALM-02 test: N = 1 (so only hysteresis is tested), noise ±1 % of span around 98.0, hysteresis 2 % → exactly 1 RAISE.

**3. Persistence (ALM-03).** *Per check, keep a ring of the last N raw levels. Escalate to L when all N are ≥ L; de-escalate to L when all N are ≤ L.* This is an on-delay plus an off-delay (OPC UA), on top of the deadband, which is the series design in Gyasi & Wang. Red does not need its own N: Y,R,R,R reports RED after 3 reds. A spike N,R,N never alarms. ALM-05 latency is measured from the **N-th** frame (`trigger_seq`), and the README states the N × 100 ms on-delay. **Gap rule (A-R1-3):** a `GAP`, `SEQ_RESET` or `STALE` clears the persistence ring and the ROC window but **keeps the debounced level and the alarm**. Missing data never clears an alarm, so a value can show RED + STALE, and after the link returns it takes N fresh frames to change level. Rejected: the COSMOS counter (counts any differing state, so the reported level depends on order); the Yamcs on-delay only (clears on one good frame, so it chatters on the way out).

**4. Rate of change (ALM-04).** *OLS slope (`statistics.linear_regression`) over the samples with `t_onboard` in the last `window_s` (default 1.0 s).* It needs at least 80 % of the expected samples, otherwise it gives "no opinion": no level change and no push into the ring. It is evaluated as |slope| against `limit_per_s` with the same hysteresis-% (of the ROC limit) and N. It uses `t_onboard`, never arrival time, because batching and broker jitter would add a false slope. The window is reset on GAP or SEQ_RESET. Rejected: adjacent difference (13× noisier); end-point difference (1.4× noisier at the same lag, and one bad sample sets the result).

**5. Life cycle (ALM-06, Q2, UI-05, OPS-2).** There is one alarm instance per `(vehicle, param)`. Its severity is the max of the param's check levels, and `trigger_check` is recorded. So a leak raises ROC-yellow, then limit-yellow (no event), then red, which is **one** alarm that escalates. `alert` (audible plus banner) is a column on the transition row, true only when the alarm enters RED above `acked_severity`. The UI alerts once per transition key, which also holds after a reconnect. Spec mapping: ACTIVE = UNACK, ACKED = ACKED, CLEARED = closed. RTN_UNACK is shown as "CLEARED – NOT ACKED" (answers A-R1-5: a separate state, ISA vocabulary, same behaviour as Yamcs).

| state \ event | RAISE(s) | ESCALATE→RED | DEESCALATE→YELLOW | RTN | ACK(user, note) |
|---|---|---|---|---|---|
| (none) | → UNACK(s); alert if s = RED | – | – | – | – |
| UNACK(s) | – | → UNACK(RED); alert | → UNACK(YELLOW) | → RTN_UNACK | → ACKED; acked_sev = s |
| ACKED(s) | – | RED > acked_sev → **UNACK(RED); alert** (Q2); else stay ACKED | → ACKED(YELLOW), acked_sev kept | → CLEARED (by system) | no-op (idempotent) |
| RTN_UNACK | → UNACK(s); alert if s = RED | – | – | – | → CLEARED (by user) |

Every row stores who (`system` or user), `t_onboard` of the trigger frame, `processed_at`, note (ACK note is required, OPS-2), and `limits_sha256`. ACK is idempotent by a client UUID (R5). Keeping `acked_sev` means red → yellow → red after an ack does not alert again. **Out of scope:** shelving, latching, suppression, re-alarm timers (named in the README as next steps). Flood control comes only from per-param aggregation, deadband and persistence; the session report adds alarms per 10 min.

**6. Pluggable checks (ALM-07) and rebuildable state.** All checks are pure. The engine is `step(state, frame, limits) -> (state, events)`.

```python
class Level(IntEnum): NOMINAL = 0; YELLOW = 1; RED = 2
@dataclass(frozen=True)
class Sample: seq: int; t: float; value: float          # t = t_onboard, epoch seconds

class Check(Protocol[S]):
    kind: ClassVar[str]                                  # key used in limits.yaml `checks[].type`
    Config: ClassVar[type[BaseModel]]                    # pydantic model, validated at load
    def __init__(self, cfg: BaseModel) -> None: ...
    def initial(self) -> S: ...                          # frozen, JSON-serialisable
    def step(self, state: S, s: Sample, current: Level) -> tuple[S, Level | None]: ...
        # raw level given the debounced `current` (needed for hysteresis); None = no opinion

CHECKS: dict[str, type[Check]] = {}
def register(cls): CHECKS[cls.kind] = cls; return cls    # @register on each class
```
`nyx/alarms/checks/__init__.py` imports every module in the package with `pkgutil.iter_modules`. "New class + registration only" therefore means adding one file, with no worker edit. Persistence, aggregation and the life cycle live in the engine, not in the checks.
**Rebuild (R4 takeover, DAT-06 replay):** with hysteresis the state has unbounded memory, so re-feeding K frames from NOMINAL can differ from live when a value sits inside the deadband. The fix is one mechanism for both cases. Log every per-check debounced-level change as an event (`LEVEL`, keyed like R5). To rebuild after frame s: warm up over the stored frames `s−K+1..s`, with K = max(N, ROC window samples). During warm-up, `current` for each frame comes **from the `LEVEL` log**, not from the engine, and the engine only refills the persistence ring and the ROC window. From frame s+1 the engine decides as usual. Every input then matches live and step is pure, so the output is identical. Warm-up emits nothing, so there are no duplicate transitions. `ON CONFLICT DO NOTHING` (R5) remains the second guard. Takeover uses s = last committed seq; replay uses s = the last frame before the window start. Rejected: a JSON snapshot of state per batch (a second source of truth, and replay would need snapshot history anyway).

**7–8. Limits hot reload and versioning (ALM-08, Q6).** Every worker polls `limits.yaml` once per second with stdlib only: `stat`, then on change read the bytes and compute `sha256(bytes)` (the same value as `sha256sum`). If the hash is new: `yaml.safe_load` → pydantic validation → `INSERT INTO limits_versions(sha256 PK, content, first_seen_at) ON CONFLICT DO NOTHING`. An invalid file keeps the old set and emits `LIMITS_REJECTED` (UI banner). The owner switches at its next batch boundary and writes `LIMITS_APPLIED(vehicle, epoch, first_seq, sha256)`. Activation takes 1–2 s, well inside 5 s. **In-flight state:** debounced levels and open alarms are kept. The persistence ring is reset, because its levels were classified under the old limits. The ROC window (raw values) is kept. A removed check is forced to NOMINAL (RTN with reason `LIMITS_CHANGED`), and an added check starts NOMINAL. Every transition row carries `limits_sha256`, and the UI shows the first 8 hex digits. **Replay** follows the `LIMITS_APPLIED` timeline and takes the content from `limits_versions` by hash, so limits change at the same `seq` as live (Q4, Q6). `limits_versions` is never pruned. Rejected: inotify/watchfiles (one more dependency, and rename and bind-mount issues); a hash of the parsed content (the operator could not check it with `sha256sum`); resetting all state on reload (alarms would flap during a test).

```yaml
# limits.yaml — draft schema (values illustrative, not from the spec)
version: 1
defaults: {hysteresis_pct: 2.0, persistence: 3}
parameters:
  cabin_pressure_kpa:
    checks:
      - {type: limit, red_low: 95.0, yellow_low: 98.0, yellow_high: 104.0, red_high: 106.0}
      - {type: roc, limit_per_s: 0.15, window_s: 1.0, severity: YELLOW, persistence: 3}
  rate_x_dps:
    checks:
      - {type: limit, red_low: -5.0, yellow_low: -2.0, yellow_high: 2.0, red_high: 5.0, hysteresis_pct: 1.0}
  bus_current_a:
    checks:
      - {type: limit, yellow_high: 8.0, red_high: 10.0, hysteresis_abs: 0.1}   # one-sided → abs required
```
`hysteresis_pct` and `hysteresis_abs` are mutually exclusive. `persistence` must be ≥ 1, and the check value overrides `defaults`. Unknown keys are an error (`extra="forbid"`).

## Risks / what would make us switch

- **Spec reading of ALM-02 "±1 %"**: if the board means ±1 % of the *limit value* (±0.98 kPa), hysteresis must be ≥ 9 % of span for cabin pressure. That is still correct but large, and the deciding factor is the noise level we choose in the simulator.
- **Boundary rule differs from Yamcs/XTCE** (inclusive in-limits). Importing an XTCE MDB later needs a per-limit `inclusive` flag.
- **ROC false alarms**: correlated windows plus unknown simulator noise σ. Rule: `limit_per_s ≥ 5 × 1.10 σ` for a 1 s window. With 0.15 kPa/s that needs σ ≤ 0.027 kPa. If σ is larger, raise the limit (0.3 kPa/s still catches 0.4 kPa/s) or lengthen the window, which costs lag.
- **Re-alarm on escalation (Q2)** deviates from Yamcs, which keeps the ack. If operators find it noisy, switch to "notify, keep ack" (Yamcs).
- **Per-param aggregation** hides a second check that goes red while the first one is acked yellow. That is accepted: escalation re-alarms.
- **Rebuild via `LEVEL` events** adds one event row per debounced level change. If that grows (chatter), it is visible in the report, which is useful. Switch to per-batch snapshots only if takeover warm-up exceeds 1 s.
- **Workers polling at different moments** switch limits on different vehicles at slightly different times. This is fine because the switch is logged per vehicle.

## Open questions → assumptions.md

- **A-R8-1** (ASSUMED) On-limit = more severe band (EPICS, COSMOS). Boundary table above.
- **A-R8-2** (OPEN) "±1 %" in ALM-02 = % of span, not % of limit value. `hysteresis_pct` = % of `red_high − red_low`.
- **A-R8-3** (ASSUMED) Persistence N gates escalation, de-escalation and clearing alike. ALM-05 latency is counted from the N-th frame.
- **A-R8-4** (ASSUMED) GAP, SEQ_RESET and STALE reset the counters and the ROC window. They never clear an alarm.
- **A-R8-5** (OPEN, Q2) Acked yellow → red becomes unacknowledged again and alerts once. Red → yellow → red after an ack does not alert again.
- **A-R8-6** (ASSUMED) One alarm per (vehicle, param). Only RED alerts audibly. Yellow is visual (OPS-2 "sounds once").
- **A-R8-7** (ASSUMED) RTN before ack → RTN_UNACK, shown as "CLEARED – NOT ACKED". Ack closes it. (Closes A-R1-5.)
- **A-R8-8** (OPEN) The ROC limit is absolute |slope|. Does a pressurisation test need `direction: falling` to avoid alarms on purpose-made rises?
- **A-R8-9** (ASSUMED) Shelving, latching, suppression and re-alarm timers are out of scope.
- **A-R8-10** (OPEN → R7) Replay seeds levels from `LEVEL` events in Postgres. Must `alarm_events` and `limits_versions` outlive the 24 h hot window, or be archived next to the Parquet files (`limits/<sha256>.yaml`)? Proposal: never prune them (small).
- **A-R8-11** (OPEN → R7/R9) Does replay overlay the live ACKs from the audit log, or show frame-driven transitions only?

## Corrections from R14 (2026-11-14)

Appended after the paper-evidence round [R14](R14-paper-evidence.md) (§3 and §4). The text above is left as written; where it disagrees with this section, this section wins. Verdicts are R14's reading of the full text (PaperLab model not used); pages are PDF pages.

| R8 claim | Paper | R14 verdict | Page |
| --- | --- | --- | --- |
| "Chattering is the main cause of alarm overload … first of four causes" | Wang, Yang, Chen & Shah 2016 | **Partly supported**: chattering is listed first of **four** main causes, not "the main cause"; for floods the true reason is "too many **correct** alarms" in a short time, from abnormality propagation. Read: "one of four main causes, listed first" | p. 1, 7, 14 (full text) |
| Deadband trade-off: removes nuisance alarms, adds detection delay, optimum = weighted balance | Wang, Bai, Wang & Yang 2019 | **Supported (abstract only)**; owner upload recorded, full text not yet ingested | abstract |
| Deadbands handle small deviations, delay timers short durations; a deadband followed by a timer in series handles both | Gyasi & Wang 2022 | **Supported** | p. 1–2 (full text) |
| Delay timers **and deadbands** lower false and nuisance alarms "significantly"; detection delay traded against false/missed rates | Izadi et al. 2009 | **Partly supported**: the trade-offs yes (p. 6); "significantly" is said of delay only (p. 5); deadband is the least effective technique (p. 6). The Markov-chain analysis is also in Izadi (p. 4) | p. 4–6 (full text) |
| Detection delay computed with Markov chains | Adnan, Izadi & Chen 2011 | **VERIFIED** (2026-11-15, owner upload ingested): Markov chains for deadband (Fig. 3) and delay timer n = 3, m = 2 (Fig. 4); "significantly reduce" is Adnan's own wording (p. 1) | p. 1, 3–5 (full text) |
| Chatter can be measured from run-length distributions | Kondaveeti et al. 2010 | **Supported** | p. 3 (full text) |
| Online method removes chattering alarms using alarm durations and intervals ("title only, not read") | Wang & Chen 2014 | **Supported**; "title only" is out of date | p. 4, p. 9 (full text) |
| Flood: > 10 new alarms / 10 min, ends < 5 (secondary, UNVERIFIED) | Chemical Engineering magazine → Wang 2016 | **Now supported by a peer-reviewed source** (Wang 2016, citing Rothenberg 2009 and ISA/EEMUA); the "< 1 % of time in flood" target is still UNVERIFIED | Wang 2016 p. 7 |
| §5 "Flood control comes only from per-param aggregation, deadband and persistence" | Gyasi & Wang 2022; Wang 2016; Beebe 2013 | **Contradicted**: those tools remove *false* alarms (Gyasi p. 2); floods are mostly *correct* alarms (Wang p. 7); rationalization doesn't control floods (Beebe, abstract). Read: "Nyx has no flood control; D-ALM-15 measures floods" | Gyasi p. 2, Wang p. 7 |
| §3 persistence "is the series design in Gyasi & Wang" (values implied) | Gyasi & Wang 2022; Wang & Chen 2014; Izadi 2009 | **Structure supported, values not**: literature delays are 15 s (pressure) and 60 s (temperature) (EEMUA via Izadi p. 4) or m = 20 s by default (Wang & Chen p. 4), against Nyx's 0.3 s. N = 3 is the spec's value (ALM-03/05); the tuning study D-ALM-16 checks it on simulator noise | Izadi p. 4; Wang & Chen p. 4 |
| "All seven papers … abstract only; no free PDFs" | — | **Out of date**: five now have full text (Wang 2016, Gyasi 2022, Izadi 2009, Kondaveeti 2010, Wang & Chen 2014) | — |
| "Persistence filters ROC noise less than it filters level noise (derivation, UNVERIFIED by test)" | simulation (R-verify, 2026-11-14) | **VERIFIED**: same 5 % per-sample exceedance, 3-in-a-row survives 39× more often for the 1 s LS-slope ROC than for level (5.2e-3 vs 1.3e-4). D-ALM-16's sweep remains the empirical check | — |

Updated tags: Wang 2016 → `verified: full text p. 1, 7, 14`; Gyasi 2022 → `p. 1–2`; Izadi 2009 → `p. 4–6, partly`; Kondaveeti 2010 → `p. 3`; Wang & Chen 2014 → `p. 4`. Design consequences: [design.md §13](../design.md#13-research-rounds-r14--r15-disposition-with-r16-r17-r18) (D-ALM-02, D-ALM-03, D-ALM-13, D-ALM-15, D-ALM-16).

## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by simulation. Marked in place above (the R14 correction table above stays as written, except the ROC row).

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Yamcs alarms have no deadband / hysteresis | VERIFIED | `yamcs/yamcs` code search: no "hysteresis" / "deadband"; persistence exists as `AlarmType.minViolations` | D-ALM-02 (we add hysteresis Yamcs lacks) |
| Persistence filters ROC noise less than level noise | VERIFIED by simulation | 5 % per-sample exceedance: 3-in-a-row 5.2e-3 (ROC) vs 1.3e-4 (level), 39× | D-ALM-03, D-ALM-05, D-ALM-16 (the ROC threshold, not N, must carry the noise rejection) |
| OPC UA: does High→HighHigh reset the ack? | VERIFIED "not stated / server's choice" | Part 9 v1.05 §4.3; only ReAlarmTime (§5.8.2) mandates unack | D-ALM-08 is a permitted choice; no change |
| Flood > 10 / 10 min, ends < 5; < 1 % of time in flood | PARTLY VERIFIED (secondary) | start/end: EEMUA 191 via VanCamp (ISA-18.2 committee member); < 1 % and ≤ 10 per 10 min: Siemens' reproduction of the ISA-18.2 metrics table; "per operator": STILL UNVERIFIED (standards paywalled) | D-ALM-15 `peak_10min`, `time_in_flood` |
| inotify loses a file replaced by rename | VERIFIED | inotify(7) IN_DELETE_SELF / IN_IGNORED; notify-rs known problems | D-ALM-12 hashes `mission/` every 1 s instead (moot) |
| Docker Desktop bind mounts miss events | STILL UNVERIFIED | nothing documented for VirtioFS | moot (D-ALM-12 polls) |

CONTRADICTED: none.

### Verification pass, owner uploads (2026-11-15)

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Delay timers and deadbands lower false and nuisance alarms "significantly"; delay computed with Markov chains, traded against false/missed rates | VERIFIED | Adnan 2011 p. 1, 3–5 (full text) | D-ALM-02/03 rationale |
| Deadband trade-off, optimum = weighted balance | STILL UNVERIFIED | Wang 2019 uploaded as a scan without text layer; needs OCR and re-upload | D-ALM-16 (bench re-run) |

CONTRADICTED: none.
