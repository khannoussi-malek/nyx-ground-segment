"""Deadband / persistence tuning study (D-ALM-16, R20): `uv run python -m perf.alarm_tuning`.

Limit checks are judged analytically (Xu 2012 chain, R20 F1/F2); Monte Carlo through the real
engine only where the i.i.d. assumption fails: the ROC check and the deterministic fault profiles.
Simulator noise only: re-run on recorded bench noise before the first campaign (ICD-B-06).
"""

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import NormalDist

from gs.alarms.checks import Check
from gs.alarms.checks.limit import Limit, LimitCheck, deadband, floor
from gs.alarms.checks.roc import Roc, RocCheck
from gs.alarms.contract import Transition
from gs.alarms.engine import CompiledSet, EngineState, initial_state, step
from gs.alarms.quality import QEvent, quality
from gs.common.missiondb import load_dir
from gs.simulator.faults import LEAK_KPA_PER_S, OVERHEAT_C_PER_S
from gs.simulator.nominals import NOMINALS

GRID_N = (1, 2, 3, 4, 5)
GRID_DEADBAND_PCT = (2, 4, 6, 8, 10, 12, 15)  # cabin pressure
GRID_ROC_LIMIT = (0.05, 0.10, 0.15, 0.20, 0.25)  # kPa/s, shipped 0.15
NOMINAL_HOURS = 8
ISOLATE_RATE = 0.5  # kPa/s, D-SIM-10
PERIOD_S = 0.1
ALM05_S = 0.3
MIN_ROC_MARGIN_S = 5.0
SHIPPED = ("deadband", 3, 15)
DOC = Path(__file__).resolve().parents[1] / "docs" / "perf" / "alarm-tuning.md"
MISSION = Path(__file__).resolve().parents[1] / "mission"

PARAM = "cabin_pressure_kpa"
_P0, _P_SIGMA = NOMINALS[PARAM]
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_BOUNDS = {"red_low": 95.0, "yellow_low": 98.0, "yellow_high": 104.0, "red_high": 106.0}
_TEMP = "cabin_temp_c"
_TEMP_BOUNDS = {"red_low": 15.0, "yellow_low": 18.0, "yellow_high": 27.0, "red_high": 30.0}


def grid() -> list[tuple[str, int, float]]:
    return [("deadband", n, pct) for n in GRID_N for pct in GRID_DEADBAND_PCT] + [
        ("roc", n, lim) for n in GRID_N for lim in GRID_ROC_LIMIT
    ]


# ---- Xu 2012 chain (R20 code, verbatim semantics)
def stationary(n: int, p: float, iters: int = 3000) -> list[float]:
    """Xu 2012 Figs. 3-4: states 0..n-1 no-alarm with i exceedances in a row, n..2n-1 alarm."""
    k = 2 * n
    T = [[0.0] * k for _ in range(k)]
    for i in range(n):
        T[i][i + 1 if i + 1 < n else n] += p
        T[i][0] += 1 - p
        T[n + i][n + i + 1 if i + 1 < n else 0] += 1 - p
        T[n + i][n] += p
    pi = [1.0 / k] * k
    for _ in range(iters):
        pi = [sum(pi[a] * T[a][b] for a in range(k)) for b in range(k)]
    return pi


def far_and_raise_rate(n: int, p: float) -> tuple[float, float]:
    pi = stationary(n, p)
    return sum(pi[n:]), pi[n - 1] * p  # time in alarm (Xu's FAR), raises per sample


def z_n(n: int, events_per_shift: float = 1.0, samples: int = 288_000) -> float:
    return -NormalDist().inv_cdf((events_per_shift / samples) ** (1 / n))


# ---- analytic noise margins (R20 F2)
@dataclass(frozen=True)
class Margin:
    param: str
    sim_sigma: float
    distance: float  # nominal to the nearest yellow bound (red when there is no yellow)
    deadband: float
    nuisance_sigma: float  # largest sigma without a nuisance raise at nominal
    chatter_sigma: float  # largest sigma without clear-and-re-raise cycles at the limit
    nuisance_x: float  # both also as multiples of the simulator sigma
    chatter_x: float


def margins(param: str, n: int = 3) -> Margin:
    nominal, sigma = NOMINALS[param]
    chk = next(c for c in load_dir(MISSION).limits[param].checks if isinstance(c, LimitCheck))
    yellow = [b for b in (chk.yellow_low, chk.yellow_high) if b is not None]
    bounds = yellow or [b for b in (chk.red_low, chk.red_high) if b is not None]
    dist, d, z = min(abs(b - nominal) for b in bounds), deadband(chk), z_n(n)
    return Margin(param, sigma, dist, d, dist / z, d / z, dist / z / sigma, d / z / sigma)


