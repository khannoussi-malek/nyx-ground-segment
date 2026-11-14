import { useEffect, useState, useSyncExternalStore } from "react";
import type { MissionDb } from "./api";
import { ageAt } from "./format";
import { paramViews } from "./params";
import type { Store } from "./store";
import { VehicleCard } from "./VehicleCard";

const AGE_TICK_MS = 100; // D-UI-05 clock tick

/** Re-render every `ms`; the age itself reads the clock at render time. */
function useTick(ms: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}

function Vehicle({ store, v, mission }: { store: Store; v: string; mission: MissionDb | null }) {
  const tm = useSyncExternalStore(store.subscribe, () => store.vehicle(v), () => store.vehicle(v));
  useTick(AGE_TICK_MS);
  if (!tm) return null;
  // states (ParamView.state, statesWired) are wired on day 3 from SSE (G20)
  const params = paramViews(tm.values, mission);
  return (
    <VehicleCard
      vehicle={v}
      mode={tm.mode}
      seq={tm.seq}
      tOnboard={tm.t_onboard}
      ageMs={ageAt(tm.age_ms, store.receivedAt(v), performance.now())}
      params={params}
    />
  );
}

export function LiveTable({ store, mission }: { store: Store; mission: MissionDb | null }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  return (
    <>
      {vehicles.map((v) => (
        <Vehicle key={v} store={store} v={v} mission={mission} />
      ))}
    </>
  );
}
