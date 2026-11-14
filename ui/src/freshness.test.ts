import { expect, test } from "vitest";
import { ageAt } from "./format";
import { paramState } from "./states";

test("old t_onboard + fresh receipt → STALE", () => {
  // server age at send 2500 ms, received just now
  expect(paramState(1, ageAt(2500, 0, 0), false, undefined).acq).toBe("STALE");
});

test("STALE at 2.0 s, OK at 1.9 s", () => {
  expect(paramState(1, 2000, false, undefined).acq).toBe("STALE");
  expect(paramState(1, 1900, false, undefined).acq).toBe("OK");
});

test("age grows with time since receipt", () => {
  expect(paramState(1, ageAt(1500, 0, 600), false, undefined).acq).toBe("STALE");
});