# ---- engine driving (the real engine; CompiledSet built directly: ponytail: a grid cell below the
# D-ALM-02 floor is measured here and never loaded through the mission db)
@dataclass(frozen=True)
class Cell:
    kind: str
    n: int
    value: float
    false_per_h: float
    psi: float
    leak_delay_s: float
    overheat_delay_s: float
    roc_margin_s: float
    rtn_after_isolate_s: float
    spike_rejected: bool


def _limit_cfg(bounds: dict[str, float], pct: float) -> LimitCheck:
    return LimitCheck.model_validate({"type": "limit", "hysteresis_pct": pct, **bounds})


def _limit(bounds: dict[str, float], pct: float, n: int) -> tuple[Check, int]:
    return Limit(_limit_cfg(bounds, pct)), n


def _roc(limit: float, n: int) -> tuple[Check, int]:
    cfg = RocCheck(
        type="roc", limit_per_s=limit, window_s=1.0, direction="falling", severity="YELLOW"
    )
    return Roc(cfg), n


def _run(
    param: str, checks: Sequence[tuple[Check, int]], values: Iterable[float]
) -> list[tuple[float, Transition]]:
    cset = CompiledSet("study", "study", {param: tuple(checks)})
    st: EngineState = initial_state(cset)
    out: list[tuple[float, Transition]] = []
    for i, v in enumerate(values):
        t = _T0 + timedelta(seconds=i * PERIOD_S)
        st, trs = step(st, cset, "NYX-TA1", _T0, i, t, PERIOD_S, {param: v}, False)
        out.extend((i * PERIOD_S, tr) for tr in trs)
    return out


def _ramp(rng: random.Random, start: float, sigma: float, rate: float, secs: float) -> list[float]:
    return [start + rate * i * PERIOD_S + rng.gauss(0, sigma) for i in range(int(secs / PERIOD_S))]


def _first(trs: list[tuple[float, Transition]], kinds: tuple[str, ...], after: float = 0) -> float:
    return next((t for t, tr in trs if tr.kind in kinds and t >= after), float("nan"))


def _delay(values: list[float], *, limit: float, above: bool) -> float:
    """Time of the first raw frame beyond the limit."""
    return next(i * PERIOD_S for i, v in enumerate(values) if (v >= limit if above else v <= limit))


def _false_rate(
    kind: str, n: int, value: float, rng: random.Random, hours: float
) -> tuple[float, float]:
    if kind == "deadband":  # limit checks: analytic, both yellow bounds at their distance
        nd = NormalDist()
        rate = sum(
            far_and_raise_rate(n, nd.cdf(-abs(b - _P0) / _P_SIGMA))[1]
            for b in (_BOUNDS["yellow_low"], _BOUNDS["yellow_high"])
        )
        return rate * 3600 / PERIOD_S, 0.0
    # ROC: overlapping windows are correlated, so Monte Carlo. ponytail: the limit check is
    # 135 sigma away and cannot fire at nominal, so it is left out of this run
    vals = [_P0 + rng.gauss(0, _P_SIGMA) for _ in range(int(hours * 3600 / PERIOD_S))]
    trs = _run(PARAM, [_roc(value, n)], vals)
    evs = [
        QEvent("NYX-TA1", PARAM, tr.kind, _T0 + timedelta(seconds=t), tr.alarm_id) for t, tr in trs
    ]
    q = quality(evs, [])
    return q.raises / hours, q.psi.get(f"NYX-TA1/{PARAM}", 0.0)


def run_cell(kind: str, n: int, value: float, seed: str, hours: float = NOMINAL_HOURS) -> Cell:
    rng = random.Random(f"{seed}/{kind}/{n}/{value}")
    pct, roc_lim = (value, 0.15) if kind == "deadband" else (15.0, value)
    lim, roc = _limit(_BOUNDS, pct, n), _roc(roc_lim, n)
    false_h, psi = _false_rate(kind, n, value, rng, hours)

    leak = _ramp(rng, _P0, _P_SIGMA, LEAK_KPA_PER_S, 21.0)
    cross = _delay(leak, limit=_BOUNDS["yellow_low"], above=False)
    leak_raise = _first(_run(PARAM, [lim], leak), ("RAISE",))
    roc_raise = _first(_run(PARAM, [lim, roc], leak), ("RAISE",))

    t0, ts = NOMINALS[_TEMP]
    heat = _ramp(rng, t0, ts, OVERHEAT_C_PER_S, 20.0)
    heat_cross = _delay(heat, limit=_TEMP_BOUNDS["yellow_high"], above=True)
    heat_raise = _first(_run(_TEMP, [_limit(_TEMP_BOUNDS, 3, n)], heat), ("RAISE",))

    spike = [_P0] * 50 + [94.0] + [_P0] * 50  # one frame below red_low (ALM-03)
    # leak to 93 kPa (the ATP-02 operator moment), then isolate_valve repressurises at +0.5 kPa/s
    iso_t = (_P0 - 93.0) / -LEAK_KPA_PER_S
    leak_to_93 = _ramp(rng, _P0, _P_SIGMA, LEAK_KPA_PER_S, iso_t)
    back = [
        min(93.0 + ISOLATE_RATE * i * PERIOD_S, _P0) + rng.gauss(0, _P_SIGMA)
        for i in range(int(40 / PERIOD_S))
    ]
    rtn = _first(_run(PARAM, [lim, roc], leak_to_93 + back), ("RTN", "CLEAR"), after=iso_t) - iso_t

    return Cell(
        kind, n, value, false_h, psi,
        leak_raise - cross, heat_raise - heat_cross, cross - roc_raise, rtn,
        not _run(PARAM, [lim], spike),
    )  # fmt: skip


