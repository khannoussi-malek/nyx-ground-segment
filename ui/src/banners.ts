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
}): Condition {
  if (!c.connected) return "NO LIVE CONNECTION";
  if (c.broker === "down") return "GROUND PROCESSING DOWN – BROKER";
  if (c.processing === false) return "GROUND PROCESSING DOWN";
  if (c.ageMs !== null && c.ageMs >= STALE_MS) return "LOSS OF SIGNAL";
  return c.frozen ? "FROZEN" : null;
}
