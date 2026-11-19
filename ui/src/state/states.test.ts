import { expect, test } from "vitest";
import { paramState, type AlarmView } from "./states";

const alarm = (level: string, state: AlarmView["state"] = "UNACK", trigger_check = "limit"): AlarmView =>
  ({ level, state, trigger_check, severity: level.startsWith("RED") ? "RED" : "YELLOW" }) as AlarmView;

test("RED_HIGH alarm → RED, HIGH, with the check kind", () => {
  expect(paramState(1, 0, false, alarm("RED_HIGH"))).toEqual({ acq: "OK", level: "RED", side: "HIGH", check_kind: "limit" });
  expect(paramState(1, 0, false, alarm("YELLOW_LOW", "ACKED")).level).toBe("YELLOW");
});

test("RTN_UNACK and no alarm are NOMINAL", () => {
  expect(paramState(1, 0, false, alarm("RED_HIGH", "RTN_UNACK"))).toEqual({ acq: "OK", level: "NOMINAL" });
  expect(paramState(1, 0, false, undefined)).toEqual({ acq: "OK", level: "NOMINAL" });
});

test("rate alarms carry RISING / FALLING", () => {
  expect(paramState(1, 0, false, alarm("YELLOW_FALLING", "UNACK", "roc"))).toMatchObject({ side: "FALLING", check_kind: "roc" });
});

test("an unreadable level text falls back to the severity, with no side", () => {
  expect(paramState(1, 0, false, { ...alarm("weird"), severity: "RED" })).toMatchObject({ level: "RED" });
  expect(paramState(1, 0, false, alarm("RED_SIDEWAYS"))).not.toHaveProperty("side");
});

test("acquisition: null → NO_DATA, frozen → FROZEN, then STALE, else OK; the alarm level survives", () => {
  expect(paramState(null, 0, true, undefined).acq).toBe("NO_DATA");
  expect(paramState(1, 5000, true, undefined).acq).toBe("FROZEN");
  expect(paramState(1, 2000, false, alarm("RED_LOW"))).toMatchObject({ acq: "STALE", level: "RED" });
  for (const unknown of [null, undefined]) expect(paramState(1, unknown, false, undefined).acq).toBe("STALE"); // P-pf22
  expect(paramState(null, null, false, undefined).acq).toBe("NO_DATA");
});
