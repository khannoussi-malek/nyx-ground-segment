import { utcClock } from "./time";
import type { EventView } from "./store";

const text = (e: EventView, k: string): string | null => {
  const v = e.details[k];
  return typeof v === "string" ? v : null;
};
const num = (e: EventView, k: string): number | null => {
  const v = e.details[k];
  return typeof v === "number" ? v : null;
};
const sha = (e: EventView, k: string): string => (text(e, k) ?? "?").slice(0, 8);
const clock = (t: string | null): string => (t ? utcClock(t) : "?");
const frames = (n: number | null): string =>
  n === null ? "unknown number of frames lost" : `${n} ${n === 1 ? "frame" : "frames"} lost`;

/** One operator line per ground event (D-UI-19). `all` is the strip's list: an AOS looks up the LOS it ends. */
export function eventText(e: EventView, all: readonly EventView[]): string {
  const v = e.vehicle ?? "";
  switch (e.kind) {
    case "LOS":
      return `${v} LOS since ${clock(e.t_onboard)}`;
    case "AOS": {
      const los = all.find((x) => x.kind === "LOS" && x.key === e.key.replace(/^AOS\//, "LOS/")); // same key suffix
      const span = los ? `${clock(los.t_onboard)}–${clock(e.t_onboard)}` : `until ${clock(e.t_onboard)}`;
      return `${v} AOS – ${frames(num(e, "lost_frames"))} ${span}`;
    }
    case "GAP": {
      const from = num(e, "from_seq");
      return `${v} GAP – ${frames(num(e, "lost"))} ${from !== null && from > 0 ? `after seq ${from - 1}` : "at the start of the sequence"}`;
    }
    case "SEQ_JUMP":
      return `${v} SEQ JUMP – sequence jumped by ${num(e, "lost") ?? "?"} after seq ${(num(e, "from_seq") ?? 1) - 1}, ${num(e, "lost_by_time") ?? "?"} expected by time`;
    case "SEQ_RESET":
      return `${v} SEQ RESET – gap of unknown size`;
    case "SEQ_ANOMALY":
      return `${v} SEQ ANOMALY – sequence restarted at an unexpected number`;
    case "FROZEN":
      return `${v} FROZEN since ${clock(text(e, "t_from") ?? e.t_onboard)}`;
    case "LIMITS_APPLIED":
      return `mission db applied: limits ${sha(e, "limits_sha256")} · commands ${sha(e, "commands_sha256")} · parameters ${sha(e, "parameters_sha256")}`;
    case "LIMITS_REJECTED": {
      const base = `mission db REJECTED – ${text(e, "file") ?? "?"}: ${text(e, "error") ?? "?"}`;
      // the API's own check refused it: the worker may have applied it, the API keeps its old commands (P-pf5)
      return text(e, "by") === "api" ? `${base} – commands not updated until API restart` : base;
    }
    case "LATE_VERIFICATION":
      return `${v} late verification of command ${e.key.split("/")[1] ?? "?"}`.trim();
    case "INTERFACE_MISMATCH": {
      const [, stream = "?", id = "?"] = e.key.split("/");
      return `interface mismatch on ${stream} (${id})`;
    }
    case "POISON":
      return `${v} POISON – entry ${text(e, "entry_id") ?? "?"} of ${text(e, "stream") ?? "?"} dead-lettered after ${num(e, "deliveries") ?? "?"} deliveries`;
    case "ENGINE_STATE_RESET":
      return `${v} ENGINE STATE RESET – ${text(e, "reason") ?? "reason unknown"}`;
    default:
      return `${v} ${e.kind}`.trim();
  }
}
