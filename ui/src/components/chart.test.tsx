// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MissionDb } from "../server/api";
import { Chart, ChartPanel, options, RESIZE_DEBOUNCE_MS } from "./Chart";
import { createStore, ingest } from "../state/store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// uPlot needs a real canvas: replaced by a recorder, the geometry is tested through its hooks
const made = vi.hoisted(() => ({ plots: [] as { opts: any; data: any; el: HTMLElement; setData: any; setSize: any; destroy: any; setScale: any; setSelect: any; over: HTMLElement }[] }));
vi.mock("uplot", () => ({
  default: class {
    setData = vi.fn();
    setSize = vi.fn();
    destroy = vi.fn();
    setScale = vi.fn();
    setSelect = vi.fn();
    // the plotting area: 800 px wide, 40 px from the chart's left edge (the y axis)
    over = Object.assign(document.createElement("div"), { getBoundingClientRect: () => ({ left: 40, width: 800 }) });
    constructor(public opts: unknown, public data: unknown, public el: HTMLElement) {
      made.plots.push(this as never);
    }
  },
}));
vi.mock("uplot/dist/uPlot.min.css", () => ({}));

class FakeRO {
  static all: FakeRO[] = [];
  constructor(public cb: (e: { contentRect: { width: number; height?: number } }[]) => void) {
    FakeRO.all.push(this);
  }
  observe() {}
  disconnect() {}
  resize(width: number, height?: number) {
    act(() => this.cb([{ contentRect: { width, height } }]));
  }
}

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  made.plots.length = 0;
  FakeRO.all = [];
  vi.stubGlobal("ResizeObserver", FakeRO);
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout", "Date"] });
  vi.setSystemTime(new Date("2026-11-14T10:10:00.000Z"));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const bucket = (t: string, first: number, last: number) => ({ t, n: 2, first, last, lo: Math.min(first, last), hi: Math.max(first, last), mean: (first + last) / 2 });
function stubBuckets(rows: unknown[] = []) {
  const f = vi.fn(async (_url: string) => new Response(JSON.stringify(rows), { status: 200 }));
  vi.stubGlobal("fetch", f);
  return f;
}
const urls = (f: ReturnType<typeof stubBuckets>) => f.mock.calls.map((c) => new URL(String(c[0]), "http://x"));
const flush = () => act(async () => {});
const settle = () => act(async () => void vi.advanceTimersByTime(RESIZE_DEBOUNCE_MS));
async function mountChart(rows: unknown[] = []) {
  const f = stubBuckets(rows);
  act(() => root.render(<Chart vehicle="NYX-TA1" param="cabin_pressure_kpa" unit="kPa" limits={{ red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 }} />));
  FakeRO.all[0]!.resize(800);
  await flush();
  return f;
}

test("the request carries the container width and the 2-minute window; the plot is built at that width", async () => {
  const f = await mountChart();
  const u = urls(f)[0]!;
  expect(u.pathname).toBe("/api/telemetry/NYX-TA1/cabin_pressure_kpa");
  expect(u.searchParams.get("width")).toBe("800");
  expect(u.searchParams.get("to")).toBe("2026-11-14T10:10:00.000Z");
  expect(u.searchParams.get("from")).toBe("2026-11-14T10:08:00.000Z");
  expect(made.plots).toHaveLength(1);
  expect(made.plots[0]!.opts.width).toBe(800);
});

test("a resize refetches with the new width and resizes the plot; a width outside 100..4000 is clamped", async () => {
  const f = await mountChart();
  FakeRO.all[0]!.resize(1200);
  await settle();
  expect(urls(f).map((u) => u.searchParams.get("width"))).toEqual(["800", "1200"]);
  expect(made.plots).toHaveLength(1);
  expect(made.plots[0]!.setSize).toHaveBeenCalledWith({ width: 1200, height: 220 });
  FakeRO.all[0]!.resize(5000);
  await settle();
  expect(urls(f).at(-1)!.searchParams.get("width")).toBe("4000");
});

