import { useEffect, useState, useSyncExternalStore } from "react";
import type { MissionDb } from "./api";
import { conditionFor } from "./banners";
import { ageAt } from "./format";
import { paramViews } from "./params";
import type { Probe } from "./probe";
import { paramState } from "./states";
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

function Vehicle({ store, v, mission, clock, probe }: { store: Store; v: string; mission: MissionDb | null; clock: () => number; probe: Probe | undefined }) {
  const tm = useSyncExternalStore(store.subscribe, () => store.vehicle(v), () => store.vehicle(v));
  const link = useSyncExternalStore(store.subscribe, store.link, store.link);
  useSyncExternalStore(store.subscribe, store.alarms, store.alarms); // re-render on alarm changes
  const wired = useSyncExternalStore(store.subscribe, store.hasSnapshot, store.hasSnapshot);
  useTick(AGE_TICK_MS);
  if (!tm) return null;
  const ageMs = ageAt(tm.age_ms, store.receivedAt(v), clock());
  const condition = conditionFor({ ...link, processing: link.processing[v], ageMs, frozen: tm.frozen });
  // FROZEN is counted from the rows; only the vehicle-level conditions replace the summary
  const banner = condition === "FROZEN" || condition === "STALE" ? undefined : (condition ?? undefined);
  // no row state before the first snapshot: the gauge fill and the summary wait for the engine (G26)
  const params = paramViews(tm.values, mission).map((p) =>
    wired ? { ...p, state: paramState(p.value, ageMs, tm.frozen, store.openAlarm(v, p.id)) } : p,
  );
  return (
    <VehicleCard
      vehicle={v}
      mode={tm.mode}
      seq={tm.seq}
      tOnboard={tm.t_onboard}
      ageMs={ageMs}
      params={params}
      banner={banner}
      statesWired={wired}
      onCommit={probe && (() => probe.painted(v, tm.seq))}
    />
  );
}

/** `clock` must be the store's clock: receipt stamps and the age clock share one time base. */
export function LiveTable({ store, mission, clock = () => performance.now(), probe }: { store: Store; mission: MissionDb | null; clock?: () => number; probe?: Probe }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  return (
    <>
      {vehicles.map((v) => (
        <Vehicle key={v} store={store} v={v} mission={mission} clock={clock} probe={probe} />
      ))}
    </>
  );
}
