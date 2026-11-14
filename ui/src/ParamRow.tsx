import type { CSSProperties } from "react";
import { formatValue } from "./format";
import { gaugeOf, type Limits } from "./gauge";
import { Glyph } from "./Glyph";
import { acqMark, limitBadge, NOMINAL_OK, type ParamState } from "./status";

export type ParamView = {
  id: string;
  value: number | null;
  label?: string; // mission db description
  unit?: string;
  limits?: Limits;
  state?: ParamState; // from the alarm engine via SSE (day 3); absent = not wired yet
};

const pct = (f: number) => `${(f * 100).toFixed(2)}%`;

function GaugeBar({ limits, value }: { limits: Limits; value: number | null }) {
  const g = gaugeOf(limits, value);
  if (!g) return null;
  const normal: CSSProperties = { left: pct(g.normal[0]), width: pct(g.normal[1] - g.normal[0]) };
  return (
    <span className="gauge" aria-hidden="true">
      <span className="gauge-normal" style={normal} />
      {g.ticks.map((t) => (
        <span key={`${t.level}${t.at}`} className={`gauge-tick ${t.level.toLowerCase()}`} style={{ left: pct(t.at) }} />
      ))}
      {g.pos !== null && (
        <span className="gauge-marker" data-clamped={g.clamped || undefined} style={{ left: pct(g.pos) }} />
      )}
    </span>
  );
}

export function ParamRow({ vehicle, seq, tOnboard, p }: { vehicle: string; seq: number; tOnboard: string; p: ParamView }) {
  const state = p.state ?? NOMINAL_OK;
  const acq = acqMark(state.acq);
  const badge = limitBadge(state);
  return (
    <tr data-level={p.state?.level} data-acq={p.state?.acq}>
      <th scope="row">
        <span className="label">{p.label || p.id}</span>
        {p.label && <span className="pid">{p.id}</span>}
        {(badge || acq) && (
          <span className="marks">
            {badge && (
              <span className="badge" data-tone={badge.tone}>
                <Glyph kind={state.level === "RED" ? "red" : "yellow"} />
                {badge.text}
              </span>
            )}
            {acq && (
              <span className="acq">
                <Glyph kind={acq.glyph} />
                {acq.text}
              </span>
            )}
          </span>
        )}
      </th>
      <td className="gauge-cell">{p.limits && <GaugeBar limits={p.limits} value={p.value} />}</td>
      <td className="value" data-vehicle={vehicle} data-param={p.id} data-seq={seq} data-t-onboard={tOnboard}>
        <span className="num">{formatValue(p.value)}</span>
        {p.unit && <span className="unit">{p.unit}</span>}
      </td>
    </tr>
  );
}
