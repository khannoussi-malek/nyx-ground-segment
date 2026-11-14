# R20 – Alarm noise margins from Xu 2012 and Adnan 2011: what the full texts change

**Drives:** D-ALM-02, D-ALM-03, D-ALM-16, §3.5 ICD-B-06   **Answers review Q:** 2 (alarm tuning)

PaperLab workspace: **Nyx ground segment design**. The owner uploaded the full texts on 2026-11-15. Xu et al. 2012 (IEEE T-ASE, 10.1109/TASE.2011.2176490) and Adnan, Izadi & Chen 2011 (J. Process Control, 10.1016/j.jprocont.2011.06.019) are now readable. Wang et al. 2019 (10.1109/TIE.2018.2885718) was uploaded as a scan; it was OCR'd and re-uploaded the same day and is now full text (used in F3). Verification results are marked in R8 and R14.

## Question

D-ALM-16 plans a simulation study to check that N = 3 and the deadbands sit on the false-alarm / missed-alarm / delay frontier. With Xu's closed-form chain, what can we know now without running it, and does the study as specified answer a useful question?

## Findings

### F1 – Our engine's persistence is exactly Xu's n-sample on/off delay (confirmation, and a test oracle)

- Xu models an alarm that is "raised/cleared if and only if more than [n] consecutive samples … are larger/smaller than the trip point" as a Markov chain with n no-alarm and n alarm states (pp. 3–5, Figs. 3–5). It gives the FAR (time in alarm), MAR and AAD (average alarm delay) for **i.i.d.** samples (p. 3). Adnan models deadband and delay timer the same way (pp. 3–4).
- The Nyx engine (`_slot_step` ring + `Limit`, deadband → 0) was checked against that chain with a Monte Carlo of 400,000 samples per setting, against the real code:

| N | p (sample beyond trip) | Time in alarm, chain / engine | Raises per sample, chain / engine |
| --- | --- | --- | --- |
| 1 | 0.2 | 0.2000 / 0.2007 | 0.16000 / 0.16045 |
| 3 | 0.2 | 0.0298 / 0.0294 | 0.00626 / 0.00624 |
| 3 | 0.5 | 0.5000 / 0.4970 | 0.03571 / 0.03560 |
| 5 | 0.4 | 0.1554 / 0.1521 | 0.00524 / 0.00514 |

- **Change (D-ALM-16 tooling test, D-ENG-14):** add `unit/test_alarm_tuning.py::test_engine_matches_xu_chain`, the table above as a test with tolerances (0.01 absolute on time in alarm, 5 % relative on raise rate). It runs in about 10 s and fails if the persistence semantics ever drift from the model the study relies on. The chain itself is about 25 lines of stdlib (stationary distribution of a 2N-state chain; code below).

### F2 – As specified, the D-ALM-16 nominal sweep can't tell N apart, and will mark N = 3 "dominated" (gap, medium: saves the study's main run)

- The simulator's noise (`nominals.py`) puts every yellow limit **40–200 σ** from nominal:
  - bus voltage: 2 V / 0.05 = 40 σ;
  - cabin pressure: 135 σ;
  - rates: 200 σ.
- By Xu's chain the false-raise rate is about p^N per sample, with p = Q(40) ≈ 10⁻³⁵⁰. So every cell of the planned grid (N = 1..5 × deadband 2–15 %) gives **0 false raises per hour**. The nominal runs measure nothing.
- With the false-alarm axis flat, the frontier is decided by delay alone. **N = 2** rejects the ALM-03 one-frame spike and is 0.1 s faster than N = 3. So the acceptance rule as written ("N = 3 on or near the frontier, … a dominated value raises an assumption entry") will predictably report N = 3 as dominated. That would be a statement about the simulator's noise, not about the system.
- **Change: sweep the noise, not only N × deadband.** For each parameter, report the largest σ the configuration tolerates (i.i.d. Gaussian, 10 Hz, ≤ 1 event per 8 h shift):
  - **Nuisance raise at nominal:** the distance to the nearest yellow limit over z, for the target rate.
  - **Chatter at the limit (ALM-02 situation):** the value parked on the limit; one clear-and-re-raise cycle needs N consecutive samples beyond the deadband: σ ≤ d / z_N, with z_N = −Φ⁻¹((1 / 288,000)^(1/N)) = 4.50 (N = 1), 2.17 (N = 3).

