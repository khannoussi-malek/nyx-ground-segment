import { expect, test } from "vitest";
import type { MissionDb } from "./api";
import { paramViews } from "./params";

const mission = {
  parameters: {
    cabin_pressure_kpa: { unit: "kPa", type: "float", description: "Cabin total pressure" },
    bus_current_a: { unit: "A", type: "float", description: "" },
    tank_pressure_bar: { unit: "bar", type: "float", description: "Propellant tank pressure" },
  },
  limits: {
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
