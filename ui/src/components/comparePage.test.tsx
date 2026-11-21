// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MissionDb } from "../server/api";
import { compareOptions } from "./CompareCharts";
import { ComparePage } from "./ComparePage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const made = vi.hoisted(() => ({ plots: [] as { opts: any; data: any }[] }));
vi.mock("uplot", () => ({
  default: class {
    destroy = vi.fn();
    setSize = vi.fn();
    constructor(public opts: unknown, public data: unknown) {
      made.plots.push(this as never);
    }
  },
}));
vi.mock("uplot/dist/uPlot.min.css", () => ({}));

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  made.plots.length = 0;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const json = (status: number, body: unknown, headers: Record<string, string> = {}) => new Response(JSON.stringify(body), { status, headers });
const sess = (id: number, name: string, vehicle: string, at: string) => ({ id, name, vehicle, started_at: at, ended_at: null, ended_by: null, conductor: "c" });
const SESSIONS = [sess(1, "burn A", "NYX-TA1", "2026-11-14T10:00:00Z"), sess(2, "burn B", "NYX-TA2", "2026-11-14T11:30:00Z")];
const mission = { derived: {}, parameters: { cabin_pressure_kpa: { unit: "kPa", description: "Cabin pressure" }, temp_c: { unit: "C", description: "Cabin temperature" } } } as unknown as MissionDb;
const bucket = (t: number, v: number) => ({ t_rel_s: t, lo: v - 1, hi: v + 1, mean: v, n: 2 });
const COMPARE = {
  bucket_s: 1,
  sessions: SESSIONS.map((s) => ({ id: s.id, name: s.name, vehicle: s.vehicle, started_at: s.started_at, ended_at: null, duration_s: 90 })),
  series: [
    { session_id: 1, param: "cabin_pressure_kpa", buckets: [bucket(0, 100), bucket(65, 101)] },
    { session_id: 2, param: "cabin_pressure_kpa", buckets: [bucket(0, 90)] },
  ],
};

function mount(compare: () => Response) {
  const f = vi.fn(async (url: string) => {
    if (url.startsWith("/api/sessions")) return json(200, SESSIONS);
    if (url.startsWith("/api/vehicles")) return json(200, { vehicles: ["NYX-TA1", "NYX-TA2"] });
    if (url.startsWith("/api/compare")) return compare();
    if (url.startsWith("/api/export")) return json(429, { error: "EXPORT_BUSY", message: "an export is running; retry" }, { "Retry-After": "5" });
    throw new Error(url);
  });
  vi.stubGlobal("fetch", f);
  return { f, ready: act(async () => root.render(<ComparePage mission={mission} />)) };
}
const box = (text: string) => [...host.querySelectorAll("label")].find((l) => l.textContent?.includes(text))!.querySelector("input")!;
const click = (el: HTMLElement) => act(async () => el.click());
const compareButton = () => [...host.querySelectorAll("button")].find((b) => b.textContent === "Compare")!;

async function pickTwo() {
  await click(box("burn A"));
  await click(box("burn B"));
  await click(box("Cabin pressure"));
  await click(compareButton());
}

test("Compare stays disabled below two sessions and one parameter", async () => {
  const { ready } = mount(() => json(200, COMPARE));
  await ready;
  expect(compareButton().disabled).toBe(true);
  await click(box("burn A"));
  await click(box("Cabin pressure"));
  expect(compareButton().disabled).toBe(true);
});

test("the request names the sessions and parameters; the legend gives name, vehicle and UTC start", async () => {
  const { f, ready } = mount(() => json(200, COMPARE));
  await ready;
  await pickTwo();
  const url = f.mock.calls.map((c) => c[0]).find((u) => u.startsWith("/api/compare"))!;
  expect(decodeURIComponent(url)).toBe("/api/compare?sessions=1,2&params=cabin_pressure_kpa");
  const legend = [...host.querySelectorAll(".compare-legend li")].map((l) => l.textContent);
  expect(legend).toEqual([
    expect.stringContaining("burn A · NYX-TA1 · started 2026-11-14T10:00:00.000Z"),
    expect.stringContaining("burn B · NYX-TA2 · started 2026-11-14T11:30:00.000Z"),
  ]);
  // swatches: a different dash and colour per session (not colour alone)
  const [a, b] = [...host.querySelectorAll(".compare-legend line")];
  expect(a!.getAttribute("stroke-dasharray")).not.toBe(b!.getAttribute("stroke-dasharray"));
  expect(a!.getAttribute("stroke")).not.toBe(b!.getAttribute("stroke"));
});

