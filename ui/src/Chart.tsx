import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { fetchBuckets, type MissionDb } from "./api";
import { bucketMsFor, clampWidth, limitLabels, refreshMs, toSeries, WINDOWS, type Series, type WindowKey } from "./chartSeries";
import { formatValue } from "./format";
import type { Limits } from "./gauge";
import type { Store } from "./store";
import { utcClock } from "./time";

const HEIGHT = 220;
const RIGHT_PAD = 44; // room for the red limit numbers outside the plot

const css = (name: string, fallback: string): string => {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  } catch {
    return fallback;
  }
};
const toPlot = (s: Series): uPlot.AlignedData => [s.x.map((ms) => ms / 1000), s.line, s.lo, s.hi];
const EMPTY: uPlot.AlignedData = [[], [], [], []];

/** One parameter over a window: the line goes through each bucket's first and last value, the band is its min/max
 *  (M4, D-UI-11). Axes are UTC with a Z. One request per range, sized to the plot's pixel width (D-DB-04). */
export function Chart({ vehicle, param, unit, limits }: { vehicle: string; param: string; unit?: string; limits?: Limits }) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const limitsRef = useRef(limits);
  limitsRef.current = limits;
  const [win, setWin] = useState<WindowKey>("2 min");
  const [width, setWidth] = useState(0);
  const [error, setError] = useState("");
  const [data, setData] = useState<uPlot.AlignedData>(EMPTY);

  // the plot's width follows its container
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // one request per range and refresh: min, max, first and last come from the same buckets
  useEffect(() => {
    if (width === 0) return;
    const w = clampWidth(width);
    const ms = WINDOWS[win];
    let live = true;
    const load = async () => {
      const to = new Date();
      try {
        const buckets = await fetchBuckets(vehicle, param, new Date(to.getTime() - ms).toISOString(), to.toISOString(), w);
        if (!live) return;
        setData(toPlot(toSeries(buckets, bucketMsFor(ms, w))));
        setError("");
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : "chart data unavailable");
      }
    };
    void load();
    const id = setInterval(() => void load(), refreshMs(ms));
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [vehicle, param, win, width]);

  // the plot itself: made once, then resized and fed
  useEffect(() => {
    const el = host.current;
    if (!el || width === 0) return;
    if (plot.current) {
      plot.current.setSize({ width, height: HEIGHT });
      return;
    }
    plot.current = new uPlot(options(width, param, unit, limitsRef), data, el);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- created once; data is pushed by the next effect
  }, [width]);
  useEffect(() => plot.current?.setData(data), [data]);
  useEffect(() => () => {
    plot.current?.destroy();
    plot.current = null;
  }, []);

  return (
    <div className="chart">
      <div className="windows" role="group" aria-label="Window">
        {(Object.keys(WINDOWS) as WindowKey[]).map((k) => (
          <button key={k} type="button" aria-pressed={k === win} onClick={() => setWin(k)}>{k}</button>
        ))}
      </div>
      {error && <p role="alert">Chart data unavailable: {error}</p>}
      <div ref={host} className="plot" />
    </div>
  );
}

/** uPlot options. Exported for the tests. */
export function options(width: number, param: string, unit: string | undefined, limits: { current: Limits | undefined }): uPlot.Options {
  const ink = css("--ink", "#0E0E0E");
  const band = css("--zone-base", "#ECE9E5");
  return {
    width,
    height: HEIGHT,
    padding: [8, RIGHT_PAD, 0, 0],
    legend: { show: false },
    cursor: { drag: { x: false, y: false } },
    scales: { x: { time: true } },
    axes: [
      // UTC clock text with the Z (UI-08), from the same helper as every other time on the page
      { values: (_u, splits) => splits.map((s) => utcClock(new Date(s * 1000).toISOString())), stroke: ink },
      { values: (_u, splits) => splits.map((v) => formatValue(v)), stroke: ink, label: unit ? `${param} (${unit})` : param },
    ],
    series: [
      {},
      { label: param, stroke: ink, width: 1.5, points: { show: false }, spanGaps: false },
      { label: "min", stroke: band, width: 0, points: { show: false } },
      { label: "max", stroke: band, width: 0, points: { show: false } },
    ],
    bands: [{ series: [3, 2], fill: band }],
    hooks: { draw: [(u) => drawLimits(u, limits.current)] },
  };
}

/** Limit lines with their numbers: red outside the plot (right margin), yellow inside, none within 14 px of another. */
function drawLimits(u: uPlot, limits: Limits | undefined): void {
  if (!limits) return;
  const dpr = devicePixelRatio || 1;
  const { left, top, width, height } = u.bbox;
  const labels = limitLabels(limits, (v) => u.valToPos(v, "y"))
    .filter((l) => l.at >= 0 && l.at <= height / dpr); // a limit off the plotted range has no place to be drawn
  const ctx = u.ctx;
  ctx.save();
  ctx.font = `${12 * dpr}px sans-serif`;
  ctx.textBaseline = "middle";
  for (const l of labels) {
    const y = top + l.at * dpr;
    const colour = l.tone === "red" ? css("--red", "#B3141B") : css("--yellow-ink", "#7A5C00");
    ctx.strokeStyle = colour;
    ctx.fillStyle = colour;
    ctx.setLineDash([4 * dpr, 4 * dpr]);
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(left + width, y);
    ctx.stroke();
    ctx.textAlign = l.tone === "red" ? "left" : "right";
    ctx.fillText(formatValue(l.value), l.tone === "red" ? left + width + 4 * dpr : left + width - 4 * dpr, y);
  }
  ctx.restore();
}

/** Pick a vehicle and parameter; the limits come from the mission db. */
export function ChartPanel({ store, mission }: { store: Store; mission: MissionDb | null }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  const [v, setV] = useState("");
  const [p, setP] = useState("");
  const vehicle = vehicles.includes(v) ? v : (vehicles[0] ?? "");
  const params = Object.keys(store.vehicle(vehicle)?.values ?? {}).sort();
  const param = params.includes(p) ? p : (params[0] ?? "");
  const check = mission && Object.hasOwn(mission.limits, param) ? mission.limits[param].checks.find((c) => c.type === "limit") : undefined;
  const unit = mission && Object.hasOwn(mission.parameters, param) ? mission.parameters[param].unit : undefined;
  return (
    <section className="chart-panel" aria-label="Chart">
      <h2>Chart</h2>
      {vehicle && param ? (
        <>
          <div className="filters">
            <label>
              Vehicle
              <select value={vehicle} onChange={(e) => setV(e.target.value)}>
                {vehicles.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
            <label>
              Parameter
              <select value={param} onChange={(e) => setP(e.target.value)}>
                {params.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
          </div>
          <Chart key={`${vehicle}/${param}`} vehicle={vehicle} param={param} unit={unit} limits={check?.type === "limit" ? check : undefined} />
        </>
      ) : (
        <p className="empty">Waiting for telemetry…</p>
      )}
    </section>
  );
}
