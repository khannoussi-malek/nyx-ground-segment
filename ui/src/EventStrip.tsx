import { useSyncExternalStore } from "react";
import { eventText } from "./eventText";
import type { Store } from "./store";
import { utcClock } from "./time";

/** Ground events, newest first (the store keeps 200). */
export function EventStrip({ store }: { store: Store }) {
  const events = useSyncExternalStore(store.subscribe, store.events, store.events);
  const wired = useSyncExternalStore(store.subscribe, store.hasSnapshot, store.hasSnapshot);
  return (
    <section className="events" aria-label="Events">
      <h2>Events</h2>
      {events.length === 0 ? (
        <p className="empty" role="status">{wired ? "No events" : "Waiting for the first snapshot…"}</p>
      ) : (
        <ol className="scroll">
          {events.map((e) => {
            const line = eventText(e, events);
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
