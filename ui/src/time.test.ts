import { expect, test } from "vitest";
import { utcClock, utcClockMs, utcIso } from "./time";

test("the engine's microsecond ISO text", () => {
  const t = "2026-11-14T10:00:01.123456Z";
  expect([utcClock(t), utcClockMs(t), utcIso(t)]).toEqual(["10:00:01Z", "10:00:01.123Z", "2026-11-14T10:00:01.123Z"]);
});

test("no fraction, and an explicit +00:00 offset", () => {
  expect(utcClock("2026-11-14T10:00:00Z")).toBe("10:00:00Z");
  expect(utcClockMs("2026-11-14T10:00:00Z")).toBe("10:00:00.000Z");
  expect(utcClock("2026-11-14T10:00:00+00:00")).toBe("10:00:00Z");
  expect(utcIso("2026-11-14T10:00:00.5+00:00")).toBe("2026-11-14T10:00:00.500Z");
});

test("a non-UTC offset is converted, never printed as UTC; garbage shows a dash", () => {
  expect(utcClock("2026-11-14T12:00:00+02:00")).toBe("10:00:00Z");
  expect([utcClock("nope"), utcClockMs(""), utcIso("x")]).toEqual(["\u2014", "\u2014", "\u2014"]);
});
