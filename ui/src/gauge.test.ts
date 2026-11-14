import { expect, test } from "vitest";
import { gaugeOf } from "./gauge";

const cabin = { red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 };

test("no limits, no gauge", () => {
  expect(gaugeOf({}, 1)).toBeNull();
  expect(gaugeOf({ red_low: null, red_high: null }, 1)).toBeNull();
});

test("the red limits sit inside the track, padded 10 % of their span on each side", () => {
  const g = gaugeOf(cabin, 100)!;
  const ticks = g.ticks.map((t) => [t.level, +t.at.toFixed(4)]);
  // range 95..106 (span 11) padded to 93.9..107.1
  expect(ticks).toEqual([
    ["RED", +(1.1 / 13.2).toFixed(4)],
    ["YELLOW", +(4.1 / 13.2).toFixed(4)],
    ["YELLOW", +(10.1 / 13.2).toFixed(4)],
    ["RED", +(12.1 / 13.2).toFixed(4)],
  ]);
  expect(g.normal[0]).toBeCloseTo(4.1 / 13.2);
  expect(g.normal[1]).toBeCloseTo(10.1 / 13.2);
  expect(g.pos).toBeCloseTo(6.1 / 13.2);
  expect(g.clamped).toBe(0);
});

test("a value beyond the track is pinned to its edge and flagged", () => {
  expect(gaugeOf(cabin, 200)).toMatchObject({ pos: 1, clamped: 1 });
  expect(gaugeOf(cabin, -50)).toMatchObject({ pos: 0, clamped: -1 });
});

test("a high-only parameter starts its track at zero and its normal band at the edge", () => {
  const g = gaugeOf({ yellow_high: 8, red_high: 10 }, 5)!;
  expect(g.normal[0]).toBe(0);
  expect(g.normal[1]).toBeCloseTo(9 / 12); // 0..10 padded to -1..11
  expect(g.pos).toBeCloseTo(6 / 12);
  expect(g.ticks.map((t) => t.level)).toEqual(["YELLOW", "RED"]);
});

test("a low-only parameter ends its track at zero", () => {
  const g = gaugeOf({ red_low: -10 }, -5)!;
  expect(g.normal[0]).toBeCloseTo(1 / 12);
  expect(g.normal[1]).toBe(1);
});

test("the track does not move with the value, and a missing value hides the marker", () => {
  expect(gaugeOf(cabin, 99)!.ticks).toEqual(gaugeOf(cabin, 105)!.ticks);
  expect(gaugeOf(cabin, null)).toMatchObject({ pos: null, clamped: 0 });
});

test("a degenerate range gives no gauge", () => {
  expect(gaugeOf({ red_high: 0 }, 0)).toBeNull();
});
