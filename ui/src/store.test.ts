import { expect, test, vi } from "vitest";
import { createStore, ingest, type Snapshot, type Tm } from "./store";

const tm = (seq: number, t: string, vehicle = "NYX-TA1", p = 101.3): Tm => ({
  kind: "tm", vehicle, seq, t_onboard: t, mode: "TEST",
  last_cmd_id: null, values: { cabin_pressure_kpa: p }, age_ms: 5, frozen: false,
});
const snap = (...rows: Tm[]): Snapshot => ({
  kind: "snapshot", vehicles: Object.fromEntries(rows.map((r) => [r.vehicle, r])),
  alarms: [], commands: [], events: [],
});

test("applyTm drops a tm not newer than the shown one", () => {
  const s = createStore();
  expect(s.applyTm(tm(1, "2026-11-14T10:00:00.100000Z"))).toBe(true);
  expect(s.applyTm(tm(1, "2026-11-14T10:00:00.100000Z"))).toBe(false);
  expect(s.applyTm(tm(0, "2026-11-14T10:00:00.099999Z"))).toBe(false);
  expect(s.applyTm(tm(2, "2026-11-14T10:00:01.000000Z"))).toBe(true);
});

test("whole-second timestamps order correctly", () => {
  const s = createStore();
  s.applyTm(tm(1, "2026-11-14T10:00:00.000000Z"));
  expect(s.applyTm(tm(2, "2026-11-14T10:00:00.000001Z"))).toBe(true);
  expect(s.vehicle("NYX-TA1")?.seq).toBe(2);
  expect(s.applyTm(tm(1, "2026-11-14T10:00:00.000000Z"))).toBe(false);
});

test("a dropped tm leaves the shown row untouched", () => {
  const s = createStore();
  s.applyTm(tm(5, "2026-11-14T10:00:05.000000Z", "NYX-TA1", 1));
  s.applyTm(tm(4, "2026-11-14T10:00:04.000000Z", "NYX-TA1", 2));
  expect(s.vehicle("NYX-TA1")?.values.cabin_pressure_kpa).toBe(1);
});

test("snapshot replaces state, including vehicles absent from it", () => {
  const s = createStore();
  s.applyTm(tm(9, "2026-11-14T10:00:09.000000Z", "NYX-TA1"));
  s.applySnapshot(snap(tm(1, "2026-11-14T10:00:01.000000Z", "NYX-TA2")));
  expect(s.vehicles()).toEqual(["NYX-TA2"]);
  expect(s.vehicle("NYX-TA1")).toBeUndefined();
});

test("snapshot may move a vehicle back in time (reconnect after restart)", () => {
  const s = createStore();
  s.applyTm(tm(9, "2026-11-14T10:00:09.000000Z"));
  s.applySnapshot(snap(tm(1, "2026-11-14T10:00:01.000000Z")));
  expect(s.vehicle("NYX-TA1")?.seq).toBe(1);
});

test("vehicles are sorted and the list is stable between changes", () => {
  const s = createStore();
  s.applyTm(tm(1, "2026-11-14T10:00:00.000000Z", "NYX-TA2"));
  s.applyTm(tm(1, "2026-11-14T10:00:00.000000Z", "NYX-TA1"));
  expect(s.vehicles()).toEqual(["NYX-TA1", "NYX-TA2"]);
  expect(s.vehicles()).toBe(s.vehicles());
});

test("subscribe notifies once per accepted change and stops after unsubscribe", () => {
  const s = createStore();
  const fn = vi.fn();
  const off = s.subscribe(fn);
  s.applyTm(tm(1, "2026-11-14T10:00:00.100000Z"));
  s.applyTm(tm(1, "2026-11-14T10:00:00.100000Z")); // dropped
  expect(fn).toHaveBeenCalledTimes(1);
  s.applySnapshot(snap());
  expect(fn).toHaveBeenCalledTimes(2);
  off();
  s.applyTm(tm(2, "2026-11-14T10:00:01.000000Z"));
  expect(fn).toHaveBeenCalledTimes(2);
});

test("ingest routes snapshot and tm and ignores other kinds", () => {
  const s = createStore();
  ingest(s, "snapshot", snap(tm(1, "2026-11-14T10:00:01.000000Z")));
  ingest(s, "tm", tm(2, "2026-11-14T10:00:02.000000Z"));
  expect(s.vehicle("NYX-TA1")?.seq).toBe(2);
  ingest(s, "hello", { kind: "hello", server_ms: 1 });
  expect(s.vehicles()).toEqual(["NYX-TA1"]);
});

test("ingest rejects wrong-shape events with a log and leaves state alone", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const s = createStore();
  ingest(s, "tm", tm(1, "2026-11-14T10:00:01.000000Z"));
  for (const bad of [null, "x", {}, { kind: "snapshot" }, { vehicles: { a: {} } }])
    ingest(s, "snapshot", bad);
  for (const bad of [null, 5, {}, { vehicle: "V" }, { vehicle: "V", t_onboard: "t" }, { ...tm(2, "t"), values: null }])
    ingest(s, "tm", bad);
  expect(s.vehicles()).toEqual(["NYX-TA1"]);
  expect(s.vehicle("NYX-TA1")?.seq).toBe(1);
  expect(warn).toHaveBeenCalledTimes(11);
  warn.mockRestore();
});

test("an empty snapshot is valid and clears the table", () => {
  const s = createStore();
  ingest(s, "tm", tm(1, "2026-11-14T10:00:01.000000Z"));
  ingest(s, "snapshot", { kind: "snapshot", vehicles: {} });
  expect(s.vehicles()).toEqual([]);
});

test("the store stamps when each vehicle's latest tm arrived", () => {
  let now = 100;
  const s = createStore(() => now);
  expect(s.receivedAt("NYX-TA1")).toBeUndefined();
  s.applyTm(tm(1, "2026-11-14T10:00:01.000000Z"));
  expect(s.receivedAt("NYX-TA1")).toBe(100);
  now = 250;
  s.applyTm(tm(1, "2026-11-14T10:00:01.000000Z")); // dropped: stamp unchanged
  expect(s.receivedAt("NYX-TA1")).toBe(100);
  s.applySnapshot(snap(tm(2, "2026-11-14T10:00:02.000000Z"), tm(2, "2026-11-14T10:00:02.000000Z", "NYX-TA2")));
  expect([s.receivedAt("NYX-TA1"), s.receivedAt("NYX-TA2")]).toEqual([250, 250]);
});
