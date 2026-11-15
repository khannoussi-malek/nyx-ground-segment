import { afterEach, expect, test, vi } from "vitest";
import type { AlarmView, CommandView } from "./store";
import { alarmView, commandView, eventView } from "./fixtures";
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

afterEach(() => vi.useRealTimers());

const alarm = (id: string, state: AlarmView["state"], param = "p", vehicle = "NYX-TA1", at = "2026-11-14T10:00:00Z") =>
  alarmView({ alarm_id: id, state, param, vehicle, last: { ...alarmView().last, t: at } });
const cmd = (id: string, state: CommandView["state"] = "QUEUED") => commandView({ cmd_id: id, state });
const ev = (key: string) => eventView(key);

test("alarm upsert by id, CLEARED removed, openAlarm finds by vehicle and param", () => {
  const s = createStore();
  s.applyAlarm(alarm("a1", "UNACK"));
  s.applyAlarm(alarm("a1", "ACKED"));
  expect(s.alarms()).toHaveLength(1);
  expect(s.openAlarm("NYX-TA1", "p")?.state).toBe("ACKED");
  expect(s.openAlarm("NYX-TA2", "p")).toBeUndefined();
  s.applyAlarm(alarm("a1", "CLEARED"));
  expect(s.alarms()).toEqual([]);
  expect(s.openAlarm("NYX-TA1", "p")).toBeUndefined();
});

test("snapshot replaces alarms, commands and events and sets hasSnapshot", () => {
  const s = createStore();
  expect(s.hasSnapshot()).toBe(false);
  s.applyAlarm(alarm("old", "UNACK"));
  s.applyCmd(cmd("old"));
  s.applyLink(ev("old"));
  s.applySnapshot({ ...snap(), alarms: [alarm("n", "UNACK")], commands: [cmd("n")], events: [ev("n")] });
  expect(s.hasSnapshot()).toBe(true);
  expect([s.alarms()[0]?.alarm_id, s.commands()[0]?.cmd_id, s.events()[0]?.key]).toEqual(["n", "n", "n"]);
  expect([s.alarms().length, s.commands().length, s.events().length]).toEqual([1, 1, 1]);
});

test("command upsert by id", () => {
  const s = createStore();
  s.applyCmd(cmd("c1"));
  s.applyCmd(cmd("c1", "SENT"));
  expect(s.commands()).toHaveLength(1);
  expect(s.commands()[0]?.state).toBe("SENT");
});

test("link events dedupe by key, newest first, keep 200", () => {
  const s = createStore();
  s.applyLink(ev("a"));
  s.applyLink(ev("a"));
  s.applyLink(ev("b"));
  expect(s.events().map((e) => e.key)).toEqual(["b", "a"]);
  for (let i = 0; i < 250; i++) s.applyLink(ev(`k${i}`));
  expect(s.events()).toHaveLength(200);
  expect(s.events()[0]?.key).toBe("k249");
});

test("link state: broker, processing, selectors stable between changes", () => {
  const s = createStore();
  const l = s.link();
  expect(s.link()).toBe(l);
  expect(l).toEqual({ connected: false, broker: "up", processing: {} });
  s.setGround("down");
  s.setProcessing({ "NYX-TA1": false });
  expect(s.link()).toMatchObject({ broker: "down", processing: { "NYX-TA1": false } });
  expect(s.alarms()).toBe(s.alarms());
});

test("connected: a message sets it, an error clears it until the next message", () => {
  const s = createStore();
  s.noteMessage();
  expect(s.link().connected).toBe(true);
  s.noteError();
  expect(s.link().connected).toBe(false);
  s.noteMessage();
  expect(s.link().connected).toBe(true);
});

test("3 s without a message → connected false by a one-shot timer, re-armed by each message", () => {
  vi.useFakeTimers();
  const s = createStore();
  const fn = vi.fn();
  s.subscribe(fn);
  s.noteMessage();
  vi.advanceTimersByTime(2999);
  s.noteMessage(); // re-arm
  vi.advanceTimersByTime(2999);
  expect(s.link().connected).toBe(true);
  vi.advanceTimersByTime(1);
  expect(s.link().connected).toBe(false);
  expect(vi.getTimerCount()).toBe(0); // one-shot, nothing polling
  s.noteMessage();
  s.stop();
  expect(vi.getTimerCount()).toBe(0);
});

test("ingest routes every kind and counts each as a message", () => {
  const s = createStore();
  ingest(s, "hello", { kind: "hello", server_ms: 1 });
  expect(s.link().connected).toBe(true);
  ingest(s, "alarm", { kind: "alarm", alarm: alarm("a", "UNACK") });
  ingest(s, "cmd", { kind: "cmd", command: cmd("c") });
  ingest(s, "link", { kind: "link", event: ev("e") });
  ingest(s, "ground", { kind: "ground", broker: "down" });
  ingest(s, "hb", { kind: "hb", server_ms: 1, processing: { "NYX-TA1": true } });
  expect([s.alarms().length, s.commands().length, s.events().length]).toEqual([1, 1, 1]);
  expect(s.link()).toMatchObject({ broker: "down", processing: { "NYX-TA1": true } });
  ingest(s, "hb", { kind: "hb", server_ms: 2 }); // no processing map: keep the last
  expect(s.link().processing).toEqual({ "NYX-TA1": true });
});

