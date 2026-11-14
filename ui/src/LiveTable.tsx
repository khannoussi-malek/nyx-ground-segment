import { useSyncExternalStore } from "react";
import type { Store } from "./store";

function VehicleTable({ store, v }: { store: Store; v: string }) {
  const tm = useSyncExternalStore(store.subscribe, () => store.vehicle(v));
  if (!tm) return null;
  return (
    <table>
      <caption>
        {v} {tm.mode} seq {tm.seq} {tm.t_onboard}
      </caption>
      <tbody>
        {Object.entries(tm.values)
          .sort(([a], [b]) => (a < b ? -1 : 1))
          .map(([p, value]) => (
            <tr key={p}>
              <th scope="row">{p}</th>
              <td data-vehicle={v} data-param={p} data-seq={tm.seq} data-t-onboard={tm.t_onboard}>
                {value === null ? "—" : value.toFixed(2)}
              </td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}

export function LiveTable({ store }: { store: Store }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles);
  return (
    <>
      {vehicles.map((v) => (
        <VehicleTable key={v} store={store} v={v} />
      ))}
    </>
  );
}
