import { useEffect, useState, useSyncExternalStore } from "react";
import type { MissionDb } from "./api";
import { ageAt } from "./format";
import { paramViews } from "./params";
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

function Vehicle({ store, v, mission }: { store: Store; v: string; mission: MissionDb | null }) {
  const tm = useSyncExternalStore(store.subscribe, () => store.vehicle(v));
  const now = useNow(AGE_TICK_MS);
  if (!tm) return null;
  // states (ParamView.state, statesWired) are wired on day 3 from SSE (G20)
  const params = paramViews(tm.values, mission);
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

export function LiveTable({ store, mission }: { store: Store; mission: MissionDb | null }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles);
  return (
    <>
      {vehicles.map((v) => (
        <Vehicle key={v} store={store} v={v} mission={mission} />
      ))}
    </>
  );
}