test("ingest drops wrong-shape alarm, cmd, link, ground, hb events", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const s = createStore();
  ingest(s, "alarm", { alarm: {} });
  ingest(s, "cmd", { command: null });
  ingest(s, "link", {});
  ingest(s, "ground", { broker: "sideways" });
  ingest(s, "hb", { processing: 3 });
  expect([s.alarms().length, s.commands().length, s.events().length, s.link().broker]).toEqual([0, 0, 0, "up"]);
  expect(warn).toHaveBeenCalledTimes(5);
  warn.mockRestore();
});

test("an older alarm upsert is dropped, also after CLEARED (no flash back to UNACK)", () => {
  const s = createStore();
  s.applyAlarm(alarm("a1", "UNACK", "p", "NYX-TA1", "2026-11-14T10:00:05Z"));
  s.applyAlarm(alarm("a1", "UNACK", "p", "NYX-TA1", "2026-11-14T10:00:01Z")); // older replay
  expect(s.alarms()[0]?.last.t).toBe("2026-11-14T10:00:05Z");
  s.applyAlarm(alarm("a1", "CLEARED", "p", "NYX-TA1", "2026-11-14T10:00:09Z"));
  s.applyAlarm(alarm("a1", "UNACK", "p", "NYX-TA1", "2026-11-14T10:00:05Z"));
  expect(s.alarms()).toEqual([]);
  s.applyAlarm(alarm("a1", "UNACK", "p", "NYX-TA1", "2026-11-14T10:00:09Z")); // same time: accepted
  expect(s.alarms()).toHaveLength(1);
});

// final review F1: the server orders an ACK by its frame position (after_t_onboard = the last frame
// before the batch it was applied in, D-ALM-14), so its last.t can equal the transition it acknowledges
const step = (state: AlarmView["state"], kind: AlarmView["last"]["kind"], t: string, severity: AlarmView["severity"] = "YELLOW") =>
  alarmView({ alarm_id: "a1", state, severity, last: { ...alarmView().last, kind, t, key: `${kind}@${t}` } });

test("an ESCALATE processed after the ACK, from an earlier frame than the ACK's apply time, shows UNACK RED", () => {
  const s = createStore();
  s.applyAlarm(step("UNACK", "RAISE", "2026-11-14T10:00:00.900000Z"));
  s.applyAlarm(step("ACKED", "ACK", "2026-11-14T10:00:00.900000Z")); // after the RAISE frame
  s.applyAlarm(step("UNACK", "ESCALATE", "2026-11-14T10:00:01.400000Z", "RED"));
  expect(s.alarms().map((a) => [a.state, a.severity, a.last.kind])).toEqual([["UNACK", "RED", "ESCALATE"]]);
});

test("at the same time an ACK is newer than a frame transition, never older", () => {
  const s = createStore();
  s.applyAlarm(step("UNACK", "ESCALATE", "2026-11-14T10:00:00.900000Z", "RED"));
  s.applyAlarm(step("ACKED", "ACK", "2026-11-14T10:00:00.900000Z", "RED"));
  s.applyAlarm(step("UNACK", "ESCALATE", "2026-11-14T10:00:00.900000Z", "RED")); // replay of the ACKed frame
  expect(s.alarms().map((a) => a.state)).toEqual(["ACKED"]);
  s.applyAlarm(step("RTN_UNACK", "RTN", "2026-11-14T10:00:01.900000Z", "RED"));
  s.applyAlarm(step("CLEARED", "ACK", "2026-11-14T10:00:01.900000Z", "RED")); // ACK of RTN_UNACK clears
  s.applyAlarm(step("RTN_UNACK", "RTN", "2026-11-14T10:00:01.900000Z", "RED")); // replay: no flash back
  expect(s.alarms()).toEqual([]);
});

test("a snapshot's ACK keeps the same-time order", () => {
  const s = createStore();
  s.applySnapshot({ ...snap(), alarms: [step("ACKED", "ACK", "2026-11-14T10:00:00.900000Z")] });
  s.applyAlarm(step("UNACK", "RAISE", "2026-11-14T10:00:00.900000Z"));
  expect(s.alarms().map((a) => a.state)).toEqual(["ACKED"]);
});

test("a snapshot resets what 'older' means", () => {
  const s = createStore();
  s.applyAlarm(alarm("a1", "UNACK", "p", "NYX-TA1", "2026-11-14T10:00:09Z"));
  s.applySnapshot({ ...snap(), alarms: [alarm("a1", "UNACK", "p", "NYX-TA1", "2026-11-14T10:00:02Z")] });
  s.applyAlarm(alarm("a1", "ACKED", "p", "NYX-TA1", "2026-11-14T10:00:03Z"));
  expect(s.alarms()[0]?.state).toBe("ACKED");
});

