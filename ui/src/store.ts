import type { components } from "./api/schema";

export type Tm = components["schemas"]["Tm"];
export type Snapshot = components["schemas"]["Snapshot"];
export type AlarmView = components["schemas"]["AlarmView"];
export type CommandView = components["schemas"]["CommandView"];
export type EventView = components["schemas"]["EventView"];
export type Link = { connected: boolean; broker: "up" | "down"; processing: Record<string, boolean> };

const ms = (t: string | null): number => (t === null ? -Infinity : Date.parse(t));
/** Newest time a command row carries; a row with more evidence is newer. */
const cmdTime = (c: CommandView): number =>
  Math.max(...[c.queued_at, c.sent_at, c.received_at, c.verified_at, c.failed_at, c.late_ack_at, c.late_verified_at].map(ms));

export const SILENCE_MS = 3000;
export const EVENTS_KEPT = 200;

export function createStore(
  clock: () => number = () => performance.now(),
  timers = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout },
) {
  // bare calls: a browser's setTimeout throws "Illegal invocation" when called as timers.setTimeout(...)
  const { setTimeout, clearTimeout } = timers;
  let rows: Readonly<Record<string, Tm>> = {};
  let stamps: Readonly<Record<string, number>> = {}; // receipt time per vehicle, for the age clock (D-UI-05)
  let names: string[] = []; // cached so useSyncExternalStore sees a stable reference
  let alarms: readonly AlarmView[] = [];
  let commands: readonly CommandView[] = [];
  let events: readonly EventView[] = [];
  let alarmT: Readonly<Record<string, number>> = {}; // newest transition time per alarm id, cleared ones included
  let seen: ReadonlySet<string> = new Set(); // event keys seen, so a replay is not shown twice
  let snapshotSeen = false;
  let link: Link = { connected: false, broker: "up", processing: {} };
  let silence: ReturnType<typeof setTimeout> | undefined;
  const subs = new Set<() => void>();
  const notify = () => subs.forEach((fn) => fn());
  const set = (next: Readonly<Record<string, Tm>>) => {
    rows = next;
    names = Object.keys(next).sort();
    notify();
  };
  const setLink = (patch: Partial<Link>) => {
    link = { ...link, ...patch };
    notify();
  };
  const trim = (list: readonly EventView[]) => list.slice(0, EVENTS_KEPT);
  return {
    applySnapshot(s: Snapshot): void {
      const now = clock();
      stamps = Object.fromEntries(Object.keys(s.vehicles).map((v) => [v, now]));
      alarms = (s.alarms ?? []).filter((a) => a.state !== "CLEARED");
      alarmT = Object.fromEntries((s.alarms ?? []).map((a) => [a.alarm_id, ms(a.last.t)]));
      commands = [...(s.commands ?? [])];
      events = trim(s.events ?? []);
      seen = new Set(events.map((e) => e.key));
      snapshotSeen = true;
      set({ ...s.vehicles });
    },
    applyTm(tm: Tm): boolean {
      const shown = rows[tm.vehicle];
      // string compare is valid: the server always sends fixed-width YYYY-MM-DDTHH:MM:SS.ffffffZ
      if (shown && tm.t_onboard <= shown.t_onboard) return false;
      stamps = { ...stamps, [tm.vehicle]: clock() };
      set({ ...rows, [tm.vehicle]: tm });
      return true;
    },
    /** An upsert older than what is held is dropped: a CLEARED alarm must not flash back to UNACK (reconnect replay). */
    applyAlarm(a: AlarmView): void {
      const t = ms(a.last.t);
      if (t < (alarmT[a.alarm_id] ?? -Infinity)) return;
      alarmT = { ...alarmT, [a.alarm_id]: t };
      const rest = alarms.filter((x) => x.alarm_id !== a.alarm_id);
      alarms = a.state === "CLEARED" ? rest : [...rest, a];
      notify();
    },
    applyCmd(c: CommandView): void {
      const held = commands.find((x) => x.cmd_id === c.cmd_id);
      if (held && cmdTime(c) < cmdTime(held)) return;
      commands = commands.some((x) => x.cmd_id === c.cmd_id)
        ? commands.map((x) => (x.cmd_id === c.cmd_id ? c : x))
        : [c, ...commands];
      notify();
    },
    applyLink(e: EventView): void {
      if (seen.has(e.key)) return;
      seen = new Set([...seen, e.key]);
      events = trim([e, ...events]);
      notify();
    },
    setGround: (b: "up" | "down"): void => setLink({ broker: b }),
    setProcessing: (p: Record<string, boolean>): void => setLink({ processing: { ...p } }),
    /** Any message proves the link. One-shot timer, re-armed here: a hidden tab throttles intervals (P-pf18). */
    noteMessage(): void {
      if (silence !== undefined) clearTimeout(silence);
      silence = setTimeout(() => {
        silence = undefined;
        setLink({ connected: false });
      }, SILENCE_MS);
      if (!link.connected) setLink({ connected: true });
    },
    noteError(): void {
      if (link.connected) setLink({ connected: false });
    },
    /** Cancel the silence timer (console unmount). */
    stop(): void {
      if (silence !== undefined) clearTimeout(silence);
      silence = undefined;
    },
    vehicle: (v: string): Tm | undefined => rows[v],
    receivedAt: (v: string): number | undefined => stamps[v],
    vehicles: (): string[] => names,
    alarms: (): readonly AlarmView[] => alarms,
    commands: (): readonly CommandView[] => commands,
    events: (): readonly EventView[] => events,
    link: (): Link => link,
    hasSnapshot: (): boolean => snapshotSeen,
    openAlarm: (v: string, param: string): AlarmView | undefined =>
      alarms.find((a) => a.vehicle === v && a.param === param),
    subscribe(fn: () => void): () => void {
      subs.add(fn);
      return () => void subs.delete(fn);
    },
  };
}

