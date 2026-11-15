import { expect, test } from "vitest";
import { conditionFor, vehicleCondition } from "./banners";
import { eventView } from "./fixtures";
import { toneActive } from "./audio";
import { createStore, ingest, type EventView, type Snapshot, type Tm } from "./store";

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

// the real store + ageAt + conditionFor together (vehicleCondition, as the live table renders it), fed the wire
// messages through ingest()
const V = "NYX-TA1";
const W = "NYX-TA2";
const frame = (seq: number, ageMs = 5): Tm => ({
  kind: "tm", vehicle: V, seq, t_onboard: new Date(Date.UTC(2026, 10, 15, 10) + seq * 100).toISOString().replace("Z", "000Z"),
  mode: "TEST", last_cmd_id: null, values: { cabin_pressure_kpa: 101.3 }, age_ms: ageMs, frozen: false,
});
const LOS = eventView("LOS/NYX-TA1/e/0", { kind: "LOS", vehicle: V });
/** The snapshot the server sends on page load, after ground{up} and on a resync (hub.build_snapshot): the newest
 *  50 events (here all of NYX-TA2's), then each vehicle's newest LOS/AOS when it is older, and the last frame
 *  (received at 0 ms) with its server age. */
const serverSnapshot = (now: number, link?: EventView): Snapshot => ({
  kind: "snapshot", vehicles: { [V]: frame(0, now) }, alarms: [], commands: [],
  events: [...Array.from({ length: 50 }, (_, i) => eventView(`GAP/${W}/${i}`, { kind: "GAP", vehicle: W })), ...(link ? [link] : [])],
});

/** A live vehicle loaded at 0 ms; `losAt` its server LOS (then silent); an outage of the broker or of the
 *  vehicle's owner from 5 s to 15 s, ended as the server ends it: ground{up} or hb true, then the snapshot. */
function outage(what: "broker" | "owner", losAt?: number) {
  let now = 0;
  const at = (t: number) => void (now = t);
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} });
  ingest(s, "snapshot", serverSnapshot(0));
  ingest(s, "hb", { processing: { [V]: true } });
  if (losAt !== undefined) {
    at(losAt);
    ingest(s, "link", { event: LOS });
    expect(vehicleCondition(s, V, losAt)).toBe("LOSS OF SIGNAL");
  }
  at(5000);
  if (what === "broker") ingest(s, "ground", { broker: "down" });
  else ingest(s, "hb", { processing: { [V]: false } });
  expect(vehicleCondition(s, V, 10_000)).toMatch(/^GROUND PROCESSING DOWN/);
  at(15_000);
  if (what === "broker") ingest(s, "ground", { broker: "up" });
  else ingest(s, "hb", { processing: { [V]: true } });
  ingest(s, "snapshot", serverSnapshot(15_000, losAt === undefined ? undefined : LOS));
  return { s, at, up: 15_000, cond: (t: number) => vehicleCondition(s, V, t) };
}

for (const what of ["broker", "owner"] as const) {
  test(`${what} outage, frames resume 500 ms after it ends: never LOSS OF SIGNAL`, () => {
    const { s, at, up, cond } = outage(what);
    for (let t = up, seq = 1; t <= up + 5000; t += 100) {
      at(t);
      if (t >= up + 500) ingest(s, "tm", frame(seq++));
      expect(cond(t), `up + ${t - up} ms`).not.toBe("LOSS OF SIGNAL");
    }
  });

  test(`${what} outage, the vehicle still silent after it: no LOS at +1999 ms, LOS at +2000 ms`, () => {
    const { up, cond } = outage(what);
    expect(cond(up + 1999)).toBeNull();
    expect(cond(up + 2000)).toBe("LOSS OF SIGNAL");
  });

  test(`${what} outage with a LOS open before it, past the newest snapshot events: LOS at once, the tone stays on`, () => {
    const { s, up, cond } = outage(what, 2500);
    for (let t = up; t <= up + 2000; t += 100) expect(cond(t), `up + ${t - up} ms`).toBe("LOSS OF SIGNAL");
    expect(toneActive(s)).toEqual({ [LOS.key]: "LOS" });
  });
}

test("page load: a vehicle silent for a minute whose open LOS is past the newest snapshot events shows LOS at once, toned", () => {
  const now = 100_000;
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} });
  ingest(s, "snapshot", serverSnapshot(60_000, LOS));
  ingest(s, "hb", { processing: { [V]: true } }); // unknown -> true: a recovery, but the LOS is open
  expect(vehicleCondition(s, V, now)).toBe("LOSS OF SIGNAL");
  expect(toneActive(s)).toEqual({ [LOS.key]: "LOS" });
});
