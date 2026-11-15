import { STALE_MS } from "./states";
import type { Banner } from "./status";

export type Condition = Banner | "FROZEN" | "STALE" | null;

/** Vehicle condition, highest first (D-UI-04/15). broker down shows on every vehicle; LOS only when the
 *  owner is healthy and the broker up. STALE is per row and never reaches here: it shares LOS's 2 s. */
export function conditionFor(c: {
  connected: boolean;
  broker: "up" | "down";
  processing: boolean | undefined;
  ageMs: number | null;
  frozen: boolean;
  /** ms since the broker and this vehicle's owner were last both back (store.healthySince); null = never down */
  healthyMs?: number | null;
}): Condition {
  if (!c.connected) return "NO LIVE CONNECTION";
  if (c.broker === "down") return "GROUND PROCESSING DOWN – BROKER";
  if (c.processing === false) return "GROUND PROCESSING DOWN";
  // a broker or owner outage is not loss of signal: the 2 s count from when monitoring came back, as the
  // server's own LOS does (D-UI-04/15); the rows are still STALE meanwhile
  const healthy = c.healthyMs == null || c.healthyMs >= STALE_MS;
  if (c.ageMs !== null && c.ageMs >= STALE_MS && healthy) return "LOSS OF SIGNAL";
  return c.frozen ? "FROZEN" : null;
}
