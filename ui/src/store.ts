import type { components } from "./api/schema";

export type Tm = components["schemas"]["Tm"];
export type Snapshot = components["schemas"]["Snapshot"];

export function createStore() {
  let rows: Readonly<Record<string, Tm>> = {};
  let names: string[] = []; // cached so useSyncExternalStore sees a stable reference
  const subs = new Set<() => void>();
  const set = (next: Readonly<Record<string, Tm>>) => {
    rows = next;
    names = Object.keys(next).sort();
    subs.forEach((fn) => fn());
  };
  return {
    applySnapshot: (s: Snapshot): void => set({ ...s.vehicles }),
    applyTm(tm: Tm): boolean {
      const shown = rows[tm.vehicle];
      // string compare is valid: the server always sends fixed-width YYYY-MM-DDTHH:MM:SS.ffffffZ
      if (shown && tm.t_onboard <= shown.t_onboard) return false;
      set({ ...rows, [tm.vehicle]: tm });
      return true;
    },
    vehicle: (v: string): Tm | undefined => rows[v],
    vehicles: (): string[] => names,
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
const isSnapshot = (x: unknown): x is Snapshot =>
  isObj(x) && isObj(x.vehicles) && Object.values(x.vehicles).every(isTm);

/** Route a parsed SSE event into the store; wrong shapes are logged and dropped. */
export function ingest(store: Store, kind: string, data: unknown): void {
  if (kind === "snapshot" && isSnapshot(data)) store.applySnapshot(data);
  else if (kind === "tm" && isTm(data)) store.applyTm(data);
  else if (kind === "snapshot" || kind === "tm") console.warn(`dropping ${kind} event with wrong shape`, data);
}
