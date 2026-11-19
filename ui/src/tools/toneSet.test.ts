import { expect, test } from "vitest";
import { toneActive, toneKeys, type ToneState } from "./audio";
import { createKeySet, createToneKeySet } from "../state/keySet";
import { createStore, ingest } from "../state/store";

const memoryStorage = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
};

const st = (active: Record<string, string>, lastPlayAt: number | null = null): ToneState => ({ active, lastPlayAt });
const none = st({});

test("one tone per onset; LOS uses its event key; dedupe across reload", () => {
  const storage = memoryStorage();
  const next = st({ "LOS/NYX-TA1/1/7": "LOS" });
  expect(toneKeys(none, next, 0, createKeySet("tones", storage))).toEqual({ play: true, keys: ["LOS/NYX-TA1/1/7"] });
  expect(toneKeys(next, next, 100, createKeySet("tones", storage))).toEqual({ play: false, keys: [] }); // no repeat
  expect(toneKeys(none, next, 5000, createKeySet("tones", storage))).toEqual({ play: false, keys: [] }); // reload
});

test("BROKER and NLC are console-wide and coalesce within one second", () => {
  const both = toneKeys(none, st({ "*|BROKER": "BROKER", "*|NLC": "NLC" }), 0);
  expect(both).toEqual({ play: true, keys: ["*|BROKER", "*|NLC"] });
  const later = toneKeys(st({ "*|NLC": "NLC" }, 0), st({ "*|NLC": "NLC", "TA1|GPD": "GPD" }), 999);
  expect(later).toEqual({ play: false, keys: ["TA1|GPD"] });
  expect(toneKeys(st({}, 0), st({ "*|NLC": "NLC" }), 1000).play).toBe(true);
});

test("an ended state plays again on its next onset", () => {
  const seen = createKeySet("tones", memoryStorage());
  const on = st({ "*|NLC": "NLC" });
  expect(toneKeys(none, on, 0, seen).play).toBe(true);
  expect(toneKeys(on, none, 5000, seen).play).toBe(false); // ends: forgotten
  expect(toneKeys(none, on, 9000, seen).play).toBe(true);
});

test("an event-keyed onset is not forgotten when its state ends", () => {
  const seen = createKeySet("tones", memoryStorage());
  const on = st({ "LOS/V/1/7": "LOS" });
  toneKeys(none, on, 0, seen);
  toneKeys(on, none, 5000, seen);
  expect(toneKeys(none, on, 9000, seen).play).toBe(false);
});

const tm = (frozen = false, age_ms: number | null = 5000) => ({
  kind: "tm", vehicle: "NYX-TA1", seq: 1, t_onboard: "2026-11-14T10:00:00.000000Z", mode: "TEST",
  last_cmd_id: null, values: { p: 1 }, age_ms, frozen,
});
const ev = (kind: string, key: string) => ({ kind: "link", event: { key, kind, vehicle: "NYX-TA1", t_onboard: null, details: {} } });
const snap = (frozen = false) => ({ kind: "snapshot", vehicles: { "NYX-TA1": tm(frozen) }, alarms: [], commands: [], events: [] });

test("a STALE parameter is silent, however old", () => {
  const s = createStore();
  ingest(s, "snapshot", snap());
  expect(toneActive(s)).toEqual({});
});

test("LOS and FROZEN come from their link events; AOS ends the LOS", () => {
  const s = createStore();
  ingest(s, "snapshot", snap(true));
  ingest(s, "link", ev("FROZEN", "FROZEN/NYX-TA1/1"));
  expect(toneActive(s)).toEqual({ "FROZEN/NYX-TA1/1": "FROZEN" });
  ingest(s, "link", ev("LOS", "LOS/NYX-TA1/1/7"));
  expect(toneActive(s)).toEqual({ "LOS/NYX-TA1/1/7": "LOS" });
  ingest(s, "link", ev("AOS", "AOS/NYX-TA1/1/8"));
  expect(toneActive(s)).toEqual({ "FROZEN/NYX-TA1/1": "FROZEN" });
  ingest(s, "snapshot", snap(false));
  expect(toneActive(s)).toEqual({});
});

test("processing down, broker down and lost link, with the banner's precedence", () => {
  const s = createStore();
  ingest(s, "snapshot", snap());
  ingest(s, "hb", { kind: "hb", server_ms: 1, processing: { "NYX-TA1": false } });
  expect(toneActive(s)).toEqual({ "NYX-TA1|GPD": "GPD" });
  ingest(s, "ground", { kind: "ground", broker: "down" });
  expect(toneActive(s)).toEqual({ "*|BROKER": "BROKER" });
  s.noteError();
  expect(toneActive(s)).toEqual({ "*|NLC": "NLC" });
});

test("no NLC tone before the first snapshot", () => {
  expect(toneActive(createStore())).toEqual({});
});

test("state keys are memory only: nothing of them reaches the storage, a reload starts clean", () => {
  const storage = memoryStorage();
  const seen = createToneKeySet(storage);
  const nlc = st({ "*|NLC": "NLC" });
  const los = st({ "LOS/V/1/7": "LOS" });
  expect(toneKeys(none, nlc, 0, seen).play).toBe(true);
  expect(toneKeys(none, los, 5000, seen).play).toBe(true);
  expect(storage.getItem("nyx.tones")).toBe('["LOS/V/1/7"]');
  const reloaded = createToneKeySet(storage); // reload: NLC key gone, LOS key remembered
  expect(toneKeys(none, nlc, 9000, reloaded).play).toBe(true);
  expect(toneKeys(none, los, 12000, reloaded).play).toBe(false);
});
