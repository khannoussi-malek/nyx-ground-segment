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

/** Route a parsed SSE event into the store; hello and hb never reach here. */
export function ingest(store: Store, kind: string, data: unknown): void {
  if (kind === "snapshot") store.applySnapshot(data as Snapshot);
  else if (kind === "tm") store.applyTm(data as Tm);
}
