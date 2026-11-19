import { useState, useSyncExternalStore } from "react";
import { eventText } from "../tools/eventText";
import { FilterBar, keep, NO_FILTER, vehiclesOf } from "./listFilter";
import type { Store } from "../state/store";
import { utcClock } from "../tools/time";

/** Ground events, newest first (the store keeps 200). */
export function EventStrip({ store }: { store: Store }) {
  const events = useSyncExternalStore(store.subscribe, store.events, store.events);
  const wired = useSyncExternalStore(store.subscribe, store.hasSnapshot, store.hasSnapshot);
  const [filter, setFilter] = useState(NO_FILTER);
  const lines = events.map((e) => ({ e, line: eventText(e, events) }));
  const shown = lines.filter(({ e, line }) => keep(filter, e.vehicle, `${e.vehicle ?? ""} ${line}`));
  return (
    <section className="events" aria-label="Events">
      <h2>Events</h2>
      <FilterBar what="events" value={filter} onChange={setFilter} vehicles={vehiclesOf(events)} />
      {events.length === 0 ? (
        <p className="empty" role="status">{wired ? "No events" : "Waiting for the first snapshot…"}</p>
      ) : shown.length === 0 ? (
        <p className="empty" role="status">No events match the filter</p>
      ) : (
        <ol className="scroll">
          {shown.map(({ e, line }) => {
            return (
              <li key={e.key} data-kind={e.kind}>
                {e.t_onboard && (
                  <>
                    <time dateTime={e.t_onboard}>{utcClock(e.t_onboard)}</time>{" "}
                  </>
                )}
                {e.vehicle && !line.startsWith(e.vehicle) && (
                  <>
                    <span className="tag">{e.vehicle}</span>{" "}
                  </>
                )}
                {line}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