| Parameter | Sim σ | Nuisance raise, N = 1 / **N = 3** (× sim σ) | Deadband d | Chatter at the limit, **N = 3** (× sim σ) |
| --- | --- | --- | --- | --- |
| cabin_pressure_kpa | 0.02 | 0.58 / **1.20** (60×) | 1.65 | **0.76** (38×) |
| cabin_temp_c | 0.05 | 1.08 / **2.22** (44×) | 0.45 | **0.21** (4.2×) |
| bus_voltage_v | 0.05 | 0.43 / **0.89** (18×) | 0.48 | **0.22** (4.4×) |
| bus_current_a | 0.05 | 0.67 / **1.39** (28×) | 0.20 | **0.092 (1.8×)** |
| tank_pressure_bar | 0.02 | 0.65 / **1.33** (67×) | 0.40 | **0.18** (9.2×) |
| rate_x/y/z_dps | 0.01 | 0.43 / **0.89** (89×) | 0.20 | **0.092** (9.2×) |

- **Reading the table:**
  - N = 3 roughly **doubles the noise** a limit tolerates before it raises nuisance alarms, compared with N = 1. That is the quantitative case for keeping the spec's N = 3, which the current study can't make.
  - **Chatter at the limit is the binding margin everywhere**, not false raises at nominal.
- **Change to the D-ALM-16 acceptance:**
  - Replace "on or near the frontier with 0 false raises" with this margin table, computed from the chain for limit checks.
  - Keep Monte Carlo only where Xu's i.i.d. assumption fails: the **ROC** check (overlapping 1 s windows are strongly correlated; R8's 39× result) and the deterministic fault profiles (leak, overheat, the `isolate_valve` RTN).
  - N = 3 stays the spec's value. The study reports its margin rather than judging it against a flat frontier.

### F3 – Bus current chatters at twice the simulator's noise (gap, medium; bench-facing)

- **Prediction vs engine:** the chain predicts chatter at the limit from 1.8× the simulator's σ. The real engine with the shipped config (`yellow_high 8.0`, `hysteresis_abs 0.2`, N = 3, value parked at 8.0 A) gives **0 / 6 / 925** clear-and-re-raise cycles per 8 h at 1× / 2× / 4× σ. Cabin temperature first chatters at 4× (1 cycle per 8 h). Cabin pressure: 0 even at 4×.
- **Why it happens:** the D-ALM-02 floor rule (1.5 × 1 % of the limit) doesn't depend on noise. The literature designs deadband and delay from the **noise distribution** (Xu §IV; Adnan p. 5: "An acceptable design should not only minimize false and m[issed alarms]…"; Gyasi & Wang 2022 p. 2: joint design). On the simulator the floor is enough. On a real bench, a current sensor with σ ≈ 0.1 A, which is ordinary, would chatter whenever the load sits near 8 A.
- **Change (no code now; §3.5 ICD-B-06, the bench-noise re-run before the first campaign):**
  1. Estimate σ per parameter from a recorded nominal run (sample standard deviation of the residual around a moving median).
  2. Pass rule per limit check: **d ≥ 2.2 σ** and **nearest yellow distance ≥ 2.3 σ**, the N = 3 / 8 h values above; a failing parameter gets a wider deadband in `mission/limits.yaml` before the campaign.
  3. Check the residual's autocorrelation. If lag-1 autocorrelation is above ~0.2, the samples are not i.i.d. and the chain is optimistic, so the chain is not valid. Use **Wang et al. 2019** instead, which is written for this case: the IID assumption "is not satisfied by a large number of signals, for which it is often observed that several consecutive data samples are overpassing alarm thresholds" (p. 2). Its method, from recorded data: (a) a **deadband index** from normalized alarm durations and deviations; above 45° a deadband is the right tool, otherwise a delay timer (p. 1–2); (b) the deadband width at the best weighted balance between the nuisance-alarm duration ratio and the pseudo-detection delay, which beat IID-based designs on their industrial example (p. 2). Correlated noise also weakens N = 3, since consecutive samples exceed together (R8: 39× for the correlated ROC slope), so for such a signal the deadband, not N, has to carry the margin.
