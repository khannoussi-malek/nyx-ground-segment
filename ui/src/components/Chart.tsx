import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { fetchBuckets, type MissionDb } from "../server/api";
import { bucketMsFor, clampWidth, limitLabels, refreshMs, toSeries, WINDOWS, type Series, type WindowKey } from "../tools/chartSeries";
import { formatValue } from "../tools/format";
import { paramMeta, paramViews } from "../tools/params";
import type { Limits } from "../tools/gauge";
import type { Store } from "../state/store";
import { MAX_SPAN, MIN_SPAN, pan, rangeOf, select, zoom, type View } from "../tools/chartView";
import { utcClock } from "../tools/time";

const HEIGHT = 220; // when the container gives no height (stacked layout, tests)
const MIN_HEIGHT = 64; // a squeezed pane still gets a plot with its axes
export const RESIZE_DEBOUNCE_MS = 200;
const WHEEL_SETTLE_MS = 150; // a wheel or swipe moves the plot at once, and asks the server once it stops
const WHEEL_ZOOM = 1.25; // per wheel step
const RIGHT_PAD = 44; // room for the red limit numbers outside the plot
const FONT = '12px "Inter Variable", Inter, system-ui, sans-serif';

const css = (name: string, fallback: string): string => {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  } catch {
    return fallback;
  }
};
const toPlot = (s: Series): uPlot.AlignedData => [s.x.map((ms) => ms / 1000), s.line, s.lo, s.hi];
const EMPTY: uPlot.AlignedData = [[], [], [], []];

/** Where the cursor is and what the data says there (epoch ms; values null in a gap). */
type Hover = { x: number; y: number; t: number; v: number | null; lo: number | null; hi: number | null };
/** What the uPlot hooks call back into: kept in a ref so the plot, made once, always reaches the current component. */
type Ui = { onSelect: (fromMs: number, toMs: number) => void; onHover: (h: Hover | null) => void };

const iso = (ms: number) => new Date(ms).toISOString();
/** "2026-11-14 10:08:00Z – 10:10:00Z"; the second date only when the range crosses midnight (UTC, D-UI-13). */
function rangeText([from, to]: [number, number]): string {
  const [a, b] = [iso(from), iso(to)];
  const day = a.slice(0, 10) === b.slice(0, 10) ? "" : `${b.slice(0, 10)} `;
  return `${a.slice(0, 10)} ${a.slice(11, 19)}Z – ${day}${b.slice(11, 19)}Z`;
}

/** The value under the cursor, large: time to a tenth of a second, the value and unit, the bucket's min and max. */
function Tip({ h, unit, flip }: { h: Hover; unit?: string; flip: boolean }) {
  const t = iso(h.t);
  return (
    <div className="chart-tip" data-flip={flip || undefined} style={{ left: h.x, top: h.y }} aria-hidden="true">
      <span className="tip-time">{t.slice(11, 21)}Z <small>{t.slice(0, 10)}</small></span>
      <strong>{h.v === null ? "no data" : `${formatValue(h.v)}${unit ? ` ${unit}` : ""}`}</strong>
      {h.lo !== null && h.hi !== null && h.lo !== h.hi && <span>min {formatValue(h.lo)} · max {formatValue(h.hi)}</span>}
    </div>
  );
}

/** One parameter over a range: the line goes through each bucket's first and last value, the band is its min/max
 *  (M4, D-UI-11). Axes are UTC with a Z. One request per range, sized to the plot's pixel width (D-DB-04).
 *  The range follows the clock (live) until the operator zooms (buttons, a drag on the plot, Ctrl/⌘ + wheel or a
 *  pinch) or moves in time (buttons, Shift + wheel or a sideways swipe); then it stays where it was put. */