test("one chart per parameter: dash per session, a band each, T+ axis labels", async () => {
  const { ready } = mount(() => json(200, COMPARE));
  await ready;
  await pickTwo();
  expect(made.plots).toHaveLength(1);
  const o = compareOptions(COMPARE as never, "cabin_pressure_kpa", "kPa", 800);
  expect(o.series).toHaveLength(7); // x + (line, min, max) x 2
  expect([o.series![1]!.dash, o.series![4]!.dash]).toEqual([[], [8, 4]]);
  expect(o.series![1]!.stroke).not.toBe(o.series![4]!.stroke);
  expect(o.bands).toHaveLength(2);
  o.bands!.forEach((b, i) => {
    expect(b.series).toEqual([3 * i + 3, 3 * i + 2]); // [max, min] of session i
    expect(b.fill).toBe(`${o.series![3 * i + 1]!.stroke}26`);
  });
  const xAxis = o.axes![0]!.values as (u: unknown, s: number[]) => string[];
  expect(xAxis(null, [0, 65, 3725])).toEqual(["T+00:00:00", "T+00:01:05", "T+01:02:05"]);
  expect(made.plots[0]!.data[0]).toEqual([0, 65]);
  expect(made.plots[0]!.data[1]).toEqual([100, 101]);
  expect(made.plots[0]!.data[4]).toEqual([90, null]);
  // band columns: min/max of each session are the API's lo/hi, at the series indices the bands point to
  const d = made.plots[0]!.data;
  expect([d[2], d[3]]).toEqual([[99, 100], [101, 102]]);
  expect([d[5], d[6]]).toEqual([[89, null], [91, null]]);
  expect(made.plots[0]!.opts.bands).toHaveLength(2);
  expect(host.querySelector(".compare-plot")!.getAttribute("data-bands")).toBe("2");
  expect(host.querySelector(".compare-plot")!.getAttribute("data-bands")).toBe(String(o.bands!.length));
});

test.each([
  [400, "INVALID_SESSIONS", "sessions must be 2 to 6"],
  [404, "NOT_FOUND", "session 9 not found"],
  [409, "ARCHIVE_CORRUPT", "an archive object failed its hash check"],
  [429, "COMPARE_BUSY", "a compare is running; retry"],
])("a %i is shown with the server's message and no chart", async (status, error, message) => {
  const { ready } = mount(() => json(status, { error, message }));
  await ready;
  await pickTwo();
  expect(host.querySelector("[role=alert]")!.textContent).toBe(message);
  expect(made.plots).toHaveLength(0);
});

function setText(el: HTMLInputElement, v: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const exportForm = () => host.querySelector<HTMLElement>("section.export")!;

test("export: a time without Z is refused before any request", async () => {
  const { f, ready } = mount(() => json(200, COMPARE));
  await ready;
  const sel = exportForm().querySelectorAll("select")[1]!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(sel, "NYX-TA1");
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const [from, to] = exportForm().querySelectorAll("input");
  setText(from!, "2026-11-20T08:05");
  setText(to!, "2026-11-20T09:05Z");
  await click(exportForm().querySelector("button")!);
  expect(exportForm().querySelector("[role=alert]")!.textContent).toMatch(/From must be a UTC time ending in Z/);
  expect(f.mock.calls.some((c) => c[0].startsWith("/api/export"))).toBe(false);
});

test("export: 429 EXPORT_BUSY shows the Retry-After", async () => {
  const { f, ready } = mount(() => json(200, COMPARE));
  await ready;
  const session = exportForm().querySelectorAll("select")[0]!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(session, "1");
    session.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await click(exportForm().querySelector("button")!);
  expect(f.mock.calls.map((c) => c[0])).toContain("/api/export?session=1&format=csv");
  expect(exportForm().querySelector("[role=alert]")!.textContent).toBe("an export is running; retry (retry in 5 s)");
});

test("pickers, chart title and axis label show the mission-db description with the unit; the id stays as hover text", async () => {
  const { ready } = mount(() => json(200, COMPARE));
  await ready;
  await pickTwo();
  expect(box("Cabin pressure (kPa)").closest("label")!.getAttribute("title")).toBe("cabin_pressure_kpa");
  expect(host.querySelector(".compare-charts h3")!.textContent).toBe("Cabin pressure (kPa)");
  expect(host.querySelector(".compare-charts h3")!.getAttribute("title")).toBe("cabin_pressure_kpa");
  expect(made.plots[0]!.opts.axes[1].label).toBe("Cabin pressure (kPa)");
  const exportOpts = [...host.querySelectorAll(".export select[multiple] option")];
  expect(exportOpts.map((o) => o.textContent)).toContain("Cabin pressure (kPa)");
  expect(exportOpts.map((o) => o.getAttribute("value"))).toContain("cabin_pressure_kpa");
});

test("changing the selection clears the shown result", async () => {
  const { ready } = mount(() => json(200, COMPARE));
  await ready;
  await pickTwo();
  expect(host.querySelector(".compare-legend")).not.toBeNull();
  await click(box("burn B"));
  expect(host.querySelector(".compare-charts")).toBeNull();
  await click(box("burn B"));
  await click(compareButton());
  expect(host.querySelector(".compare-legend")).not.toBeNull();
  await click(box("Cabin temperature"));
  expect(host.querySelector(".compare-charts")).toBeNull();
});

test("while a compare runs the button is disabled and says so; a late answer for an older selection is dropped", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const f = vi.fn(async (url: string) => {
    if (url.startsWith("/api/sessions")) return json(200, SESSIONS);
    if (url.startsWith("/api/vehicles")) return json(200, { vehicles: [] });
    await gate;
    return json(200, COMPARE);
  });
  vi.stubGlobal("fetch", f);
  await act(async () => root.render(<ComparePage mission={mission} />));
  await click(box("burn A"));
  await click(box("burn B"));
  await click(box("Cabin pressure"));
  await click(compareButton());
  const b = [...host.querySelectorAll("button")].find((x) => x.textContent === "Comparing…")!;
  expect(b.disabled).toBe(true);
  expect(b.getAttribute("aria-busy")).toBe("true");
  await click(box("Cabin temperature")); // selection changes while the request is out
  await act(async () => release());
  expect(host.querySelector(".compare-charts")).toBeNull();
  expect(compareButton().disabled).toBe(false);
});
