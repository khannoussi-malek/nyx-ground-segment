import { expect, test } from "vitest";
import { conditionFor } from "./banners";

const base = { connected: true, broker: "up" as const, processing: true, ageMs: 100, frozen: false };

test("orders the conditions", () => {
  expect(conditionFor({ ...base, connected: false, broker: "down" })).toBe("NO LIVE CONNECTION");
  expect(conditionFor({ ...base, broker: "down", processing: false })).toBe("GROUND PROCESSING DOWN – BROKER");
  expect(conditionFor({ ...base, processing: false, ageMs: 5000 })).toBe("GROUND PROCESSING DOWN");
  expect(conditionFor({ ...base, ageMs: 2000, frozen: true })).toBe("LOSS OF SIGNAL");
  expect(conditionFor({ ...base, frozen: true })).toBe("FROZEN");
  expect(conditionFor(base)).toBeNull();
});

test("broker down shows BROKER on every vehicle, never LOS", () => {
  for (const processing of [true, false, undefined])
    expect(conditionFor({ ...base, broker: "down", processing, ageMs: 10_000 })).toBe("GROUND PROCESSING DOWN – BROKER");
});

test("NO LIVE CONNECTION outranks processing down and LOS; 1999 ms is not LOS", () => {
  expect(conditionFor({ ...base, connected: false, processing: false })).toBe("NO LIVE CONNECTION");
  expect(conditionFor({ ...base, connected: false, ageMs: 9000 })).toBe("NO LIVE CONNECTION");
  expect(conditionFor({ ...base, ageMs: 1999 })).toBeNull();
  expect(conditionFor({ ...base, ageMs: 1999, frozen: true })).toBe("FROZEN");
});

test("an unknown processing state is not 'down'; an unknown age is not LOS", () => {
  expect(conditionFor({ ...base, processing: undefined })).toBeNull();
  expect(conditionFor({ ...base, ageMs: null })).toBeNull();
});