export function Chart({ vehicle, param, unit, limits, label }: { vehicle: string; param: string; unit?: string; limits?: Limits; label?: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const limitsRef = useRef(limits);
  limitsRef.current = limits;
  const [view, setView] = useState<View>({ span: WINDOWS["2 min"], end: null });
  const latest = useRef(view); // what the plot shows now: a wheel moves it before the request is made
  const range = useRef<[number, number] | null>(null); // ms, of the data last drawn
  const [width, setWidth] = useState(0);
  const [height, setHeight] = useState(HEIGHT);
  const [error, setError] = useState("");
  const [data, setData] = useState<uPlot.AlignedData>(EMPTY);
  const [hover, setHover] = useState<Hover | null>(null);

  const showRange = (r: [number, number]) => {
    range.current = r;
    plot.current?.setScale("x", { min: r[0] / 1000, max: r[1] / 1000 });
  };
  const go = (next: View) => {
    latest.current = next;
    setView(next);
  };
  const ui = useRef<Ui>({ onSelect: () => {}, onHover: () => {} });
  ui.current = { onSelect: (from, to) => go(select(from, to, Date.now())), onHover: setHover };

  // the plot follows its container: the width drives the request, the height only the plot
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let first = true;
    const ro = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      const w = Math.round(box?.width ?? 0);
      if (w <= 0) return;
      const h = box?.height ? Math.max(MIN_HEIGHT, Math.round(box.height)) : HEIGHT;
      const apply = () => {
        setWidth(w);
        setHeight(h);
      };
      clearTimeout(timer);
      // the first measurement starts the chart at once; a window being dragged refetches once it settles
      if (first) {
        first = false;
        apply();
      } else timer = setTimeout(apply, RESIZE_DEBOUNCE_MS);
    });
    ro.observe(el);
    return () => {
      clearTimeout(timer);
      ro.disconnect();
    };
  }, []);

  // Ctrl/⌘ + wheel (a trackpad pinch sends the same) zooms at the cursor; Shift + wheel or a sideways swipe moves in
  // time; a plain wheel is left to the page. The plot moves at once; the request waits until the gesture stops.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onWheel = (e: WheelEvent) => {
      const box = plot.current?.over.getBoundingClientRect();
      if (!box || box.width <= 0) return;
      const now = Date.now();
      const sideways = Math.abs(e.deltaX) > Math.abs(e.deltaY);
      let next: View;
      if (e.ctrlKey || e.metaKey) {
        const at = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
        next = zoom(latest.current, e.deltaY > 0 ? WHEEL_ZOOM : 1 / WHEEL_ZOOM, at, now);
      } else if (e.shiftKey || sideways) {
        next = pan(latest.current, (sideways ? e.deltaX : e.deltaY) / box.width, now);
      } else return;
      e.preventDefault();
      latest.current = next;
      showRange(rangeOf(next, now));
      clearTimeout(timer);
      timer = setTimeout(() => setView(latest.current), WHEEL_SETTLE_MS);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      el.removeEventListener("wheel", onWheel);
      clearTimeout(timer);
    };
  }, []);

  // one request per range: min, max, first and last come from the same buckets. A live range refreshes; history stays
  useEffect(() => {
    if (width === 0) return;
    const w = clampWidth(width);
    let live = true;
    const abort = new AbortController(); // a response for an old range or width must not replace a newer one
    const load = async () => {
      const [from, to] = rangeOf(view, Date.now());
      try {
        const buckets = await fetchBuckets(vehicle, param, iso(from), iso(to), w, abort.signal);
        if (!live) return;
        range.current = [from, to];
        setData(toPlot(toSeries(buckets, bucketMsFor(view.span, w))));
        setError("");
      } catch (e) {
        if (live && !abort.signal.aborted) setError(e instanceof Error ? e.message : "chart data unavailable");
      }
    };
    void load();
    const id = view.end === null ? setInterval(() => void load(), refreshMs(view.span)) : undefined;
    return () => {
      live = false;
      abort.abort();
      clearInterval(id);
    };
  }, [vehicle, param, view, width]);

  // the plot itself: made once, then resized and fed
  useEffect(() => {
    const el = host.current;
    if (!el || width === 0) return;
    if (plot.current) {
      plot.current.setSize({ width, height });
      return;
    }
    plot.current = new uPlot(options(width, param, unit, limitsRef, height, ui, label), data, el);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- created once; data is pushed by the next effect
  }, [width, height]);
  useEffect(() => {
    plot.current?.setData(data);
    if (range.current) showRange(range.current); // the x axis is the asked range, also where it holds no data
  }, [data]);
  useEffect(() => () => {
    plot.current?.destroy();
    plot.current = null;
  }, []);

  const live = view.end === null;
  const zoomBy = (f: number) => go(zoom(latest.current, f, latest.current.end === null ? 1 : 0.5, Date.now()));
  const step = (share: number) => go(pan(latest.current, share, Date.now()));
  return (
    <div className="chart">
      <div className="tools">
        <div className="seg" role="group" aria-label="Window">
          {(Object.keys(WINDOWS) as WindowKey[]).map((k) => (
            <button key={k} type="button" aria-pressed={live && view.span === WINDOWS[k]} onClick={() => go({ span: WINDOWS[k], end: null })}>{k}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Zoom">
          <button type="button" aria-label="Zoom out" title="Zoom out" disabled={view.span >= MAX_SPAN} onClick={() => zoomBy(2)}>−</button>
          <button type="button" aria-label="Zoom in" title="Zoom in" disabled={view.span <= MIN_SPAN} onClick={() => zoomBy(0.5)}>+</button>
        </div>
        <div className="seg" role="group" aria-label="History">
          <button type="button" aria-label="Older" title="Back in time by half the range" onClick={() => step(-0.5)}>‹ Older</button>
          <button type="button" aria-label="Newer" title="Forward in time by half the range" disabled={live} onClick={() => step(0.5)}>Newer ›</button>
          <button type="button" aria-label="Live" aria-pressed={live} onClick={() => go({ span: view.span, end: null })}>Live</button>
        </div>
      </div>
      <p className="range">
        <span>{rangeText(rangeOf(view, Date.now()))}</span>
        {live ? <span className="live">LIVE</span> : <span className="paused">HISTORY</span>}
        <span className="hint">Hover to read · drag to zoom · Ctrl/⌘ + scroll to zoom · Shift + scroll or swipe to move in time</span>
      </p>
      {error && <p role="alert">Chart data unavailable: {error}</p>}
      <div ref={wrap} className="plot">
        <div ref={host} className="plot-canvas" />
        {hover && <Tip h={hover} unit={unit} flip={hover.x > width * 0.6} />}
      </div>
    </div>
  );
}

/** uPlot options. Exported for the tests. */
export function options(
  width: number,
  param: string,
  unit: string | undefined,
  limits: { current: Limits | undefined },
  height = HEIGHT,
  ui: { current: Ui } = { current: { onSelect: () => {}, onHover: () => {} } },
  label?: string,
): uPlot.Options {
  const ink = css("--ink", "#0E0E0E");
  const muted = css("--ink-muted", "#57534E");
  const band = css("--zone-base", "#ECE9E5");
  const grid = css("--border", "#D8D3CD");
  const name = label || param;
  return {
    width,
    height,
    padding: [8, RIGHT_PAD, 0, 0],
    legend: { show: false },
    // a drag across the plot selects a range to zoom to; the view, not uPlot, sets the scale
    cursor: { drag: { x: true, y: false, setScale: false }, points: { size: 9, width: 2 } },
    scales: {
      x: { time: true },
      // the limits are always inside the range, so their lines can be seen even when the data stays well within them
      y: {
        range: (_u, dataMin, dataMax) => {
          const lim = limits.current;
          const all = [dataMin, dataMax, lim?.red_low, lim?.yellow_low, lim?.yellow_high, lim?.red_high].filter((v): v is number => typeof v === "number" && Number.isFinite(v));
          if (all.length === 0) return [0, 1];
          const lo = Math.min(...all);
          const hi = Math.max(...all);
          const pad = (hi - lo) * 0.05 || 1;
          return [lo - pad, hi + pad];
        },
      },
    },
    axes: [
      // UTC clock text with the Z (UI-08), from the same helper as every other time on the page; 90 px between labels
      { values: (_u, splits) => splits.map((s) => utcClock(iso(s * 1000))), stroke: muted, font: FONT, space: 90, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } },
      { values: (_u, splits) => splits.map((v) => formatValue(v)), stroke: muted, font: FONT, labelFont: `600 ${FONT}`, label: unit ? `${name} (${unit})` : name, size: 64, labelSize: 22, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } }, // room for the numbers and the title side by side
    ],
    series: [
      {},
      { label: param, stroke: ink, width: 1.5, points: { show: false }, spanGaps: false },
      { label: "min", stroke: band, width: 0, points: { show: false } },
      { label: "max", stroke: band, width: 0, points: { show: false } },
    ],
    bands: [{ series: [3, 2], fill: band }],
    hooks: {
      draw: [(u) => drawLimits(u, limits.current)],
      setSelect: [
        (u) => {
          const { left, width: w } = u.select;
          if (w < 4) return; // a click, not a drag
          const from = u.posToVal(left, "x") * 1000;
          const to = u.posToVal(left + w, "x") * 1000;
          u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
          ui.current.onSelect(from, to);
        },
      ],
      setCursor: [
        (u) => {
          const i = u.cursor.idx;
          const left = u.cursor.left ?? -1;
          if (i == null || left < 0) return ui.current.onHover(null);
          const [xs, line, lo, hi] = u.data as unknown as [number[], (number | null)[], (number | null)[], (number | null)[]];
          ui.current.onHover({
            x: left + u.over.offsetLeft,
            y: (u.cursor.top ?? 0) + u.over.offsetTop,
            t: xs[i]! * 1000,
            v: line[i] ?? null,
            lo: lo[i] ?? null,
            hi: hi[i] ?? null,
          });
        },
      ],
    },
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

export type PlotPick = { vehicle: string; param: string };

/** Pick a vehicle and parameter (or click one in a card or an alarm: the console owns the pick); the limits come
 *  from the mission db. The picker names each parameter, in mission-db order. */
export function ChartPanel({ store, mission, value, onChange }: {
  store: Store;
  mission: MissionDb | null;
  value: PlotPick;
  onChange: (p: PlotPick) => void;
}) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  const vehicle = vehicles.includes(value.vehicle) ? value.vehicle : (vehicles[0] ?? "");
  const params = paramViews(store.vehicle(vehicle)?.values ?? {}, mission, store.vehicle(vehicle)?.mode);
  const param = params.some((x) => x.id === value.param) ? value.param : (params[0]?.id ?? "");
  const view = params.find((x) => x.id === param); // its limits are the current mode set's (paramViews)
  const unit = paramMeta(mission, param)?.unit;
  return (
    <section className="chart-panel" aria-label="Chart">
      <h2>Chart</h2>
      {vehicle && param ? (
        <>
          <div className="filters">
            <label>
              Vehicle
              <select value={vehicle} onChange={(e) => onChange({ vehicle: e.target.value, param })}>
                {vehicles.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
            <label>
              Parameter
              <select value={param} onChange={(e) => onChange({ vehicle, param: e.target.value })}>
                {params.map((x) => <option key={x.id} value={x.id}>{x.label || x.id}</option>)}
              </select>
            </label>
          </div>
          <Chart key={`${vehicle}/${param}`} vehicle={vehicle} param={param} unit={unit} label={view?.label} limits={view?.limits} />
        </>
      ) : (
        <p className="empty">Waiting for telemetry…</p>
      )}
    </section>
  );
}