def study(seed: str, hours: float = NOMINAL_HOURS) -> tuple[list[Cell], dict[str, Margin]]:
    cells = [run_cell(k, n, v, seed, hours) for k, n, v in grid()]
    return cells, {p: margins(p) for p in NOMINALS}


def frontier(points: Sequence[tuple[float, float]]) -> list[int]:
    """Indices not dominated on (a, b), both minimised."""

    def dom(j: int, i: int) -> bool:
        (a, b), (c, d) = points[j], points[i]
        return a <= c and b <= d and (a < c or b < d)

    return [i for i in range(len(points)) if not any(dom(j, i) for j in range(len(points)))]


# ---- report
def _dominators(cells: list[Cell]) -> list[Cell]:
    def key(c: Cell) -> tuple[float, float, float]:
        return (c.false_per_h, c.leak_delay_s, c.rtn_after_isolate_s)

    ship = next(c for c in cells if (c.kind, c.n, c.value) == SHIPPED)
    return [
        c for c in cells
        if c.kind == "deadband" and c.spike_rejected and c is not ship
        and all(a <= b + 1e-9 for a, b in zip(key(c), key(ship), strict=True))
        and any(a < b - 1e-9 for a, b in zip(key(c), key(ship), strict=True))
    ]  # fmt: skip


def _plot(param: str) -> str:
    """ROC-style text plot: raises per 8 h at nominal as the noise grows (N = 1 against N = 3)."""
    sigma = NOMINALS[param][1]
    m = margins(param)
    nd, rows = NormalDist(), []
    for k in (1, 2, 4, 8, 16, 32, 64):
        r = [far_and_raise_rate(n, nd.cdf(-m.distance / (k * sigma)))[1] * 288_000 for n in (1, 3)]
        bars = [("#" * int(8 * math.log10(1 + x)) or ".") for x in r]
        rows.append(f"{k:>3}x sigma  N=1 {r[0]:>10.3g} {bars[0]:<40}  N=3 {r[1]:>10.3g} {bars[1]}")
    return (
        f"```\n{param} (sigma {sigma}, {m.distance:.3g} to the yellow limit): raises per 8 h\n"
        + "\n".join(rows)
        + "\n```"
    )


_GRID_HEAD = (
    "| check | N | value | false/h | psi | leak delay s | overheat delay s | ROC margin s "
    "| RTN after isolate s | spike rejected | floor ok |"
)
_MARGIN_HEAD = (
    "| parameter | sim sigma | distance | deadband d | nuisance N=1 (x sim) "
    "| nuisance N=3 (x sim) | chatter N=3 (x sim) |"
)


def _loadable(pct: float) -> bool:
    """The D-ALM-02 floor for the cabin-pressure limit check."""
    span = _BOUNDS["red_high"] - _BOUNDS["red_low"]
    return pct / 100 * span >= floor(_limit_cfg(_BOUNDS, 1))


def _tag(c: Cell) -> str:
    return "" if _loadable(c.value) else " (below the D-ALM-02 floor: measured, not loadable)"


def _grid_row(c: Cell) -> str:
    ok = "n/a" if c.kind == "roc" else ("yes" if _loadable(c.value) else "no")
    return (
        f"| {c.kind} | {c.n} | {c.value:g} | {c.false_per_h:.3g} | {c.psi:.3g} "
        f"| {c.leak_delay_s:.2f} | {c.overheat_delay_s:.2f} | {c.roc_margin_s:.2f} "
        f"| {c.rtn_after_isolate_s:.2f} | {c.spike_rejected} | {ok} |"
    )


def _margin_row(p: str, m: Margin) -> str:
    return (
        f"| {p} | {m.sim_sigma:g} | {m.distance:.3g} | {m.deadband:.3g} "
        f"| {margins(p, 1).nuisance_x:.0f} | {m.nuisance_x:.0f} | {m.chatter_x:.1f} |"
    )


