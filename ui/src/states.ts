import type { components } from "./api/schema";
import type { Level, ParamState, Side } from "./status";

export type AlarmView = components["schemas"]["AlarmView"];
export const STALE_MS = 2000;

const SIDES: readonly string[] = ["LOW", "HIGH", "RISING", "FALLING"];

/** The state of one value, from what the engine sent: nothing here derives a level from limits.
 *  RTN_UNACK and no alarm are NOMINAL; the "was ..." text of a returned alarm is the alarm list's job. */
export function paramState(
  value: number | null,
  ageMs: number | null,
  frozen: boolean,
  alarm: AlarmView | undefined,
): ParamState {
  const acq = value === null ? "NO_DATA" : frozen ? "FROZEN" : ageMs !== null && ageMs >= STALE_MS ? "STALE" : "OK";
  if (!alarm || (alarm.state !== "UNACK" && alarm.state !== "ACKED")) return { acq, level: "NOMINAL" };
  // "RED_HIGH" -> RED, HIGH; an unreadable text falls back to the alarm's own severity, never to a guess
  const [sev, side] = alarm.level.split("_");
  const level: Level = sev === "RED" || sev === "YELLOW" ? sev : alarm.severity;
  return {
    acq,
    level,
    ...(side !== undefined && SIDES.includes(side) && { side: side as Side }),
    check_kind: alarm.trigger_check,
  };
}