test("the plot fills its pane's height; a height-only change resizes the plot without refetching", async () => {
  const f = stubBuckets();
  act(() => root.render(<Chart vehicle="NYX-TA1" param="cabin_pressure_kpa" />));
  FakeRO.all[0]!.resize(800, 180);
  await flush();
  expect(made.plots[0]!.opts.height).toBe(180);
  FakeRO.all[0]!.resize(800, 300);
  await settle();
  expect(made.plots[0]!.setSize).toHaveBeenLastCalledWith({ width: 800, height: 300 });
  expect(f).toHaveBeenCalledTimes(1);
  FakeRO.all[0]!.resize(800, 20); // a squeezed pane still gets a readable plot
  await settle();
  expect(made.plots[0]!.setSize).toHaveBeenLastCalledWith({ width: 800, height: 64 });
});

test("a zero-width container asks for nothing", async () => {
  const f = stubBuckets();
  act(() => root.render(<Chart vehicle="v" param="p" />));
  FakeRO.all[0]!.resize(0);
  await flush();
  expect(f).not.toHaveBeenCalled();
});

test("the window buttons change the range; the refresh runs at max(1 s, window / 120)", async () => {
  const f = await mountChart();
  await act(async () => void vi.advanceTimersByTime(1000));
  expect(f).toHaveBeenCalledTimes(2); // 2 min: every second
  const btn = (t: string) => [...host.querySelectorAll("button")].find((b) => b.textContent === t)!;
  expect(btn("2 min").getAttribute("aria-pressed")).toBe("true");
  await act(async () => btn("1 h").click());
  const u = urls(f).at(-1)!;
  expect([u.searchParams.get("from"), u.searchParams.get("to")]).toEqual(["2026-11-14T09:10:01.000Z", "2026-11-14T10:10:01.000Z"]); // one second was advanced above
  expect(btn("1 h").getAttribute("aria-pressed")).toBe("true");
  const n = f.mock.calls.length;
  await act(async () => void vi.advanceTimersByTime(29_999)); // 1 h / 120 = 30 s
  expect(f.mock.calls.length).toBe(n);
  await act(async () => void vi.advanceTimersByTime(1));
  expect(f.mock.calls.length).toBe(n + 1);
});

test("the buckets reach the plot as first/last points in epoch seconds, with a null for a gap", async () => {
  const t0 = Date.parse("2026-11-14T10:09:58.000Z");
  // 120 s over 800 px: 150 ms buckets; the third one starts 1 s later: a gap
  const at = (ms: number, a: number, b: number) => bucket(new Date(t0 + ms).toISOString(), a, b);
  await mountChart([at(0, 1, 3), at(150, 3, 2), at(1150, 2, 2)]);
  const [x, line, lo, hi] = made.plots[0]!.setData.mock.calls.at(-1)![0];
  expect(x).toEqual([0, 149, 150, 299, 300, 1150, 1299].map((d) => (t0 + d) / 1000));
  expect(line).toEqual([1, 3, 3, 2, null, 2, 2]);
  expect([lo[0], hi[1], lo[4], hi[4]]).toEqual([1, 3, null, null]);
});

