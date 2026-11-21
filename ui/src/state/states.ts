import type { AlarmView } from "./store";
import type { Level, ParamState, Side } from "../tools/status";

export const STALE_MS = 2000;

const SIDES: readonly string[] = ["LOW", "HIGH", "RISING", "FALLING"];

/** The state of one value, from what the engine sent: nothing here derives a level from limits.
 *  An unknown age is STALE, never OK: old data must look old (P-pf22). RTN_UNACK and no alarm are NOMINAL; the "was ..." text of a returned alarm is the alarm list's job. */
export function paramState(
  value: number | null,
  ageMs: number | null | undefined,
  frozen: boolean,
  alarm: AlarmView | undefined,
  staleMs = STALE_MS, // the vehicle's tm.stale_ms; never under the 2 s floor
): ParamState {
  const acq = value === null ? "NO_DATA" : frozen ? "FROZEN" : ageMs == null || ageMs >= Math.max(STALE_MS, staleMs) ? "STALE" : "OK";
  if (!alarm || (alarm.state !== "UNACK" && alarm.state !== "ACKED")) return { acq, level: "NOMINAL" };
  // "RED_HIGH" -> RED, HIGH; an unreadable text falls back to the alarm's own severity, never to a guess
  const [sev, side] = alarm.level.split(" · ")[0].split("_"); // "RED_HIGH · SAFE limits": the set suffix is not the side
  const level: Level = sev === "RED" || sev === "YELLOW" ? sev : alarm.severity;
  return {
    acq,
    level,
    ...(side !== undefined && SIDES.includes(side) && { side: side as Side }),
    check_kind: alarm.trigger_check,
  };
}
