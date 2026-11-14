import { expect, test } from "vitest";
import { utcClock } from "./time";

test("UTC clock text keeps milliseconds and the Z", () => {
  expect(utcClock("2026-11-14T10:00:01.123456Z")).toBe("10:00:01.123Z");
});
