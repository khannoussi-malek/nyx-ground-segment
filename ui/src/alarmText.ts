import type { MissionDb } from "./api";
import { formatValue } from "./format";
import type { AlarmView } from "./store";

const minus = (n: number) => String(n).replaceAll("-", "−");

/** What the alarm says it saw (D-UI-09): level words, the value with its unit and the bound it crossed.
 *  Taken from the AlarmView alone (+ the mission db for unit and limit): the level of a returned alarm is its last
 *  non-NOMINAL one (P-pf20) and the value survives an ACK (P-pf4). */
export function alarmText(a: AlarmView, m: MissionDb | null): string {
  const [sev = "", side = ""] = a.level.split("_");
  if (a.state === "RTN_UNACK") return `RETURNED · was ${a.level.replace("_", " ")}`;
  const unit = m && Object.hasOwn(m.parameters, a.param) ? m.parameters[a.param].unit : "";
  const checks = m && Object.hasOwn(m.limits, a.param) ? m.limits[a.param].checks : [];
  const num = (suffix: string) => (a.value === null ? formatValue(null) : `${formatValue(a.value)}${unit ? ` ${unit}${suffix}` : ""}`);
  if (a.trigger_check === "roc") {
    const roc = checks.find((c) => c.type === "roc" && c.severity === sev) ?? checks.find((c) => c.type === "roc");
    const lim = roc?.type === "roc" ? ` (limit ${minus(roc.limit_per_s)})` : "";
    return `${sev} · RATE ${side} ${num("/s")}${lim}`;
  }
  const limit = checks.find((c) => c.type === "limit");
  const bound = limit?.type === "limit" ? limit[`${sev.toLowerCase()}_${side.toLowerCase()}` as "red_high"] : undefined;
  return `${a.level.replace("_", " ")} · ${num("")}${bound == null ? "" : ` (limit ${minus(bound)})`}`;
}

/** "limits 3f9a1c2e · set 5d07b8a1": which limits and which set were in force. */
export const hashText = (a: AlarmView): string =>
  `limits ${a.limits_sha256.slice(0, 8)} · set ${a.set_sha256.slice(0, 8)}`;
