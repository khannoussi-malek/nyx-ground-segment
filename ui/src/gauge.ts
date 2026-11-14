/** Limit gauge geometry: where a value sits between its limits, as 0..1 track fractions.
 *  Position only: the gauge never decides a state, the alarm engine does (D-UI-07, G20). */
export type Limits = {
  red_low?: number | null;
  yellow_low?: number | null;
  yellow_high?: number | null;
  red_high?: number | null;
};
export type Zone = { from: number; to: number; kind: "normal" | "yellow" | "red"; side?: "LOW" | "HIGH" };
export type Mark = { at: number; value: number }; // a limit printed under the track
export type Gauge = {
  pos: number | null; // null: no value, no pointer
  clamped: -1 | 0 | 1; // value beyond the track, pinned to that edge
  zones: readonly Zone[]; // left to right, covering 0..1
  marks: readonly Mark[];
};

const PAD = 0.1; // of the limit span, so a value just past red still shows inside the track

export function gaugeOf(l: Limits, value: number | null): Gauge | null {
  const { red_low: rl, yellow_low: yl, yellow_high: yh, red_high: rh } = l;
  const lo0 = rl ?? yl;
  const hi0 = rh ?? yh;
  if (lo0 == null && hi0 == null) return null;
  // a one-sided parameter runs from zero (current, pressure); never from the value: the track must not move
  const lo = lo0 ?? Math.min(0, hi0!);
  const hi = hi0 ?? Math.max(0, lo0!);
  const span = hi - lo;
  if (!(span > 0)) return null;
  const min = lo - PAD * span;
  const f = (x: number) => (x - min) / (span * (1 + 2 * PAD));

  const zones: Zone[] = [];
  let x = 0;
  const upTo = (to: number, kind: Zone["kind"], side?: Zone["side"]) => {
    if (to > x) zones.push(side ? { from: x, to, kind, side } : { from: x, to, kind });
    x = Math.max(x, to);
  };
  if (rl != null) upTo(f(rl), "red", "LOW");
  if (yl != null) upTo(f(yl), "yellow", "LOW");
  upTo((yh ?? rh) == null ? 1 : f((yh ?? rh)!), "normal");
  if (yh != null) upTo(rh == null ? 1 : f(rh), "yellow", "HIGH");
  upTo(1, "red", "HIGH"); // no-op when the high side has no red

  const marks = [lo0, hi0].filter((v): v is number => v != null).map((v) => ({ at: f(v), value: v }));

  if (value == null) return { pos: null, clamped: 0, zones, marks };
  const raw = f(value);
  if (raw < 0) return { pos: 0, clamped: -1, zones, marks };
  if (raw > 1) return { pos: 1, clamped: 1, zones, marks };
  return { pos: raw, clamped: 0, zones, marks };
}
