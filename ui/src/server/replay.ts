import type { components } from "./schema";
import { paramState } from "../state/states";
import { createStore, type AlarmView, type EventView, type Tm } from "../state/store";

type ReplayEnd = components["schemas"]["ReplayEnd"];
export type Ended = { reason: ReplayEnd["reason"] | "interrupted" | "expired"; detail: string | null };
export const REPLAY_KINDS = ["replay_start", "tm", "alarm", "link", "clock", "replay_end"] as const;

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null;
const never = { setTimeout: (() => undefined) as unknown as typeof setTimeout, clearTimeout: () => {} };

/** ISO 8601 UTC "YYYY-MM-DDTHH:MM[:SS]Z" (Z required, D-UI); null if empty or unreadable (e.g. 2026-02-31). */
export function utcInput(v: string): string | null {
  const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?Z$/.exec(v.trim());
  if (!m) return null;
  const d = new Date(`${m[1]}${m[2] ?? ":00"}Z`);
  return Number.isNaN(d.getTime()) || !d.toISOString().startsWith(m[1]!) ? null : d.toISOString();
}

/** The live row state with age = replay t − t_onboard, STALE at 2.0 s of vehicle time (D-REP-06). */
export const replayParamState = (value: number | null, tOnboard: string, t: number, frozen: boolean, alarm: AlarmView | undefined) =>
  paramState(value, t - Date.parse(tOnboard), frozen, alarm);

export const endText = (e: Ended): string =>
  ({
    done: "Replay finished",
    stopped: "Replay stopped",
    // ponytail: neither brief nor design words a mid-replay corrupt hour; the 409 text is the server's
    archive_corrupt: `Replay stopped: an archive object failed its hash check${e.detail ? ` (${e.detail})` : ""}`,
    interrupted: "Replay stopped: archive or database unavailable – start it again",
    expired: "This replay has ended – start it again",
  })[e.reason];

/**
 * A store apart from the live one (D-REP-03). It is a live `Store` on replay time: the clock is the latest tm or
 * clock time, so receipt stamps and ages are vehicle seconds and the live table, alarm list and event strip read it
 * unchanged. Timers are off: a silent stretch of the recording is not a lost connection.
 */
export function createReplayStore() {
  let t: number | null = null;
  let losOpen = false;
  let ended: Ended | null = null;
  const store = createStore(() => t ?? 0, never);
  const subs = new Set<() => void>();
  const notify = () => {
    subs.forEach((f) => f());
  };
  const advance = (ms: number) => {
    if (!Number.isNaN(ms) && (t === null || ms > t)) t = ms;
  };
  return {
    store,
    clock: () => t ?? 0,
    t: (): number | null => t,
    losOpen: () => losOpen,
    ended: () => ended,
    /** The stream ended without replay_end: `opened` = it had connected (db or archive failed mid-stream). */
    interrupt(opened: boolean) {
      if (ended) return;
      ended = { reason: opened ? "interrupted" : "expired", detail: null };
      notify();
    },
    subscribe(f: () => void) {
      subs.add(f);
      return () => void subs.delete(f);
    },
    /** internals for ingestReplay */
    _: { advance, notify, setLos: (b: boolean) => (losOpen = b), end: (e: Ended) => (ended = e) },
  };
}
export type ReplayStore = ReturnType<typeof createReplayStore>;

/** Route one parsed ReplayEvent into the replay store; wrong shapes are dropped. */
export function ingestReplay(rs: ReplayStore, kind: string, data: unknown): void {
  const d = isObj(data) ? data : {};
  const { store, _: i } = rs;
  if (kind === "replay_start" && typeof d.vehicle === "string" && typeof d.t_from === "string" && Array.isArray(d.alarms)) {
    i.advance(Date.parse(d.t_from));
    i.setLos(d.los_open === true);
    store.applySnapshot({ kind: "snapshot", vehicles: {}, alarms: d.alarms as AlarmView[], commands: [], events: [] });
    store.setGround("up");
    store.setProcessing({ [d.vehicle]: true });
    store.noteMessage();
  } else if (kind === "tm" && typeof d.vehicle === "string" && typeof d.t_onboard === "string" && isObj(d.values)) {
    i.advance(Date.parse(d.t_onboard));
    const age = Math.max(0, rs.clock() - Date.parse(d.t_onboard));
    store.applyTm({ ...(d as unknown as Tm), age_ms: age }); // the age at receipt, on replay time
  } else if (kind === "alarm" && isObj(d.alarm)) store.applyAlarm(d.alarm as unknown as AlarmView);
  else if (kind === "link" && isObj(d.event)) {
    const e = d.event as unknown as EventView;
    store.applyLink(e);
    if (e.kind === "LOS") i.setLos(true);
    else if (e.kind === "AOS") i.setLos(false);
  } else if (kind === "clock" && typeof d.t === "string") i.advance(Date.parse(d.t));
  else if (kind === "replay_end" && typeof d.reason === "string") i.end({ reason: d.reason as ReplayEnd["reason"], detail: typeof d.detail === "string" ? d.detail : null });
  else return;
  i.notify();
}

/** Gate 4 cross-track contract: NYX-TA9 is backfill-only (D-ENG-16), so it is not in the API's live VEHICLES; only the replay page offers it (G4-19, G4-23). */
export const RECORDED_ONLY_VEHICLES = ["NYX-TA9"];
