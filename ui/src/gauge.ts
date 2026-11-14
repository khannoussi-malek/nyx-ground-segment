/** Limit gauge geometry: where a value sits between its limits, as 0..1 track fractions.
 *  Position only: the gauge never decides a state, the alarm engine does (D-UI-07, G20). */
export type Limits = {
  red_low?: number | null;
  yellow_low?: number | null;
  yellow_high?: number | null;
  red_high?: number | null;
};
export type Tick = { at: number; level: "YELLOW" | "RED" };
export type Gauge = {
  pos: number | null; // null: no value, no marker
  clamped: -1 | 0 | 1; // value beyond the track, pinned to that edge
  normal: readonly [number, number];
  ticks: readonly Tick[];
};

const PAD = 0.1; // of the limit span, so a value just past red still shows inside the track

export function gaugeOf(l: Limits, value: number | null): Gauge | null {
  const lo0 = l.red_low ?? l.yellow_low;
  const hi0 = l.red_high ?? l.yellow_high;
  if (lo0 == null && hi0 == null) return null;
  // a one-sided parameter runs from zero (current, pressure); never from the value: the track must not move
  const lo = lo0 ?? Math.min(0, hi0!);
  const hi = hi0 ?? Math.max(0, lo0!);
  const span = hi - lo;
  if (!(span > 0)) return null;
  const min = lo - PAD * span;
  const frac = (x: number) => (x - min) / (span * (1 + 2 * PAD));

  const ticks: Tick[] = [];
  for (const [v, level] of [
    [l.red_low, "RED"],
    [l.yellow_low, "YELLOW"],
    [l.yellow_high, "YELLOW"],
    [l.red_high, "RED"],
  ] as const) {
    if (v != null) ticks.push({ at: frac(v), level });
  }
  const nLo = l.yellow_low ?? l.red_low;
  const nHi = l.yellow_high ?? l.red_high;
  const normal = [nLo == null ? 0 : frac(nLo), nHi == null ? 1 : frac(nHi)] as const;

  if (value == null) return { pos: null, clamped: 0, normal, ticks };
  const raw = frac(value);
  if (raw < 0) return { pos: 0, clamped: -1, normal, ticks };
  if (raw > 1) return { pos: 1, clamped: 1, normal, ticks };
  return { pos: raw, clamped: 0, normal, ticks };
}
