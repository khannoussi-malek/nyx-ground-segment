import { expect, test } from "vitest";
import { keep, NO_FILTER, vehiclesOf } from "./listFilter";

test("no filter keeps every row", () => {
  expect(keep(NO_FILTER, "NYX-TA1", "anything")).toBe(true);
  expect(keep(NO_FILTER, null, "")).toBe(true);
});

test("the vehicle must match exactly; a row without a vehicle is dropped once one is chosen", () => {
  const f = { vehicle: "NYX-TA1", text: "" };
  expect(keep(f, "NYX-TA1", "x")).toBe(true);
  expect(keep(f, "NYX-TA2", "x")).toBe(false);
  expect(keep(f, null, "x")).toBe(false);
});

test("the text matches every word anywhere, ignoring case and order", () => {
  const row = "NYX-TA1 isolate_valve (valve = A) · fc1 · FAILED";
  expect(keep({ vehicle: "", text: "failed" }, "NYX-TA1", row)).toBe(true);
  expect(keep({ vehicle: "", text: "  Failed   ISOLATE " }, "NYX-TA1", row)).toBe(true);
  expect(keep({ vehicle: "", text: "failed vent" }, "NYX-TA1", row)).toBe(false);
});

test("the vehicle choices are the list's own vehicles, sorted, once each", () => {
  expect(vehiclesOf([{ vehicle: "NYX-TA2" }, { vehicle: null }, { vehicle: "NYX-TA1" }, { vehicle: "NYX-TA2" }])).toEqual(["NYX-TA1", "NYX-TA2"]);
});
