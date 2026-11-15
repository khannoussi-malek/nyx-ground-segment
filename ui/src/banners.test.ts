import { expect, test } from "vitest";
import { conditionFor, vehicleCondition } from "./banners";
import { eventView } from "./fixtures";
import { createStore, type Tm } from "./store";

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

test("LOS counts from when monitoring came back: a broker or owner outage is not loss of signal (D-UI-04/15)", () => {
  // frames resume a moment after the broker or the owner is back; until 2 s of healthy silence, no LOS
  expect(conditionFor({ ...base, ageMs: 12_000, healthyMs: 500 })).toBeNull();
  expect(conditionFor({ ...base, ageMs: 12_000, healthyMs: 1999 })).toBeNull();
  expect(conditionFor({ ...base, ageMs: 12_000, healthyMs: 2000 })).toBe("LOSS OF SIGNAL");
  expect(conditionFor({ ...base, ageMs: 12_000, healthyMs: null })).toBe("LOSS OF SIGNAL"); // never interrupted
});

// the real store + ageAt + conditionFor together (vehicleCondition, as the live table renders it)
const V = "NYX-TA1";
const frame = (seq: number): Tm => ({
  kind: "tm", vehicle: V, seq, t_onboard: new Date(Date.UTC(2026, 10, 15, 10) + seq * 100).toISOString().replace("Z", "000Z"),
  mode: "TEST", last_cmd_id: null, values: { cabin_pressure_kpa: 101.3 }, age_ms: 5, frozen: false,
});

/** A live vehicle (frame at 0 ms), `before` at its own times, then the broker down for 10 s. */
function outage(before: (s: ReturnType<typeof createStore>, at: (t: number) => void) => void = () => {}) {
  let now = 0;
  const at = (t: number) => void (now = t);
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} });
  s.applySnapshot({ kind: "snapshot", vehicles: { [V]: frame(0) }, alarms: [], commands: [], events: [] });
  s.noteMessage();
  s.setProcessing({ [V]: true });
  before(s, at);
  at(Math.max(now, 1000));
  s.setGround("down");
  expect(vehicleCondition(s, V, now)).toBe("GROUND PROCESSING DOWN – BROKER");
  at(now + 10_000);
  s.setGround("up");
  return { s, at, up: now, cond: (t: number) => vehicleCondition(s, V, t) };
}

test("frames resume 500 ms after the broker is back: never LOSS OF SIGNAL", () => {
  const { s, at, up, cond } = outage();
  for (let t = up, seq = 1; t <= up + 5000; t += 100) {
    at(t);
    if (t >= up + 500) s.applyTm(frame(seq++));
    expect(cond(t), `up + ${t - up} ms`).not.toBe("LOSS OF SIGNAL");
  }
});

test("a vehicle still silent after the broker is back: no LOS at +1999 ms, LOS at +2000 ms", () => {
  const { up, cond } = outage();
  expect(cond(up + 1999)).toBeNull();
  expect(cond(up + 2000)).toBe("LOSS OF SIGNAL");
});

test("a LOS open before the outage shows on the first redraw after it (the server kept it open)", () => {
  const { up, cond } = outage((s, at) => {
    at(2500);
    s.applyLink(eventView("los:NYX-TA1:1", { kind: "LOS", vehicle: V }));
    expect(vehicleCondition(s, V, 2500)).toBe("LOSS OF SIGNAL");
  });
  for (let t = up; t <= up + 2000; t += 100) expect(cond(t), `up + ${t - up} ms`).toBe("LOSS OF SIGNAL");
});