def _acceptance(cells: list[Cell], m3: Margin) -> list[str]:
    ship = next(c for c in cells if (c.kind, c.n, c.value) == SHIPPED)
    checks = [
        ("ALM-03 spike rejected", ship.spike_rejected),
        (
            f"ALM-04 ROC margin {ship.roc_margin_s:.2f} s >= {MIN_ROC_MARGIN_S:g} s",
            ship.roc_margin_s >= MIN_ROC_MARGIN_S,
        ),
        (
            f"persistence delay {ship.leak_delay_s:.2f} s (N-1 frame periods; R20 counts "
            f"N x 0.1 s = {ship.n * PERIOD_S:.1f} s, exactly the {ALM05_S} s limit at N = 3)",
            ship.leak_delay_s <= ALM05_S + 1e-9,
        ),
        (f"false raises per hour at nominal {ship.false_per_h:.3g}", ship.false_per_h == 0),
    ]
    out = [f"- {'PASS' if ok else 'FAIL'}: {t}" for t, ok in checks]
    out.append(
        f"- noise margins from the chain (cabin pressure): nuisance {m3.nuisance_x:.0f}x, "
        f"chatter {m3.chatter_x:.0f}x the simulator sigma"
    )
    dom = _dominators(cells)
    if not dom:
        return [*out, "", "No grid value dominates N = 3 / 15 %."]
    return [
        *out,
        "",
        "Grid values that dominate N = 3 / 15 % on (false/h, leak delay, RTN after isolate) among "
        "the cells that reject the spike. They raise assumption A-R20-3; nothing is changed "
        "silently. N = 3 is the spec's value and doubles the noise margin (R20 F2). Only "
        "N = 2 / 15 % is loadable; every other line is below the D-ALM-02 floor:",
        "",
        *[
            f"- {c.kind} N={c.n} value {c.value:g}: delay {c.leak_delay_s:.2f} s, "
            f"RTN {c.rtn_after_isolate_s:.2f} s{_tag(c)}"
            for c in dom
        ],
    ]


def render(cells: list[Cell], margs: dict[str, Margin]) -> str:
    dcells = [c for c in cells if c.kind == "deadband" and c.spike_rejected]
    front = frontier([(c.false_per_h, c.leak_delay_s) for c in dcells])
    return "\n".join(
        [
            "# Alarm tuning study (D-ALM-16, R20)",
            "",
            "Generated by `uv run python -m perf.alarm_tuning`; do not edit by hand.",
            "",
            "**Simulator noise only; re-run on bench noise before the first campaign (§3.5 "
            "ICD-B-06; d >= 2.2 σ and yellow distance >= 2.3 σ; autocorrelation check, Wang "
            "2019).**",
            "",
            "Delay definition: the persistence delay from the first raw frame beyond the limit "
            "to the RAISE, N-1 frame periods (0.2 s at N = 3). R20 counts N x 0.1 s: 0.3 s at "
            "N = 3, exactly the 300 ms ALM-05 limit, with N = 4 and 5 over it. ALM-05 latency is "
            "measured from the triggering frame (`perf/alarm_latency.py`), so this figure is a "
            "proxy, not the ALM-05 result.",
            "",
            "Single seed: delays are noise-dominated and a 0.2 s difference is one frame, so do "
            "not read small delay differences as a ranking.",
            "",
            "Limit checks are analytic (Xu 2012 chain); the ROC check and the fault profiles are "
            "Monte Carlo through the real engine, seeded.",
            "",
            "## Grid (cabin pressure)",
            "",
            _GRID_HEAD,
            "|---|---|---|---|---|---|---|---|---|---|---|",
            *[_grid_row(c) for c in cells],
            "",
            "## Noise margins (R20 F2; N = 3, at most 1 nuisance event per 8 h, i.i.d., 10 Hz)",
            "",
            _MARGIN_HEAD,
            "|---|---|---|---|---|---|---|",
            *[_margin_row(p, m) for p, m in margs.items()],
            "",
            "Chatter at the limit is the binding margin: a bench sigma above the chatter column "
            "chatters (bus current first). A-R20-2 (bus current `hysteresis_abs` 0.2 to 0.3) "
            "stays open.",
            "",
            "## Frontier (spike-rejecting deadband cells; false/h and leak delay, minimised)",
            "",
            *[f"- N={dcells[i].n}, {dcells[i].value:g} %{_tag(dcells[i])}" for i in front],
            "",
            "## Plots",
            "",
            *[_plot(p) for p in margs],
            "",
            "## Acceptance (N = 3 / 15 %)",
            "",
            *_acceptance(cells, margs[PARAM]),
            "",
        ]
    )


def main() -> None:
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(render(*study("1")))
    print(f"wrote {DOC}")


if __name__ == "__main__":
    main()
