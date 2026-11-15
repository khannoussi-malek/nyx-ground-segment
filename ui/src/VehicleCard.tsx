import { useEffect } from "react";
import { formatAge } from "./format";
import { Glyph } from "./Glyph";
import { ParamRow, type ParamView } from "./ParamRow";
import { NOMINAL_OK, vehicleSummary, type Banner } from "./status";

export type VehicleCardProps = {
  vehicle: string;
  mode: string;
  seq: number;
  tOnboard: string;
  ageMs: number | null; // ageAt(...) from the parent's clock tick
  params: readonly ParamView[];
  banner?: Banner;
  /** true once every row's state comes from the engine (day 3); until then no summary is shown,
   *  because "NOMINAL" without alarm data could hide an open RED */
  statesWired?: boolean;
  /** called after each new frame (seq) was committed: the latency probe waits for the paint from here (D-UI-14) */
  onCommit?: () => void;
};

const SUMMARY_GLYPH = { red: "red", yellow: "yellow", muted: "stale", down: "nodata" } as const;

export function VehicleCard(c: VehicleCardProps) {
  const { onCommit, seq } = c;
  // eslint-disable-next-line react-hooks/exhaustive-deps -- once per frame, not per new callback identity
  useEffect(() => onCommit?.(), [seq]);
  const summary =
    c.statesWired || c.banner ? vehicleSummary(c.params.map((p) => p.state ?? NOMINAL_OK), c.banner) : null;
  return (
    <table>
      <caption>
        <span className="head">
          <span className="vehicle">{c.vehicle}</span>
          {summary && (
            <span className="summary" data-tone={summary.tone} role="status">
              {summary.tone !== "nominal" && <Glyph kind={SUMMARY_GLYPH[summary.tone]} />}
              {summary.text}
            </span>
          )}
        </span>
        <span className="meta">
          {c.mode} · seq {c.seq} · updated{" "}
          <time dateTime={c.tOnboard} title={c.tOnboard}>
            {c.ageMs === null ? "—" : `${formatAge(c.ageMs)} ago`}
          </time>
        </span>
      </caption>
      {c.params.map((p) => (
        <ParamRow key={p.id} vehicle={c.vehicle} seq={c.seq} tOnboard={c.tOnboard} p={p} />
      ))}
    </table>
  );
}
