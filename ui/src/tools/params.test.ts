import { expect, test } from "vitest";
import type { MissionDb } from "../server/api";
import { paramViews } from "./params";

const mission = {
  parameters: {
    cabin_pressure_kpa: { unit: "kPa", type: "float", description: "Cabin total pressure" },
    bus_current_a: { unit: "A", type: "float", description: "" },
    tank_pressure_bar: { unit: "bar", type: "float", description: "Propellant tank pressure" },
  },
  derived: { power_w: { unit: "W", description: "Bus power" } },
  limits: {
    power_w: { checks: [{ type: "limit", yellow_high: 250, red_high: 300 }] },
    cabin_pressure_kpa: {
      checks: [
        { type: "roc", limit_per_s: 0.15, window_s: 1, direction: "falling", severity: "YELLOW" },
        { type: "limit", red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 },
      ],
    },
    bus_current_a: { checks: [{ type: "roc", limit_per_s: 1, window_s: 1, direction: "rising", severity: "YELLOW" }] },
  },
} as unknown as MissionDb;

test("without the mission db: ids only, sorted", () => {
  expect(paramViews({ b: 2, a: null }, null)).toEqual([
    { id: "a", value: null },
    { id: "b", value: 2 },
  ]);
});

test("with it: parameters.yaml order, description as label, unit and the limit check's thresholds", () => {
  const v = paramViews({ tank_pressure_bar: 20, cabin_pressure_kpa: 101.3, bus_current_a: 5 }, mission);
  expect(v.map((p) => p.id)).toEqual(["cabin_pressure_kpa", "bus_current_a", "tank_pressure_bar"]);
  expect(v[0]).toMatchObject({ label: "Cabin total pressure", unit: "kPa", value: 101.3 });
  expect(v[0].limits).toMatchObject({ red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 });
});

test("a parameter with no limit check gets no gauge; an empty description falls back to the id", () => {
  const [bus] = paramViews({ bus_current_a: 5 }, mission);
  expect(bus.limits).toBeUndefined();
  expect(bus.label).toBeUndefined();
  expect(bus.unit).toBe("A");
});

test("values the mission db does not know are still shown, after the known ones; no frame, no row", () => {
  const v = paramViews({ zz_new: 1, cabin_pressure_kpa: 100, aa_new: 2 }, mission);
  expect(v.map((p) => p.id)).toEqual(["cabin_pressure_kpa", "aa_new", "zz_new"]);
  expect(v[1]).toEqual({ id: "aa_new", value: 2 });
});

test("ids that are Object.prototype names are looked up as own keys only", () => {
  const m = {
    parameters: { constructor: { unit: "V", type: "float", description: "Odd but legal name" } },
    limits: {},
  } as unknown as MissionDb;
  const v = paramViews({ constructor: 1.5, toString: 2 }, m); // "constructor" has no limits entry
  expect(v.map((p) => p.id)).toEqual(["constructor", "toString"]);
  expect(v[0]).toMatchObject({ label: "Odd but legal name", unit: "V" });
  expect(v[0].limits).toBeUndefined();
  expect(v[1]).toEqual({ id: "toString", value: 2 }); // unknown to the set, not mistaken for known
});

test("a derived parameter shows like a raw one: unit, description, gauge, after the raw rows", () => {
  const v = paramViews({ power_w: 120, bus_current_a: 5, other: 1 }, mission);
  expect(v.map((p) => p.id)).toEqual(["bus_current_a", "power_w", "other"]);
  expect(v[1]).toMatchObject({ unit: "W", label: "Bus power", value: 120 });
  expect(v[1].limits).toMatchObject({ yellow_high: 250, red_high: 300 });
});
