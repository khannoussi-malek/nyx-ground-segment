import { expect, test } from "vitest";
import type { Bucket } from "./api";
import { bucketMsFor, clampWidth, limitLabels, refreshMs, toSeries, WINDOWS } from "./chartSeries";

const b = (t: number, o: Partial<Bucket> = {}): Bucket => ({ t: new Date(t).toISOString(), n: 2, first: 1, last: 2, lo: 0, hi: 3, mean: 1.5, ...o });

test("line through each bucket's first and last, band from min/max", () => {
  const s = toSeries([b(0, { first: 1, last: 3, lo: 0.5, hi: 4 })], 100);
  expect(s.x).toEqual([0, 99]);
  expect(s.line).toEqual([1, 3]);
  expect(s.lo).toEqual([0.5, 0.5]);
  expect(s.hi).toEqual([4, 4]);
});

test("a ramp and a spike: the spike's bucket keeps its extremes in the band, the line keeps first and last", () => {
  const s = toSeries([b(0, { first: 1, last: 1.2, lo: 1, hi: 1.2 }), b(100, { first: 1.2, last: 1.3, lo: 1.2, hi: 9 })], 100);
  expect(s.hi).toEqual([1.2, 1.2, 9, 9]);
  expect(s.line).toEqual([1, 1.2, 1.2, 1.3]);
});

test("gap gives null", () => {
  const s = toSeries([b(0), b(300)], 100);
  expect(s.line).toEqual([1, 2, null, 1, 2]);
  expect(s.lo[2]).toBeNull();
  expect(s.hi[2]).toBeNull();
  expect(s.x).toEqual([0, 99, 100, 300, 399]); // x stays increasing
});

test("consecutive buckets have no gap, however many", () => {
  expect(toSeries([b(0), b(100), b(200)], 100).line).not.toContain(null);
  expect(toSeries([b(0), b(2400), b(4800)], 2400).line).not.toContain(null);
});

test("no buckets, no points", () => expect(toSeries([], 100)).toEqual({ x: [], line: [], lo: [], hi: [] }));

test("windows, refresh rate, width clamp and the server's bucket rule", () => {
  expect(WINDOWS).toEqual({ "2 min": 120_000, "1 h": 3_600_000, "24 h": 86_400_000 });
  expect(refreshMs(WINDOWS["2 min"])).toBe(1000);
  expect(refreshMs(WINDOWS["24 h"])).toBe(720_000);
  expect([clampWidth(50), clampWidth(800.4), clampWidth(9000)]).toEqual([100, 800, 4000]);
  expect(bucketMsFor(120_000, 1500)).toBe(100);
  expect(bucketMsFor(3_600_000, 1500)).toBe(2400);
});

const lim = { red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 };
const y = (v: number) => 400 - v * 3; // 3 px per unit

test("limit labels: every limit when there is room", () => {
  expect(limitLabels(lim, (v) => 400 - v * 10).map((l) => [l.value, l.tone])).toEqual([[95, "red"], [106, "red"], [98, "yellow"], [104, "yellow"]]);
});

test("limit labels skip collisions within 14 px; red wins over yellow", () => {
  // 95 and 98 are 9 px apart, 104 and 106 are 6 px apart
  expect(limitLabels(lim, y).map((l) => l.value)).toEqual([95, 106]);
  expect(limitLabels({ red_high: 106, yellow_high: 105 }, y).map((l) => l.value)).toEqual([106]);
  expect(limitLabels({ red_high: 106, yellow_high: 105 }, (v) => v * 14).length).toBe(2); // exactly 14 px apart stays
});

test("one-sided or no limits", () => {
  expect(limitLabels({ red_high: 10, yellow_high: null }, y)).toEqual([{ value: 10, tone: "red", at: y(10) }]);
  expect(limitLabels({}, y)).toEqual([]);
});

test("a fractional bucket width rounded by the server never paints a false gap, a missing bucket still does", () => {
  const ms = 3_600_000 / 1333; // 2700.675…
  const starts = [0, 1, 2, 3, 4].map((i) => Math.round(i * ms)); // the server's whole-millisecond starts: 0, 2701, 5401, 8102, 10803
  expect(toSeries(starts.map((t) => b(t)), ms).line).not.toContain(null);
  expect(toSeries([b(starts[0]!), b(starts[2]!)], ms).line).toEqual([1, 2, null, 1, 2]); // one bucket missing between them
  expect(toSeries([b(starts[0]!), b(starts[3]!)], ms).line).toContain(null); // two buckets missing
  expect(toSeries([b(0), b(Math.round(1.4 * ms))], ms).line).not.toContain(null); // 1.4 buckets: jitter, not a gap
  expect(toSeries([b(0), b(Math.round(1.6 * ms))], ms).line).toContain(null);
});
