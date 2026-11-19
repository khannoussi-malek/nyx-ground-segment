import { expect, test } from "vitest";
import { ageAt, formatAge, formatValue, sortedEntries } from "./format";

test("formatValue shows two decimals and a dash for null", () => {
  expect(formatValue(101.3)).toBe("101.30");
  expect(formatValue(0)).toBe("0.00");
  expect(formatValue(null)).toBe("—");
});

test("sortedEntries orders by parameter name without mutating the input", () => {
  const values = { b: 1, c: null, a: 2 };
  expect(sortedEntries(values)).toEqual([["a", 2], ["b", 1], ["c", null]]);
  expect(Object.keys(values)).toEqual(["b", "c", "a"]);
});

test("formatValue never shows a negative zero", () => {
  expect(formatValue(-0.004)).toBe("0.00");
  expect(formatValue(-0)).toBe("0.00");
  expect(formatValue(-0.005)).toBe("\u22120.01"); // U+2212 minus, aligned with tabular digits
  expect(formatValue(-12.5)).toBe("\u221212.50");
});

test("ageAt adds the time since receipt to the server's age (D-UI-05)", () => {
  expect(ageAt(120, 1000, 1350)).toBe(470);
  expect(ageAt(null, 1000, 1350)).toBeNull(); // no server age: unknown, never receipt-only
  expect(ageAt(120, undefined, 1350)).toBeNull();
  expect(ageAt(120, 1000, 950)).toBe(120); // read before the receipt stamp: never fresher than the server said
});

test("formatAge: tenths under 10 s, whole seconds under 2 min, then minutes", () => {
  expect(formatAge(null)).toBe("—");
  expect(formatAge(340)).toBe("0.3 s");
  expect(formatAge(9_940)).toBe("9.9 s");
  expect(formatAge(10_000)).toBe("10 s");
  expect(formatAge(119_400)).toBe("119 s");
  expect(formatAge(185_000)).toBe("3 min");
  expect(formatAge(-20)).toBe("0.0 s"); // clock skew: never show a negative age
});
