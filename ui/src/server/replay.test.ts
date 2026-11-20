import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { beepsFor } from "../tools/audio";
import { alarmView, eventView } from "../test/fixtures";
import { createReplayStore, endText, ingestReplay, replayParamState, utcInput } from "./replay";
import { createStore, ingest } from "../state/store";
import { vehicleCondition } from "../state/banners";
import { ageAt } from "../tools/format";
import { paramState } from "../state/states";

const V = "NYX-TA1";
const seen = () => { const s = new Set<string>(); return { has: (k: string) => s.has(k), add: (k: string) => !s.has(k) && !!s.add(k), delete: (k: string) => void s.delete(k) }; };
const T0 = "2026-11-10T14:00:00.000000Z";
const iso = (s: number) => new Date(Date.parse(T0) + s * 1000).toISOString();
const start = (over: Record<string, unknown> = {}) => ({
  kind: "replay_start", replay: true, replay_id: "r1", vehicle: V, t_from: T0, t_to: iso(60), speed: 1, alarms: [], los_open: false, ...over,
});
const tm = (s: number) => ({
  kind: "tm", replay: true, vehicle: V, seq: s, t_onboard: iso(s), mode: "TEST", frozen: false, age_ms: null,
  last_cmd_id: null, values: { cabin_pressure_kpa: 101 }, severity: {},
});
const clock = (s: number) => ({ kind: "clock", replay: true, t: iso(s) });

test("stale on replay time at 1x and 10x", () => {
  // the state is a function of replay seconds only: speed changes wall time, never this
  for (const speed of [1, 10]) {
    const rs = createReplayStore();
    ingestReplay(rs, "replay_start", start({ speed }));
    ingestReplay(rs, "tm", tm(0));
    ingestReplay(rs, "clock", clock(1.9));
    const ageNow = () => ageAt(rs.store.vehicle(V)?.age_ms, rs.store.receivedAt(V), rs.clock());
    expect(ageNow()).toBeCloseTo(1900, 0);
    expect(paramState(101, ageNow(), false, undefined).acq).toBe("OK");
    ingestReplay(rs, "clock", clock(2.0));
    expect(paramState(101, ageNow(), false, undefined).acq).toBe("STALE");
    expect(vehicleCondition(rs.store, V, rs.clock())).toBe("LOSS OF SIGNAL");
  }
  expect(replayParamState(101, iso(0), Date.parse(iso(1.9)), false, undefined).acq).toBe("OK");
  expect(replayParamState(101, iso(0), Date.parse(iso(2)), false, undefined).acq).toBe("STALE");
});

test("t follows the latest of clock and tm and never goes back", () => {
  const rs = createReplayStore();
  ingestReplay(rs, "replay_start", start());
  ingestReplay(rs, "clock", clock(5));
  ingestReplay(rs, "tm", tm(3));
  expect(rs.t()).toBe(Date.parse(iso(5)));
  ingestReplay(rs, "tm", tm(7));
  expect(rs.t()).toBe(Date.parse(iso(7)));
});

test("los from recorded rows and replay_start", () => {
  const rs = createReplayStore();
  ingestReplay(rs, "replay_start", start({ los_open: true }));
  expect(rs.losOpen()).toBe(true);
  ingestReplay(rs, "link", { kind: "link", replay: true, event: eventView("aos1", { kind: "AOS", vehicle: V }) });
  expect(rs.losOpen()).toBe(false);
  ingestReplay(rs, "link", { kind: "link", replay: true, event: eventView("los1", { kind: "LOS", vehicle: V }) });
  expect(rs.losOpen()).toBe(true);
  expect(rs.store.events().map((e) => e.key)).toEqual(["los1", "aos1"]); // newest first
});

test("replay alarms never reach the beep set", () => {
  const live = createStore();
  const rs = createReplayStore();
  const red = alarmView({ alarm_id: "r", last: { ...alarmView().last, key: "replay-key", alert: true } });
  ingestReplay(rs, "replay_start", start({ alarms: [red] }));
  ingestReplay(rs, "alarm", { kind: "alarm", replay: true, alarm: alarmView({ alarm_id: "r2" }) });
  expect(rs.store.alarms()).toHaveLength(2);
  expect(beepsFor(live.alarms(), seen())).toEqual([]); // the live store the audio reads is untouched
  expect(live.alarms()).toEqual([]);
  ingest(live, "alarm", { kind: "alarm", alarm: red });
  expect(beepsFor(live.alarms(), seen())).toEqual(["replay-key"]); // a live RED still beeps
  // the replay code never imports the audio module, from whichever folder
  for (const f of ["replay.ts", "../components/ReplayPage.tsx"]) expect(readFileSync(`${__dirname}/${f}`, "utf8")).not.toMatch(/from "[^"]*\/audio"/);
});

test("end reasons and an interrupted stream have their own text", () => {
  const rs = createReplayStore();
  expect(rs.ended()).toBeNull();
  ingestReplay(rs, "replay_end", { kind: "replay_end", replay: true, reason: "done", detail: null });
  expect(endText(rs.ended()!)).toBe("Replay finished");
  rs.interrupt(false);
  expect(rs.ended()?.reason).toBe("done"); // an end that came is not overwritten
  const b = createReplayStore();
  b.interrupt(true);
  expect(endText(b.ended()!)).toBe("Replay stopped: archive or database unavailable – start it again");
  const c = createReplayStore();
  c.interrupt(false); // never opened: the 404 of an expired or already taken replay
  expect(endText(c.ended()!)).toBe("This replay has ended – start it again");
  expect(endText({ reason: "archive_corrupt", detail: "hour 14" })).toBe("Replay stopped: an archive object failed its hash check (hour 14)");
});

test("utcInput reads ISO 8601 UTC with a required Z, with or without seconds", () => {
  expect(utcInput("2026-11-10T14:00Z")).toBe("2026-11-10T14:00:00.000Z");
  expect(utcInput("2026-11-10T14:00:07Z")).toBe("2026-11-10T14:00:07.000Z");
  expect(utcInput(" 2026-11-10T14:00Z ")).toBe("2026-11-10T14:00:00.000Z");
  for (const bad of ["", "2026-11-10T14:00", "2026-11-10T14:00:00", "11/10/2026, 02:00 PM", "2026-11-10 14:00Z", "2026-11-10T14:00+02:00", "2026-11-10T25:00Z", "2026-02-31T10:00Z", "garbage"])
    expect(utcInput(bad), bad).toBeNull();
});
