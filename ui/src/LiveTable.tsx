import { useEffect, useState, useSyncExternalStore } from "react";
import { ageAt, sortedEntries } from "./format";
import type { Store } from "./store";
import { VehicleCard } from "./VehicleCard";

const AGE_TICK_MS = 100; // D-UI-05 clock tick

function useNow(ms: number): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

function Vehicle({ store, v }: { store: Store; v: string }) {
  const tm = useSyncExternalStore(store.subscribe, () => store.vehicle(v));
  const now = useNow(AGE_TICK_MS);
  if (!tm) return null;
  // ponytail: ids only until GET /api/mission-db lands here (labels, units, limits), states on day 3
  const params = sortedEntries(tm.values).map(([id, value]) => ({ id, value }));
  return (
    <VehicleCard
      vehicle={v}
      mode={tm.mode}
      seq={tm.seq}
      tOnboard={tm.t_onboard}
      ageMs={ageAt(tm.age_ms, store.receivedAt(v), now)}
      params={params}
    />
  );
}

export function LiveTable({ store }: { store: Store }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles);
  return (
    <>
      {vehicles.map((v) => (
        <Vehicle key={v} store={store} v={v} />
      ))}
    </>
  );
}
