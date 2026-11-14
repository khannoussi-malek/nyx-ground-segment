import { expect, test } from "vitest";
import { gaugeOf, type Zone } from "./gauge";

const cabin = { red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 };
// cabin: limits 95..106 (span 11), track padded 10 % each side to 93.9..107.1 (width 13.2)
const f = (x: number) => (x - 93.9) / 13.2;
const near = (zones: readonly Zone[]) => zones.map((z) => [z.kind, +z.from.toFixed(4), +z.to.toFixed(4)]);
const r4 = (x: number) => +x.toFixed(4);

test("no limits, no gauge; a degenerate range, no gauge", () => {
  expect(gaugeOf({}, 1)).toBeNull();
  expect(gaugeOf({ red_low: null, red_high: null }, 1)).toBeNull();
  expect(gaugeOf({ red_high: 0 }, 0)).toBeNull();
});

test("two-sided limits: red, yellow, normal, yellow, red zones covering the whole track", () => {
  const g = gaugeOf(cabin, 100)!;
  expect(near(g.zones)).toEqual([
    ["red", 0, r4(f(95))],
    ["yellow", r4(f(95)), r4(f(98))],
    ["normal", r4(f(98)), r4(f(104))],
    ["yellow", r4(f(104)), r4(f(106))],
    ["red", r4(f(106)), 1],
  ]);
  expect(g.pos).toBeCloseTo(f(100));
  expect(g.clamped).toBe(0);
});

test("the scale prints the red limits", () => {
  const g = gaugeOf(cabin, 100)!;
  expect(g.marks.map((m) => [m.value, r4(m.at)])).toEqual([
    [95, r4(f(95))],
    [106, r4(f(106))],
  ]);
});

test("high-only: normal from zero, then yellow, then red; one mark", () => {
  const g = gaugeOf({ yellow_high: 8, red_high: 10 }, 5)!; // 0..10 padded to -1..11
  expect(near(g.zones)).toEqual([
    ["normal", 0, r4(9 / 12)],
    ["yellow", r4(9 / 12), r4(11 / 12)],
    ["red", r4(11 / 12), 1],
  ]);
  expect(g.marks.map((m) => m.value)).toEqual([10]);
});

test("a side with only a yellow limit marks the yellow one and starts yellow at the edge", () => {
  const g = gaugeOf({ yellow_low: 2, red_high: 10 }, 5)!; // 2..10 padded to 1.2..10.8
  expect(g.zones.map((z) => z.kind)).toEqual(["yellow", "normal", "red"]);
  expect(g.zones[0].from).toBe(0);
  expect(g.marks.map((m) => m.value)).toEqual([2, 10]);
});

test("a red-only side goes straight from red to normal", () => {
  const g = gaugeOf({ red_low: -10 }, -5)!; // -10..0 padded to -11..1
  expect(near(g.zones)).toEqual([
    ["red", 0, r4(1 / 12)],
    ["normal", r4(1 / 12), 1],
  ]);
});

test("a value beyond the track is pinned to its edge and flagged", () => {
  expect(gaugeOf(cabin, 200)).toMatchObject({ pos: 1, clamped: 1 });
  expect(gaugeOf(cabin, -50)).toMatchObject({ pos: 0, clamped: -1 });
});

test("the track does not move with the value, and a missing value hides the pointer", () => {
  expect(gaugeOf(cabin, 99)!.zones).toEqual(gaugeOf(cabin, 105)!.zones);
  expect(gaugeOf(cabin, null)).toMatchObject({ pos: null, clamped: 0 });
});

test("yellow and red zones know their side, so the engine's state can name one", () => {
  const g = gaugeOf(cabin, 100)!;
  expect(g.zones.map((z) => [z.kind, z.side])).toEqual([
    ["red", "LOW"],
    ["yellow", "LOW"],
    ["normal", undefined],
    ["yellow", "HIGH"],
    ["red", "HIGH"],
  ]);
  expect(gaugeOf({ yellow_high: 8, red_high: 10 }, 5)!.zones.map((z) => z.side)).toEqual([undefined, "HIGH", "HIGH"]);
});
