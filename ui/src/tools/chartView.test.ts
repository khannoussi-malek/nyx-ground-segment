import { expect, test } from "vitest";
import { MAX_SPAN, MIN_SPAN, pan, rangeOf, select, zoom, type View } from "./chartView";

const NOW = Date.parse("2026-11-16T12:00:00Z");
const live = (span: number): View => ({ span, end: null });

test("a live view ends now; a fixed view ends where it was left", () => {
  expect(rangeOf(live(120_000), NOW)).toEqual([NOW - 120_000, NOW]);
  expect(rangeOf({ span: 60_000, end: NOW - 3_600_000 }, NOW)).toEqual([NOW - 3_660_000, NOW - 3_600_000]);
});

test("zoom keeps the time under the anchor in place; at the right edge a live view stays live", () => {
  // anchor in the middle of a fixed 2 min view: the middle time stays in the middle
  const v = { span: 120_000, end: NOW - 60_000 };
  const z = zoom(v, 0.5, 0.5, NOW);
  const [a, b] = rangeOf(v, NOW);
  const [c, d] = rangeOf(z, NOW);
  expect(z.span).toBe(60_000);
  expect((c + d) / 2).toBe((a + b) / 2);
  expect(zoom(live(120_000), 0.5, 1, NOW)).toEqual(live(60_000));
});

test("zoom is clamped between the smallest span and the server's 24 h", () => {
  expect(zoom(live(MIN_SPAN), 0.5, 1, NOW).span).toBe(MIN_SPAN);
  expect(zoom(live(MAX_SPAN), 2, 1, NOW).span).toBe(MAX_SPAN);
});

test("zooming out never runs into the future: a range that would reach now becomes live", () => {
  expect(zoom({ span: 60_000, end: NOW - 10_000 }, 4, 0.5, NOW)).toEqual(live(240_000));
});

test("pan moves by a share of the span; back to now it is live again, never past now", () => {
  const back = pan(live(120_000), -0.5, NOW);
  expect(back).toEqual({ span: 120_000, end: NOW - 60_000 });
  expect(pan(back, 0.25, NOW)).toEqual({ span: 120_000, end: NOW - 30_000 });
  expect(pan(back, 1, NOW)).toEqual(live(120_000));
});

test("a dragged selection becomes the view; a tiny one is widened to the smallest span around its middle", () => {
  expect(select(NOW - 50_000, NOW - 20_000, NOW)).toEqual({ span: 30_000, end: NOW - 20_000 });
  expect(select(NOW - 50_000, NOW - 49_000, NOW).span).toBe(MIN_SPAN);
  expect(select(NOW - 30_000, NOW + 5_000, NOW)).toEqual(live(35_000));
});
