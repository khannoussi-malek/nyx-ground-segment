import { expect, test } from "vitest";
import { formatValue, sortedEntries } from "./format";

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