export type Store = ReturnType<typeof createStore>;

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null;
// ponytail: checks only what the store reads (identity, order, values); the server is typed, this stops a wrong shape wiping state
const isTm = (x: unknown): x is Tm =>
  isObj(x) && typeof x.vehicle === "string" && typeof x.t_onboard === "string" &&
  typeof x.seq === "number" && isObj(x.values);
const optArr = (x: unknown) => x === undefined || Array.isArray(x);
const isSnapshot = (x: unknown): x is Snapshot =>
  isObj(x) && isObj(x.vehicles) && Object.values(x.vehicles).every(isTm) &&
  optArr(x.alarms) && optArr(x.commands) && optArr(x.events);
const isAlarm = (x: unknown): x is AlarmView =>
  isObj(x) && typeof x.alarm_id === "string" && typeof x.state === "string" &&
  typeof x.vehicle === "string" && typeof x.param === "string" && typeof x.level === "string" &&
  typeof x.severity === "string" && typeof x.trigger_check === "string" && isObj(x.last) && typeof x.last.t === "string";
const isCmd = (x: unknown): x is CommandView =>
  isObj(x) && typeof x.cmd_id === "string" && typeof x.state === "string";
const isEvent = (x: unknown): x is EventView => isObj(x) && typeof x.key === "string";
const isBool = (x: unknown) => typeof x === "boolean";
const isProcessing = (x: unknown): x is Record<string, boolean> => isObj(x) && Object.values(x).every(isBool);

/** Route a parsed SSE event into the store; wrong shapes are logged and dropped. Any event proves the link. */
export function ingest(store: Store, kind: string, data: unknown): void {
  store.noteMessage();
  const d = isObj(data) ? data : {};
  if (kind === "snapshot" && isSnapshot(data)) store.applySnapshot(data);
  else if (kind === "tm" && isTm(data)) store.applyTm(data);
  else if (kind === "alarm" && isAlarm(d.alarm)) store.applyAlarm(d.alarm);
  else if (kind === "cmd" && isCmd(d.command)) store.applyCmd(d.command);
  else if (kind === "link" && isEvent(d.event)) store.applyLink(d.event);
  else if (kind === "ground" && (d.broker === "up" || d.broker === "down")) store.setGround(d.broker);
  else if (kind === "hb" && (d.processing === undefined || isProcessing(d.processing))) {
    if (d.processing) store.setProcessing(d.processing); // an hb without the map keeps the last one
  } else if (["snapshot", "tm", "alarm", "cmd", "link", "ground", "hb"].includes(kind))
    console.warn(`dropping ${kind} event with wrong shape`, data);
}