- **Optional now:** raise bus current's `hysteresis_abs` from 0.2 to 0.3 A. That gives 2.8× margin at the simulator noise, and the floor rule still holds. It is the owner's call: it changes a shipped value with no bench evidence yet, so the default is no change, and the table goes into `docs/perf/alarm-tuning.md`.

### F4 – Detection delay grows with noise (confirmation, one line in the study)

- With the simulator's noise, a limit crossing is detected **exactly N samples** later (Xu's AAD with no missed samples = N; 0.3 s at N = 3, inside ALM-05).
- Near the margin it is longer. With 10 % / 30 % of post-crossing samples still inside the limit, the expected delay is 3.7 / 6.4 samples (run-of-N waiting time, Xu Prop. 3 setting).
- Report AAD at the σ found in F2, so the bench re-run also shows the cost of a wider deadband or a larger N.

## Code (for `perf/alarm_tuning.py`)

```python
def stationary(n: int, p: float, iters: int = 3000) -> list[float]:
    """Xu 2012 Figs. 3-4: states 0..n-1 no-alarm with i exceedances in a row, n..2n-1 alarm."""
    k = 2 * n; T = [[0.0] * k for _ in range(k)]
    for i in range(n):
        T[i][i + 1 if i + 1 < n else n] += p; T[i][0] += 1 - p
        T[n + i][n + i + 1 if i + 1 < n else 0] += 1 - p; T[n + i][n] += p
    pi = [1.0 / k] * k
    for _ in range(iters):
        pi = [sum(pi[a] * T[a][b] for a in range(k)) for b in range(k)]
    return pi

def far_and_raise_rate(n: int, p: float) -> tuple[float, float]:
    pi = stationary(n, p)
    return sum(pi[n:]), pi[n - 1] * p   # time in alarm (Xu's FAR), raises per sample
```

The full script, with the engine Monte Carlo and the margin table, is in the researcher session's scratchpad (`xu_chain.py`, `chatter.py`) and can be handed over.

## Decision candidate

- **Chosen:**
  - F1: the engine-vs-chain test.
  - F2: D-ALM-16 reports noise margins, with analytic limit checks and Monte Carlo only for ROC and fault profiles.
  - F3: the bench pass rule d ≥ 2.2 σ and distance ≥ 2.3 σ in ICD-B-06.
- **Because:**
  - The current study would spend its main run proving that the simulator is quiet, and then flag N = 3 because of it.
  - The margin table is the number that matters before a real campaign, and it costs less to produce.
- **Rejected:**
  - Changing N: the spec fixes N = 3, and F2 shows it doubles the noise margin.
  - A noise-based deadband check at load time: σ isn't known at load.
  - Raising bus current's deadband now without bench data: left to the owner (F3, optional).

## Open questions → assumptions.md

- A-R20-1: The nuisance budget is "≤ 1 per 8 h shift per parameter" (≈ 1 / 288,000 samples). The owner may prefer per campaign day. The table scales with z: for ≤ 1 per 24 h, z_3 = 2.31 instead of 2.17.
- A-R20-2: Bus current `hysteresis_abs` 0.2 → 0.3? Default: keep 0.2 until bench noise is measured.

## Sources (PaperLab: Nyx ground segment design)

Xu, Wang, Izadi & Chen 2012, Performance Assessment and Design for Univariate Alarm Systems Based on FAR, MAR, and AAD, IEEE T-ASE, 10.1109/TASE.2011.2176490 (full text, owner upload) · Adnan, Izadi & Chen 2011, On expected detection delays for alarm systems with deadbands and delay-timers, J. Process Control, 10.1016/j.jprocont.2011.06.019 (full text, owner upload) · Gyasi & Wang 2022, 10.1016/j.psep.2022.05.006 · Wang, Bai, Wang & Yang 2019, Indexing and Designing Deadbands for Industrial Alarm Signals, IEEE TIE, 10.1109/TIE.2018.2885718 (full text after OCR)
