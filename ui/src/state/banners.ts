import { ageAt } from "../tools/format";
import { STALE_MS } from "./states";
import type { Banner } from "../tools/status";
import type { Store } from "./store";

export type Condition = Banner | "FROZEN" | "STALE" | null;

/** Vehicle condition, highest first (D-UI-04/15). broker down shows on every vehicle; LOS only when the
 *  owner is healthy and the broker up, and only 2 s after both are back, unless the server's LOS for the
 *  vehicle is still open (healthyMs null). STALE is per row and never reaches here: it shares LOS's 2 s. */
export function conditionFor(c: {
  connected: boolean;
  broker: "up" | "down";
  processing: boolean | undefined;
  ageMs: number | null;
  frozen: boolean;
  /** ms since the broker and this vehicle's owner were last both back (store.healthySince); null = no grace:
   *  never down, or the vehicle's LOS was already open before the outage */
  healthyMs?: number | null;
}): Condition {
  if (!c.connected) return "NO LIVE CONNECTION";
  if (c.broker === "down") return "GROUND PROCESSING DOWN – BROKER";
  if (c.processing === false) return "GROUND PROCESSING DOWN";
  // a broker or owner outage is not loss of signal: the 2 s count again from when monitoring came back, as the
  // server's own LOS clock restarts (D-UI-04/15); the rows are still STALE meanwhile
  const healthy = c.healthyMs == null || c.healthyMs >= STALE_MS;
  if (c.ageMs !== null && c.ageMs >= STALE_MS && healthy) return "LOSS OF SIGNAL";
  return c.frozen ? "FROZEN" : null;
}

/** The condition the live table shows for `v` at `now`: the store's link, age clock and outage times together. */
export function vehicleCondition(store: Store, v: string, now: number): Condition {
  const tm = store.vehicle(v);
  const link = store.link();
  const since = store.healthySince(v);
  const ageMs = ageAt(tm?.age_ms, store.receivedAt(v), now);
  return conditionFor({ ...link, processing: link.processing[v], ageMs, frozen: tm?.frozen ?? false, healthyMs: since === null ? null : now - since });
}

/** D-UI-25: the text for a LOSS OF SIGNAL that frames rejected to the DLQ preceded (condition, tone and key unchanged). */
export function rejectedText(count: number, reason: string): string {
  return `FRAMES REJECTED – ${count} ${count === 1 ? "frame" : "frames"}, ${reason} – check tm:dlq`;
}

/** The summary text the live table shows for a vehicle in `condition`: rejectedText for a LOS the server tagged. */
export function bannerText(store: Store, v: string, condition: Condition): string | undefined {
  if (condition !== "LOSS OF SIGNAL") return undefined;
  const e = store.lastLink(v);
  const r = e?.kind === "LOS" ? (e.details as { rejected?: { count: number; reason: string } }).rejected : undefined;
  return r ? rejectedText(r.count, r.reason) : undefined;
}
