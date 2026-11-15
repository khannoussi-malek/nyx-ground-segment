import type { CommandView, MissionDb } from "./api";
import { outcomeText } from "./commandText";
import { utcClock } from "./time";

const STEPS = [
  ["QUEUED", "queued_at"],
  ["SENT", "sent_at"],
  ["RECEIVED", "received_at"],
  ["VERIFIED", "verified_at"],
] as const;

/** One command's states with their UTC times, "—" for one not reached (D-UI-10); FAILED with its reason and outcome text. */
export function Timeline({ c, mission }: { c: CommandView; mission: MissionDb | null }) {
  const outcome = outcomeText(c, mission);
  return (
    <div className="timeline">
      <ol>
        {STEPS.map(([label, key]) => (
          <li key={label} data-reached={c[key] !== null || undefined}>
            {label} <time dateTime={c[key] ?? undefined}>{c[key] ? utcClock(c[key]) : "\u2014"}</time>
          </li>
        ))}
        {c.state === "FAILED" && (
          <li data-failed>
            FAILED {c.failed_at && <time dateTime={c.failed_at}>{utcClock(c.failed_at)}</time>} {c.failure_reason}
          </li>
        )}
      </ol>
      {outcome && <p className="outcome" role="status">{outcome}</p>}
    </div>
  );
}