test("an older command row (less evidence) is dropped, a newer one replaces", () => {
  const s = createStore();
  s.applyCmd(commandView({ state: "VERIFIED", sent_at: "2026-11-14T10:00:01Z", verified_at: "2026-11-14T10:00:03Z" }));
  s.applyCmd(commandView({ state: "SENT", sent_at: "2026-11-14T10:00:01Z" }));
  expect(s.commands()[0]?.state).toBe("VERIFIED");
  s.applyCmd(commandView({ state: "FAILED", failed_at: "2026-11-14T10:00:09Z" }));
  expect(s.commands()[0]?.state).toBe("FAILED");
});

test("ingest drops an alarm without severity, trigger_check or last.t", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const s = createStore();
  const { severity: _s, ...noSeverity } = alarmView();
  const { trigger_check: _t, ...noCheck } = alarmView();
  ingest(s, "alarm", { alarm: noSeverity });
  ingest(s, "alarm", { alarm: noCheck });
  ingest(s, "alarm", { alarm: { ...alarmView(), last: null } });
  expect(s.alarms()).toEqual([]);
  expect(warn).toHaveBeenCalledTimes(3);
  warn.mockRestore();
});

test("recheck after a throttled background tab: silence over 3 s is noticed without the timer", () => {
  let now = 0;
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} }); // timer never fires
  s.noteMessage();
  now = 2999;
  s.recheck();
  expect(s.link().connected).toBe(true);
  now = 3000;
  s.recheck();
  expect(s.link().connected).toBe(false);
});

test("healthySince: when the broker came back up, or the vehicle's owner came back; null if never interrupted", () => {
  let now = 1000;
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} });
  expect(s.healthySince("NYX-TA1")).toBeNull();
  s.setGround("up"); // up without a down first: no outage
  expect(s.healthySince("NYX-TA1")).toBeNull();
  s.setGround("down");
  now = 5000;
  s.setGround("up");
  expect(s.healthySince("NYX-TA1")).toBe(5000);
  s.setProcessing({ "NYX-TA1": false, "NYX-TA2": true });
  now = 9000;
  s.setProcessing({ "NYX-TA1": true, "NYX-TA2": true });
  expect(s.healthySince("NYX-TA1")).toBe(9000);
  expect(s.healthySince("NYX-TA2")).toBe(5000);
});

test("recheck after a throttled background tab: an hb over 3 s old forgets the processing map (P-pf42b)", () => {
  let now = 0;
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} }); // timer never fires
  s.setProcessing({ "NYX-TA1": true });
  now = 2999;
  s.noteMessage(); // other messages keep the link
  s.recheck();
  expect(s.link().processing).toEqual({ "NYX-TA1": true });
  now = 3000;
  s.recheck();
  expect(s.link()).toEqual({ connected: true, broker: "up", processing: {} });
});

test("the latency probe gets a receipt for every accepted tm, with its server age, and none for a dropped one", () => {
  const got: unknown[][] = [];
  const s = createStore(undefined, undefined, { receipt: (...a) => void got.push(a) });
  s.applyTm(tm(1, "2026-11-14T10:00:00.100000Z"));
  s.applyTm(tm(1, "2026-11-14T10:00:00.100000Z")); // dropped
  s.applyTm(tm(2, "2026-11-14T10:00:00.200000Z", "NYX-TA2"));
  expect(got).toEqual([["NYX-TA1", 1, 5], ["NYX-TA2", 2, 5]]);
});

test("healthySince: an owner word back after the hb went silent (unknown -> true) is a recovery too", () => {
  let now = 1000;
  const fire: (() => void)[] = [];
  const s = createStore(() => now, { setTimeout: ((fn: () => void) => fire.push(fn)) as never, clearTimeout: () => {} });
  s.setProcessing({ "NYX-TA1": true });
  fire.at(-1)!(); // no hb for SILENCE_MS: the map is forgotten
  expect(s.link().processing).toEqual({});
  now = 7000;
  s.setProcessing({ "NYX-TA1": true });
  expect(s.healthySince("NYX-TA1")).toBe(7000);
});

test("healthySince is null while the vehicle's LOS is open: a loss from before an outage gets no grace", () => {
  let now = 1000;
  const s = createStore(() => now, { setTimeout: (() => 1) as never, clearTimeout: () => {} });
  s.applyLink(eventView("los:1", { kind: "LOS", vehicle: "NYX-TA1" }));
  s.setGround("down");
  now = 5000;
  s.setGround("up");
  expect(s.healthySince("NYX-TA1")).toBeNull();
  s.applyLink(eventView("aos:1", { kind: "AOS", vehicle: "NYX-TA1" }));
  expect(s.healthySince("NYX-TA1")).toBe(5000);
});
