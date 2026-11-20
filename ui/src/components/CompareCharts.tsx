import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { Compare } from "../server/api";
import { alignParam, COLOURS, DASHES, tPlus } from "../tools/compare";
import { formatValue } from "../tools/format";
import { utcIso } from "../tools/time";

const HEIGHT = 240;
const BAND_ALPHA = "26"; // hex alpha: the min/max band is 15 % of the line colour

/** One line + min/max band per session (series 3i+1, 3i+2, 3i+3), x in seconds since each start, labelled T+hh:mm:ss.
 *  Exported for the tests. */
export function compareOptions(c: Compare, param: string, unit: string | undefined, width: number): uPlot.Options {
  const muted = "#57534E";
  const grid = "#D8D3CD";
  const series: uPlot.Series[] = [{}];
  const bands: uPlot.Band[] = [];
  c.sessions.forEach((s, i) => {
    series.push(
      { label: s.name, stroke: COLOURS[i], width: 1.5, dash: DASHES[i], points: { show: false }, spanGaps: true },
      { label: `${s.name} min`, stroke: COLOURS[i], width: 0, points: { show: false } },
      { label: `${s.name} max`, stroke: COLOURS[i], width: 0, points: { show: false } },
    );
    bands.push({ series: [3 * i + 3, 3 * i + 2], fill: `${COLOURS[i]}${BAND_ALPHA}` });
  });
  return {
    width,
    height: HEIGHT,
    legend: { show: false }, // the page's own legend names vehicle and start
    scales: { x: { time: false } },
    axes: [
      { values: (_u, splits) => splits.map(tPlus), stroke: muted, space: 90, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } },
      { values: (_u, splits) => splits.map((v) => formatValue(v)), stroke: muted, label: unit ? `${param} (${unit})` : param, size: 64, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } },
    ],
    series,
    bands,
  };
}

function Plot({ c, param, unit }: { c: Compare; param: string; unit?: string }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const p = alignParam(c, param);
    const data = [p.x, ...c.sessions.flatMap((_, i) => [p.lines[i]!, p.los[i]!, p.his[i]!])] as uPlot.AlignedData;
    const width = el.clientWidth || 800;
    const u = new uPlot(compareOptions(c, param, unit, width), data, el);
    const onResize = () => u.setSize({ width: el.clientWidth || width, height: HEIGHT });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      u.destroy();
    };
  }, [c, param, unit]);
  return <div ref={host} className="compare-plot" data-param={param} />;
}

/** The legend: name, vehicle and absolute start (UTC, Z) beside a swatch with the session's dash and colour. */
export function CompareLegend({ c }: { c: Compare }) {
  return (
    <ul className="compare-legend" aria-label="Sessions">
      {c.sessions.map((s, i) => (
        <li key={s.id} data-session={s.id}>
          <svg width="36" height="10" aria-hidden="true">
            <line x1="0" y1="5" x2="36" y2="5" stroke={COLOURS[i]} strokeWidth="2" strokeDasharray={DASHES[i]!.join(" ") || undefined} />
          </svg>{" "}
          {s.name} · {s.vehicle} · started {utcIso(s.started_at)}
        </li>
      ))}
    </ul>
  );
}

export function CompareCharts({ c, units }: { c: Compare; units: Record<string, string | undefined> }) {
  const params = [...new Set(c.series.map((s) => s.param))];
  return (
    <div className="compare-charts">
      <CompareLegend c={c} />
      {params.map((p) => (
        <section key={p} aria-label={`Compare ${p}`}>
          <h3>{p}{units[p] ? ` (${units[p]})` : ""}</h3>
          <Plot c={c} param={p} unit={units[p]} />
        </section>
      ))}
    </div>
  );
}