test("a failing request shows an error and keeps the plot; the next success clears it", async () => {
  const f = await mountChart();
  f.mockImplementationOnce(async () => new Response(null, { status: 503 }));
  await act(async () => void vi.advanceTimersByTime(1000));
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("503");
  await act(async () => void vi.advanceTimersByTime(1000));
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

test("unmounting destroys the plot and stops the refresh", async () => {
  const f = await mountChart();
  const n = f.mock.calls.length;
  act(() => root.unmount());
  expect(made.plots[0]!.destroy).toHaveBeenCalled();
  await act(async () => void vi.advanceTimersByTime(5000));
  expect(f.mock.calls.length).toBe(n);
  root = createRoot(host);
});

test("axes are UTC with a Z; the band runs between the max and min series", () => {
  const o = options(800, "cabin_pressure_kpa", "kPa", { current: undefined });
  const xAxis = o.axes![0]!;
  expect((xAxis.values as (u: unknown, s: number[]) => string[])(null, [Date.parse("2026-11-14T14:03:12Z") / 1000])).toEqual(["14:03:12Z"]);
  expect((o.axes![1]!.values as (u: unknown, s: number[]) => string[])(null, [101.3, -5])).toEqual(["101.30", "−5.00"]);
  expect(o.bands).toEqual([expect.objectContaining({ series: [3, 2] })]);
  expect(o.series![1]!.spanGaps).toBe(false); // a null breaks the line
});

function fakePlot(yOf: (v: number) => number, height = 200) {
  const calls: { op: string; args: unknown[] }[] = [];
  const rec = (op: string) => (...args: unknown[]) => void calls.push({ op, args });
  const ctx = new Proxy({ calls } as Record<string, unknown>, {
    get: (t, k: string) => (k in t ? t[k] : rec(k)),
    set: (t, k: string, v) => ((t[k] = v), true),
  });
  return { u: { ctx, bbox: { left: 40, top: 8, width: 700, height }, valToPos: yOf } as never, calls };
}
const draw = (limits: Parameters<typeof options>[3]["current"], u: unknown) =>
  (options(800, "p", undefined, { current: limits }).hooks!.draw![0] as (u: unknown) => void)(u);

test("limit labels: red outside the plot, yellow inside, a label near another is skipped, off-range limits are not drawn", () => {
  const f = fakePlot((v) => 400 - v * 3.5); // 95 -> 67.5, 98 -> 57, 104 -> 36, 106 -> 29
  draw({ red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 }, f.u);
  const text = f.calls.filter((c) => c.op === "fillText").map((c) => [c.args[0], c.args[1]]);
  // 95 and 106 kept (red); 98 is 10.5 px from 95 and 104 7 px from 106: dropped
  expect(text.map((t) => t[0])).toEqual(["95.00", "106.00"]);
  expect(text.every((t) => (t[1] as number) > 740)).toBe(true); // red: right of the plot edge (40 + 700)
  const g = fakePlot((v) => 400 - v * 10);
  draw({ red_high: 106, yellow_high: 104 }, g.u);
  const labelX = g.calls.filter((c) => c.op === "fillText");
  expect(labelX).toEqual([]); // 400 - 1060 < 0: off the plot, nothing drawn
  const h = fakePlot((v) => 100 - (v - 100) * 10); // 104 -> 60 (inside), 106 -> 40 (inside)
  draw({ yellow_high: 104 }, h.u);
  const yellow = h.calls.filter((c) => c.op === "fillText")[0]!;
  expect(yellow.args[1]).toBeLessThan(740); // yellow: inside the plot
  draw(undefined, h.u); // no limits: no throw
});

function panel(mission: MissionDb | null = null, mode = "TEST") {
  const store = createStore();
  const tm = (vehicle: string) => ({ kind: "tm", vehicle, seq: 1, t_onboard: "2026-11-14T10:00:00.000000Z", mode, last_cmd_id: null, values: { b_param: 1, a_param: 2 }, age_ms: 1, frozen: false });
  ingest(store, "snapshot", { kind: "snapshot", vehicles: { "NYX-TA1": tm("NYX-TA1"), "NYX-TA2": tm("NYX-TA2") }, alarms: [], commands: [], events: [] });
  act(() => root.render(<Picked store={store} mission={mission} />));
  return store;
}
/** the console owns the chart's choice (a click on a parameter elsewhere sets it) */
function Picked({ store, mission }: { store: ReturnType<typeof createStore>; mission: MissionDb | null }) {
  const [pick, setPick] = useState({ vehicle: "", param: "" });
  return <ChartPanel store={store} mission={mission} value={pick} onChange={setPick} />;
}
function pick(label: string, value: string) {
  const sel = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label))!.querySelector("select")!;
  act(() => {
    sel.value = value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

test("the panel lists the vehicle's parameters and charts the first; a change of vehicle or parameter remounts the chart", async () => {
  const f = stubBuckets();
  panel();
  FakeRO.all[0]!.resize(600);
  await flush();
  expect(urls(f)[0]!.pathname).toBe("/api/telemetry/NYX-TA1/a_param");
  pick("Parameter", "b_param");
  FakeRO.all.at(-1)!.resize(600);
  await flush();
  expect(urls(f).at(-1)!.pathname).toBe("/api/telemetry/NYX-TA1/b_param");
  pick("Vehicle", "NYX-TA2");
  FakeRO.all.at(-1)!.resize(600);
  await flush();
  expect(urls(f).at(-1)!.pathname).toBe("/api/telemetry/NYX-TA2/b_param");
  expect(made.plots[0]!.destroy).toHaveBeenCalled();
});

test("the limits and unit come from the mission db; before telemetry the panel waits", async () => {
  const mission = {
    parameters: { a_param: { unit: "kPa", type: "float", description: "" } },
    derived: { b_param: { unit: "W", description: "Bus power" } },
    limits: { a_param: { checks: [{ type: "limit", red_high: 106 }] } },
  } as unknown as MissionDb;
  stubBuckets();
  panel(mission);
  FakeRO.all[0]!.resize(600);
  await flush();
  expect(made.plots[0]!.opts.axes[1].label).toBe("a_param (kPa)");
  pick("Parameter", "b_param"); // a derived parameter: unit from the derived entries
  FakeRO.all.at(-1)!.resize(600);
  await flush();
  expect(made.plots.at(-1)!.opts.axes[1].label).toBe("Bus power (W)");
  act(() => root.render(<Picked store={createStore()} mission={null} />));
  expect(host.textContent).toContain("Waiting for telemetry");
});

test("the chart's limit line follows the vehicle's mode set: SAFE limits in SAFE, default ones otherwise", async () => {
  const mission = {
    parameters: { a_param: { unit: "kPa", type: "float", description: "" }, b_param: { unit: "kPa", type: "float", description: "" } },
    derived: {},
    limits: { a_param: { checks: [{ type: "limit", red_high: 106 }], checks_by_mode: { SAFE: [{ type: "limit", red_high: 50 }] } } },
  } as unknown as MissionDb;
  const top = () => {
    const range = made.plots.at(-1)!.opts.scales.y.range as (u: unknown, min: number | null, max: number | null) => [number, number];
    return range(null, 0, 1)[1];
  };
  stubBuckets();
  panel(mission, "SAFE");
  FakeRO.all[0]!.resize(600);
  await flush();
  expect(top()).toBeLessThan(106); // SAFE bound 50 in range, the default 106 is not drawn
  expect(top()).toBeGreaterThanOrEqual(50);
  panel(mission, "TEST");
  FakeRO.all.at(-1)!.resize(600);
  await flush();
  expect(top()).toBeGreaterThanOrEqual(106);
});

test("a window being dragged refetches once, after it settles; the first measurement is not delayed", async () => {
  const f = await mountChart();
  expect(f).toHaveBeenCalledTimes(1); // immediate
  FakeRO.all[0]!.resize(900);
  FakeRO.all[0]!.resize(1000);
  FakeRO.all[0]!.resize(1100);
  await act(async () => void vi.advanceTimersByTime(RESIZE_DEBOUNCE_MS - 1));
  expect(f).toHaveBeenCalledTimes(1);
  await act(async () => void vi.advanceTimersByTime(1));
  expect(urls(f).map((u) => u.searchParams.get("width"))).toEqual(["800", "1100"]);
});

test("an answer for an old range is aborted and never replaces the newer one", async () => {
  const resolvers: ((r: Response) => void)[] = [];
  const signals: AbortSignal[] = [];
  const f = vi.fn((_u: string, init?: { signal?: AbortSignal }) => {
    signals.push(init!.signal!);
    return new Promise<Response>((res) => resolvers.push(res));
  });
  vi.stubGlobal("fetch", f);
  act(() => root.render(<Chart vehicle="v" param="p" />));
  FakeRO.all[0]!.resize(800);
  await flush();
  const btn = [...host.querySelectorAll("button")].find((b) => b.textContent === "1 h")!;
  await act(async () => btn.click());
  expect(signals[0]!.aborted).toBe(true);
  const rows = (v: number) => new Response(JSON.stringify([bucket("2026-11-14T10:09:59.000Z", v, v)]), { status: 200 });
  await act(async () => resolvers[1]!(rows(2))); // the new range answers first
  await act(async () => resolvers[0]!(rows(1))); // the old one answers late
  const last = made.plots[0]!.setData.mock.calls.at(-1)![0];
  expect(last[1]).toEqual([2, 2]);
  expect(host.querySelector('[role="alert"]')).toBeNull(); // an aborted request is not an error
});

test("the y range always includes the limits, with a margin; without data or limits it is 0..1", () => {
  const range = (limits: Parameters<typeof options>[3]["current"]) =>
    (options(800, "p", undefined, { current: limits }).scales!.y!.range as (u: unknown, min: number | null, max: number | null) => [number, number]);
  const lim = { red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 };
  const [lo, hi] = range(lim)(null, 100, 102);
  expect(lo).toBeCloseTo(94.45);
  expect(hi).toBeCloseTo(106.55);
  expect(range(lim)(null, 90, 110)[0]).toBeLessThan(90); // data outside the limits widens it too
  expect(range(undefined)(null, 100, 102)).toEqual([99.9, 102.1]);
  expect(range(undefined)(null, null, null)).toEqual([0, 1]);
  expect(range({ red_high: 5 })(null, 5, 5)).toEqual([4, 6]); // a flat line at its limit still gets a range
});

test("the parameter picker names each parameter, in mission-db order; a choice from outside is shown", async () => {
  const mission = {
    derived: {}, parameters: { b_param: { unit: "A", type: "float", description: "Bus current" }, a_param: { unit: "V", type: "float", description: "Bus voltage" } },
    limits: {},
  } as unknown as MissionDb;
  const f = stubBuckets();
  const store = panel(mission);
  const opts = [...[...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith("Parameter"))!.querySelectorAll("option")];
  expect(opts.map((o) => [o.value, o.textContent])).toEqual([["b_param", "Bus current"], ["a_param", "Bus voltage"]]);
  act(() => root.render(<ChartPanel store={store} mission={mission} value={{ vehicle: "NYX-TA2", param: "a_param" }} onChange={() => {}} />));
  FakeRO.all.at(-1)!.resize(600);
  await flush();
  expect(urls(f).at(-1)!.pathname).toBe("/api/telemetry/NYX-TA2/a_param");
});

// ---- navigation: zoom, history, hover ----------------------------------------------------------------------------
const ranges = (f: ReturnType<typeof stubBuckets>) => urls(f).map((u) => `${u.searchParams.get("from")!.slice(11, 19)}-${u.searchParams.get("to")!.slice(11, 19)}`);
const press = (label: string) => act(async () => host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!.click());

test("zoom in halves the span and stays live at now; zoom out doubles it", async () => {
  const f = await mountChart();
  await press("Zoom in");
  await flush();
  expect(ranges(f).at(-1)).toBe("10:09:00-10:10:00");
  await press("Zoom out");
  await press("Zoom out");
  await flush();
  expect(ranges(f).at(-1)).toBe("10:06:00-10:10:00");
  expect(host.querySelector(".range")!.textContent).toContain("LIVE");
});

test("Older goes back half a span and stops following the clock; Newer comes back; Live returns to now", async () => {
  const f = await mountChart();
  await press("Older");
  await flush();
  expect(ranges(f).at(-1)).toBe("10:07:00-10:09:00");
  expect(host.querySelector(".range")!.textContent).not.toContain("LIVE");
  const n = f.mock.calls.length;
  await act(async () => void vi.advanceTimersByTime(10_000));
  expect(f.mock.calls.length).toBe(n); // history does not refresh
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Newer"]')!.disabled).toBe(false);
  await press("Live");
  await flush();
  expect(ranges(f).at(-1)).toBe("10:08:10-10:10:10");
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Newer"]')!.disabled).toBe(true);
});

test("the range under the toolbar is printed in UTC with its date", async () => {
  await mountChart();
  expect(host.querySelector(".range")!.textContent).toContain("2026-11-14 10:08:00Z – 10:10:00Z");
});

test("a range dragged on the plot becomes the view", async () => {
  const f = await mountChart();
  const p = made.plots[0]!;
  const t0 = Date.parse("2026-11-14T10:08:00Z") / 1000;
  const u = { select: { left: 200, width: 200 }, posToVal: (px: number) => t0 + (px / 800) * 120, setSelect: p.setSelect };
  await act(async () => p.opts.hooks.setSelect[0](u));
  await flush();
  expect(ranges(f).at(-1)).toBe("10:08:30-10:09:00");
  expect(p.setSelect).toHaveBeenCalledWith({ left: 0, top: 0, width: 0, height: 0 }, false); // the selection box goes away
});

test("hovering shows the time, the value with its unit and the bucket's min and max; leaving hides it", async () => {
  await mountChart();
  const p = made.plots[0]!;
  const t = Date.parse("2026-11-14T10:09:12.300Z") / 1000;
  const u = { cursor: { idx: 1, left: 100, top: 30 }, data: [[t - 1, t], [101.2, 101.31], [101.1, 101.25], [101.4, 101.4]], over: { offsetLeft: 40, offsetTop: 8 } };
  act(() => p.opts.hooks.setCursor[0](u));
  const tip = host.querySelector<HTMLElement>(".chart-tip")!;
  expect(tip.textContent).toContain("10:09:12.3Z");
  expect(tip.textContent).toContain("101.31 kPa");
  expect(tip.textContent).toContain("min 101.25 · max 101.40");
  act(() => p.opts.hooks.setCursor[0]({ ...u, cursor: { idx: null, left: -10, top: -10 } }));
  expect(host.querySelector(".chart-tip")).toBeNull();
});

test("Ctrl/⌘ + wheel zooms at the cursor; Shift + wheel or a sideways swipe moves in time; a plain wheel scrolls the page", async () => {
  const f = await mountChart();
  const plotEl = host.querySelector(".plot")!;
  const wheel = (init: WheelEventInit) => {
    const e = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
    // happy-dom keeps only the deltas of a WheelEventInit; a browser sets these from the init too
    for (const k of ["ctrlKey", "metaKey", "shiftKey", "clientX"] as const) Object.defineProperty(e, k, { value: init[k] ?? (k === "clientX" ? 0 : false) });
    act(() => void plotEl.dispatchEvent(e));
    return e;
  };
  expect(wheel({ deltaY: 100, clientX: 440 }).defaultPrevented).toBe(false); // the page scrolls
  expect(wheel({ deltaY: -100, ctrlKey: true, clientX: 440 }).defaultPrevented).toBe(true); // zoom in at the middle
  await act(async () => void vi.advanceTimersByTime(200));
  expect(ranges(f).at(-1)).toBe("10:08:12-10:09:48");
  expect(made.plots[0]!.setScale).toHaveBeenCalled(); // the plot moved at once, before the request
  wheel({ deltaX: 400, clientX: 440 }); // a swipe forward by half the plot: past now, so live again (span 96 s)
  wheel({ deltaY: -400, shiftKey: true, clientX: 440 }); // Shift + wheel up: half a span back in time
  await act(async () => void vi.advanceTimersByTime(200));
  expect(ranges(f).at(-1)).toBe("10:07:36-10:09:12"); // both moves, one request
});
