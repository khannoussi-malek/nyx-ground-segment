import type { CommandView, MissionDb, SendResult } from "./api";
import { utcClock } from "./time";

/** Exact match, no trimming and no case folding: typing the vehicle id is the deliberate act (D-UI-20). */
export const confirmEnabled = (typed: string, vehicle: string, reason: string): boolean =>
  typed === vehicle && reason.trim().length >= 10;

const DEFAULT_RECEIVE_S = 5;
const DEFAULT_VERIFY_S = 15;

function why(c: CommandView, m: MissionDb | null): string {
  const spec = m && Object.hasOwn(m.commands, c.name) ? m.commands[c.name] : undefined;
  switch (c.failure_reason) {
    case "NOT_RECEIVED": return `not received in ${spec?.receive_timeout_s ?? DEFAULT_RECEIVE_S} s`;
    case "NOT_VERIFIED": return `no verification in ${spec?.verify_timeout_s ?? DEFAULT_VERIFY_S} s`;
    case "ACK_INVALID": return "vehicle answer failed its interface check";
    case "REJECTED": return "rejected by the vehicle";
    case "SEND_ERROR": return "could not be sent";
    default: return "failed";
  }
}

/** What the operator may do after a failed command, from the computed outcome (D-CMD-02, Q3). null = no outcome. */
export function outcomeText(c: CommandView, m: MissionDb | null = null): string | null {
  const head = `FAILED (${why(c, m)})`;
  switch (c.outcome) {
    case "EXECUTED_LATE":
      return `${head} – vehicle executed it at ${c.late_verified_at ? utcClock(c.late_verified_at) : "an unknown time"} – do NOT resend`;
    case "NOT_EXECUTED": return `${head} – vehicle did not execute it – safe to resend after checking why`;
    case "RECEIVED_LATE": return `${head} – vehicle accepted it late, not yet seen executing – wait`;
    case "UNKNOWN":
      return `${head} – outcome unknown: check mode, last_cmd_id and the affected parameters; never blind-resend a hazardous command; tell the test conductor`;
    case "NOT_SENT": return "not sent (broker unavailable) – safe to retry";
    default: return null;
  }
}

/** The line shown for a refused command. */
export function rejectedText(r: Extract<SendResult, { kind: "rejected" }>): string {
  return r.error === "DUPLICATE_COMMAND" && r.by !== undefined && r.secondsAgo !== undefined
    ? `${r.by} sent the same command ${r.secondsAgo} s ago`
    : r.message;
}
