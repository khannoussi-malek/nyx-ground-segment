import { formatValue } from "./format";
import { gaugeOf, type Gauge, type Limits } from "./gauge";
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
/** G26: only a live LIMIT alarm names a position zone; a rate alarm has a side too but no zone */
const fillsZone = (s: ParamState | undefined) => s?.acq === "OK" && s.check_kind === "limit" && s.side !== undefined;
const limitText = (v: number) => String(v).replaceAll("-", "\u2212");

/** Grey zones (normal, yellow range, red range), a pointer at the value and the outer limits
 *  printed under the track. Decorative for screen readers: the value cell carries the number. */
function GaugeBar({ g, live }: { g: Gauge; live: ParamState | null }) {
  // the engine's live, sided state fills its zone; display only, nothing derived from limits (G20)
  const isActive = (z: Gauge["zones"][number]) =>
    live !== null && z.side === live.side && z.kind === live.level.toLowerCase();
  return (
    <div className="gauge" aria-hidden="true">
      <div className="gauge-track">
        {g.zones.map((z) => (
          <span
            key={z.from}
            className="zone"
            data-kind={z.kind}
            data-side={z.side}
            data-active={isActive(z) || undefined}
            style={{ left: pct(z.from), width: pct(z.to - z.from) }}
          />
        ))}
      </div>
      {g.pos !== null && (
        <span className="gauge-pointer" data-clamped={g.clamped || undefined} style={{ left: pct(g.pos) }} />
      )}
      {g.marks.map((m) => (
        <span key={m.at} className="gauge-mark" style={{ left: pct(m.at) }}>
          {limitText(m.value)}
        </span>
      ))}
    </div>
  );
}

export function ParamRow({ vehicle, seq, tOnboard, p }: { vehicle: string; seq: number; tOnboard: string; p: ParamView }) {
  const state = p.state ?? NOMINAL_OK;
  const acq = acqMark(state.acq);
  const badge = limitBadge(state);
  const g = p.limits ? gaugeOf(p.limits, p.value) : null;
  // one tbody per parameter, one line: name, value, gauge (fits the one-screen console); the state tints the row
  return (
    <tbody data-level={p.state?.level} data-acq={p.state?.acq}>
      <tr>
        <th scope="row" title={p.id}>
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
        <td className="value" data-vehicle={vehicle} data-param={p.id} data-seq={seq} data-t-onboard={tOnboard}>
          <span className="num">{formatValue(p.value)}</span>
          {p.unit && <span className="unit">{p.unit}</span>}
        </td>
        {g ? (
          <td className="gauge-cell">
            <GaugeBar g={g} live={fillsZone(p.state) ? p.state! : null} />
          </td>
        ) : (
          <td />
        )}
      </tr>
    </tbody>
  );
}
