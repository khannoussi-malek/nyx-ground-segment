import type { Bucket } from "./api";
import type { Limits } from "./gauge";

export const WINDOWS = { "2 min": 120_000, "1 h": 3_600_000, "24 h": 86_400_000 } as const;
export type WindowKey = keyof typeof WINDOWS;
export const MIN_WIDTH = 100; // the server accepts 100..4000 (D-DB-04)
export const MAX_WIDTH = 4000;
export const MIN_BUCKET_MS = 100;
export const LABEL_GAP_PX = 14;

export const refreshMs = (windowMs: number): number => Math.max(1000, windowMs / 120);
export const clampWidth = (px: number): number => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(px)));
/** The server's bucket: max(window / width, 100 ms). */
export const bucketMsFor = (windowMs: number, width: number): number => Math.max(windowMs / width, MIN_BUCKET_MS);

export type Series = { x: number[]; line: (number | null)[]; lo: (number | null)[]; hi: (number | null)[] };

/** M4 (D-UI-11): each bucket gives two points, its first value at the bucket start and its last at the bucket end, so the
 *  line passes through what the bucket really started and ended with; the band is the bucket's min/max. A missing bucket
 *  inserts one null point: a gap, never a line across it. x in epoch ms.
 *  The x of first and last are approximations: a Bucket carries no first/last timestamps, only the bucket start `t`.
 *  A gap is a distance of more than 1.5 buckets between starts, so the server rounding a fractional bucket width to
 *  milliseconds can never paint a false gap. */
export function toSeries(buckets: readonly Bucket[], bucketMs: number): Series {
  const s: Series = { x: [], line: [], lo: [], hi: [] };
  let prevStart: number | null = null;
  for (const b of buckets) {
    const t = Date.parse(b.t);
    if (prevStart !== null && t - prevStart > 1.5 * bucketMs) {
      s.x.push(prevStart + bucketMs);
      s.line.push(null);
      s.lo.push(null);
      s.hi.push(null);
    }
    s.x.push(t, t + bucketMs - 1);
    s.line.push(b.first, b.last);
    s.lo.push(b.lo, b.lo);
    s.hi.push(b.hi, b.hi);
    prevStart = t;
  }
  return s;
}

export type LimitLabel = { value: number; tone: "red" | "yellow"; at: number };

/** Where to print the limit numbers. Red first (they matter most), then yellow; a label within 14 px of one already
 *  kept is dropped, so numbers never overprint on a short plot. `at` is the label's y in pixels. */
export function limitLabels(limits: Limits, yPx: (v: number) => number): LimitLabel[] {
  const wanted: [number | null | undefined, "red" | "yellow"][] = [
    [limits.red_low, "red"],
    [limits.red_high, "red"],
    [limits.yellow_low, "yellow"],
    [limits.yellow_high, "yellow"],
  ];
  const kept: LimitLabel[] = [];
  for (const [value, tone] of wanted) {
    if (value == null) continue;
    const at = yPx(value);
    if (kept.every((k) => Math.abs(k.at - at) >= LABEL_GAP_PX)) kept.push({ value, tone, at });
  }
  return kept;
}
