import { expect, test } from "vitest";
import type { MissionDb } from "../server/api";
import { alarmText, hashText } from "./alarmText";
import { alarmView } from "../test/fixtures";

const MDB = {
  parameters: { cabin_pressure_kpa: { unit: "kPa", type: "float", description: "" }, suit_temp_c: { unit: "degC", type: "float", description: "" } },
  derived: { power_w: { unit: "W", description: "Bus power" } },
  limits: {
    power_w: { checks: [{ type: "limit", yellow_high: 250, red_high: 300 }] },
    cabin_pressure_kpa: { checks: [{ type: "limit", red_low: 95, red_high: 106 }, { type: "roc", direction: "both", limit_per_s: 0.15, severity: "YELLOW", window_s: 5 }] },
    suit_temp_c: { checks: [{ type: "limit", yellow_high: 28, red_high: 30, red_low: -5 }] },
  },
} as unknown as MissionDb;
const roc = (over = {}) => alarmView({ level: "YELLOW_FALLING", severity: "YELLOW", trigger_check: "roc", value: -0.4, ...over });
const lim = (over = {}) => alarmView({ param: "suit_temp_c", level: "RED_HIGH", value: 31.2, ...over });

test("rate alarm names the check, slope and limit", () =>
  expect(alarmText(roc(), MDB)).toBe("YELLOW · RATE FALLING −0.40 kPa/s (limit 0.15)"));

test("limit alarm on a value outside its band", () => {
  expect(alarmText(lim(), MDB)).toBe("RED HIGH · 31.20 degC (limit 30)");
  expect(alarmText(lim({ level: "YELLOW_HIGH", severity: "YELLOW", value: 29 }), MDB)).toBe("YELLOW HIGH · 29.00 degC (limit 28)");
  expect(alarmText(lim({ level: "RED_LOW", value: -7.5 }), MDB)).toBe("RED LOW · −7.50 degC (limit −5)");
});

test("a returned alarm keeps its last level (P-pf20); the value survives an ACK (P-pf4)", () => {
  expect(alarmText(lim({ state: "RTN_UNACK" }), MDB)).toBe("RETURNED · was RED HIGH");
  expect(alarmText(lim({ state: "ACKED" }), MDB)).toBe("RED HIGH · 31.20 degC (limit 30)");
});

test("without the mission db, a unit or a matching bound the text still says what the engine sent", () => {
  expect(alarmText(lim(), null)).toBe("RED HIGH · 31.20");
  expect(alarmText(lim({ param: "constructor" }), MDB)).toBe("RED HIGH · 31.20");
  expect(alarmText(lim({ value: null }), MDB)).toBe("RED HIGH · — (limit 30)");
  expect(alarmText(roc(), null)).toBe("YELLOW · RATE FALLING −0.40");
  expect(alarmText(roc({ level: "RED_RISING", severity: "RED" }), MDB)).toBe("RED · RATE RISING −0.40 kPa/s (limit 0.15)"); // falls back to the only roc check
});

test("hash prefixes are 8 hex digits", () =>
  expect(hashText(alarmView({ limits_sha256: "3f9a1c2e77aa", set_sha256: "5d07b8a1ffee" }))).toBe("limits 3f9a1c2e · set 5d07b8a1"));

test("a limit alarm prints its reading, a rate alarm its slope: the two are never mixed up (P-pf25)", () => {
  const reading = alarmView({ level: "RED_LOW", value: 94.2 }); // limit: the sample
  const slope = roc({ value: -0.4 }); // roc: signed slope per second
  expect(alarmText(reading, MDB)).toBe("RED LOW · 94.20 kPa (limit 95)");
  expect(alarmText(slope, MDB)).toBe("YELLOW · RATE FALLING −0.40 kPa/s (limit 0.15)");
  expect(alarmText(alarmView({ level: "YELLOW_HIGH", severity: "YELLOW", value: 100.8 }), MDB)).toBe("YELLOW HIGH · 100.80 kPa"); // no yellow_high bound in this check: none invented
});

test("a derived parameter's alarm carries its unit", () =>
  expect(alarmText(lim({ param: "power_w", value: 310 }), MDB)).toBe("RED HIGH · 310.00 W (limit 300)"));
