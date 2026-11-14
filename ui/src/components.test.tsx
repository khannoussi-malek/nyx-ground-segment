import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { ParamRow, type ParamView } from "./ParamRow";
import type { ParamState } from "./status";
import { VehicleCard, type VehicleCardProps } from "./VehicleCard";

const T = "2026-11-14T10:00:00.000000Z";
const cabin: ParamView = {
  id: "cabin_pressure_kpa",
  value: 101.32,
  label: "Cabin total pressure",
  unit: "kPa",
  limits: { red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 },
};
const row = (p: ParamView) =>
  renderToStaticMarkup(
    <table><ParamRow vehicle="NYX-TA1" seq={7} tOnboard={T} p={p} /></table>,
  );
const card = (over: Partial<VehicleCardProps> = {}) =>
  renderToStaticMarkup(
    <VehicleCard vehicle="NYX-TA1" mode="TEST" seq={7} tOnboard={T} ageMs={340} params={[cabin]} {...over} />,
  );

test("a nominal row is grey: no badge, no acquisition mark, label, id, value and unit", () => {
  const html = row(cabin);
  expect(html).not.toContain('class="badge"');
  expect(html).not.toContain('class="acq"');
  expect(html).toContain("Cabin total pressure");
  expect(html).toContain('class="pid">cabin_pressure_kpa');
  expect(html).toContain('class="num">101.32');
  expect(html).toContain('class="unit">kPa');
});

test("the value cell keeps the attributes the latency probe and smoke test read (D-UI-14)", () => {
  expect(row(cabin)).toContain(
    `data-vehicle="NYX-TA1" data-param="cabin_pressure_kpa" data-seq="7" data-t-onboard="${T}"`,
  );
});

test("a live RED shows a filled badge with the design's words", () => {
  const html = row({ ...cabin, state: { acq: "OK", level: "RED", side: "LOW" } });
  expect(html).toContain('data-level="RED"');
  expect(html).toMatch(/class="badge" data-tone="red">.*RED LOW<\/span>/);
});

test("a STALE value keeps its last limit as 'was' and says STALE", () => {
  const html = row({ ...cabin, state: { acq: "STALE", level: "RED", side: "LOW" } });
  expect(html).toMatch(/data-tone="was">.*was RED LOW/);
  expect(html).toMatch(/class="acq">.*STALE/);
});

test("one parameter = one tbody: name and value on top, the full-width gauge row under it", () => {
  const html = row(cabin);
  expect(html).toMatch(/^<table><tbody><tr>.*<\/tr><tr class="gauge-row"><td colSpan="2">/);
  expect(html.match(/<tbody/g)).toHaveLength(1);
});

test("the gauge: grey zones, pointer at the value, the red limits printed, hidden from screen readers", () => {
  const html = row(cabin);
  expect(html).toContain('class="gauge" aria-hidden="true"');
  expect(html.match(/class="zone" data-kind="(\w+)"/g)?.map((z) => z.split('"')[3])).toEqual([
    "red", "yellow", "normal", "yellow", "red",
  ]);
  expect(html).toMatch(/class="gauge-pointer" style="left:\d+\.\d{2}%"/);
  expect(html).toMatch(/class="gauge-mark" style="left:[\d.]+%">95<.*class="gauge-mark" style="left:[\d.]+%">106</);
});

test("a one-sided limit renders only its zones; negative limits print a true minus", () => {
  const html = row({ ...cabin, limits: { yellow_high: 8, red_high: 10 } });
  expect(html.match(/data-kind="(\w+)"/g)).toEqual(['data-kind="normal"', 'data-kind="yellow"', 'data-kind="red"']);
  expect(row({ ...cabin, limits: { red_low: -5, red_high: 5 } })).toContain(">\u22125<");
});

test("no limits, no gauge row; no value, no pointer", () => {
  expect(row({ ...cabin, limits: undefined })).not.toContain("gauge");
  expect(row({ ...cabin, value: null })).not.toContain("gauge-pointer");
  expect(row({ id: "x", value: null })).toContain('class="num">—');
});

test("no summary until states are wired: 'NOMINAL' without alarm data could hide a RED", () => {
  expect(card()).not.toContain('class="summary"');
});

test("with states wired the card counts them; a banner overrides", () => {
  const red = { ...cabin, state: { acq: "OK", level: "RED", side: "HIGH" } } as const;
  expect(card({ statesWired: true })).toMatch(/data-tone="nominal" role="status">NOMINAL/);
  expect(card({ statesWired: true, params: [red] })).toMatch(/data-tone="red".*1 RED/);
  expect(card({ banner: "LOSS OF SIGNAL" })).toMatch(/data-tone="down".*LOSS OF SIGNAL/);
});

test("the meta line shows mode, seq and the age clock", () => {
  expect(card()).toContain("TEST · seq 7 · updated");
  expect(card()).toContain(">0.3 s ago</time>");
  expect(card({ ageMs: null })).toContain(">—</time>");
});

test("live values merged with the mission db render label, unit and gauge", async () => {
  const { paramViews } = await import("./params");
  const mission = {
    parameters: { cabin_pressure_kpa: { unit: "kPa", type: "float", description: "Cabin total pressure" } },
    limits: { cabin_pressure_kpa: { checks: [{ type: "limit", red_low: 95, red_high: 106 }] } },
  } as unknown as import("./api").MissionDb;
  const html = card({ params: paramViews({ cabin_pressure_kpa: 101.32 }, mission) });
  expect(html).toContain("Cabin total pressure");
  expect(html).toContain('class="unit">kPa');
  expect(html).toContain('class="gauge"');
  expect(html).not.toContain('class="summary"'); // statesWired stays false until day 3
});

test("an unwired row asserts no state in the DOM", () => {
  const html = row(cabin);
  expect(html).not.toContain("data-level");
  expect(html).not.toContain("data-acq");
});

const mission = {
  parameters: { cabin_pressure_kpa: { unit: "kPa", type: "float", description: "Cabin total pressure" } },
  limits: { cabin_pressure_kpa: { checks: [{ type: "limit", red_low: 95, red_high: 106 }] } },
} as unknown as import("./api").MissionDb;
const tmOf = (frozen = false, age_ms = 40) => ({
  kind: "tm" as const, vehicle: "NYX-TA1", seq: 3, t_onboard: T, mode: "TEST" as const,
  last_cmd_id: null, values: { cabin_pressure_kpa: 101.3, unknown_p: 1 }, age_ms, frozen,
});
const redAlarm = {
  alarm_id: "a1", vehicle: "NYX-TA1", param: "cabin_pressure_kpa", state: "UNACK",
  level: "RED_HIGH", severity: "RED", trigger_check: "limit",
};

test("LiveTable before the first snapshot: labels and gauges, no summary, no row state", async () => {
  const { createStore, ingest } = await import("./store");
  const { LiveTable } = await import("./LiveTable");
  const store = createStore(() => 0);
  ingest(store, "tm", tmOf());
  store.applyAlarm(redAlarm as never); // an alarm that arrived early must not mark a row yet
  const html = renderToStaticMarkup(<LiveTable store={store} mission={mission} />);
  expect(html).toContain("Cabin total pressure");
  expect(html).toContain('class="gauge"');
  expect(html).toContain('class="label">unknown_p');
  for (const mark of ['class="summary"', 'class="badge"', 'class="acq"', "data-level", "data-acq"]) {
    expect(html).not.toContain(mark);
  }
});

test("LiveTable after the snapshot: the summary counts RED rows, the badge and the gauge fill show", async () => {
  const { createStore, ingest } = await import("./store");
  const { LiveTable } = await import("./LiveTable");
  const store = createStore(() => 0);
  ingest(store, "snapshot", {
    kind: "snapshot", vehicles: { "NYX-TA1": tmOf() }, alarms: [redAlarm], commands: [], events: [],
  });
  const html = renderToStaticMarkup(<LiveTable store={store} mission={mission} />);
  expect(html).toMatch(/data-tone="red".*1 RED/);
  expect(html).toContain('data-level="RED"');
  expect(html).toContain("RED HIGH");
  expect(active(html)).toEqual(['data-kind="red" data-side="HIGH" data-active="true"']);
});

test("LiveTable banners: broker down, lost link, frozen rows", async () => {
  const { createStore, ingest } = await import("./store");
  const { LiveTable } = await import("./LiveTable");
  const store = createStore(() => 0);
  const render = () => renderToStaticMarkup(<LiveTable store={store} mission={mission} />);
  ingest(store, "snapshot", { kind: "snapshot", vehicles: { "NYX-TA1": tmOf(true) }, alarms: [], commands: [], events: [] });
  expect(render()).toMatch(/data-tone="muted".*2 FROZEN/);
  ingest(store, "ground", { kind: "ground", broker: "down" });
  expect(render()).toContain("GROUND PROCESSING DOWN – BROKER");
  store.noteError();
  expect(render()).toContain("NO LIVE CONNECTION");
});

const active = (html: string) => html.match(/data-kind="\w+" data-side="\w+" data-active="true"/g) ?? [];

const limit = (level: "RED" | "YELLOW", side: "LOW" | "HIGH", acq: ParamState["acq"] = "OK"): ParamState =>
  ({ acq, level, side, check_kind: "limit" });

test("a live limit alarm fills exactly the zone of its level and side (G26)", () => {
  expect(active(row({ ...cabin, value: 94.2, state: limit("RED", "LOW") }))).toEqual([
    'data-kind="red" data-side="LOW" data-active="true"',
  ]);
  expect(active(row({ ...cabin, value: 107, state: limit("RED", "HIGH") }))).toEqual([
    'data-kind="red" data-side="HIGH" data-active="true"',
  ]);
  expect(active(row({ ...cabin, value: 104.5, state: limit("YELLOW", "HIGH") }))).toEqual([
    'data-kind="yellow" data-side="HIGH" data-active="true"',
  ]);
});

test("the fill follows the engine, not the pointer: RED HIGH held in the deadband stays filled", () => {
  // the value is back in the yellow range, the engine has not cleared RED yet (deadband)
  expect(active(row({ ...cabin, value: 105.2, state: limit("RED", "HIGH") }))).toEqual([
    'data-kind="red" data-side="HIGH" data-active="true"',
  ]);
});

test("no fill for a rate alarm, even with a side: only a limit check names a position zone", () => {
  expect(active(row({ ...cabin, state: { acq: "OK", level: "RED", side: "HIGH", check_kind: "roc" } }))).toEqual([]);
  expect(active(row({ ...cabin, state: { acq: "OK", level: "YELLOW", side: "LOW" } }))).toEqual([]); // kind unknown
});

test("no fill when unwired, nominal or not fresh", () => {
  expect(active(row(cabin))).toEqual([]);
  expect(active(row({ ...cabin, state: { acq: "OK", level: "NOMINAL", check_kind: "limit" } }))).toEqual([]);
  for (const acq of ["STALE", "FROZEN", "NO_DATA"] as const) {
    expect(active(row({ ...cabin, state: limit("RED", "LOW", acq) }))).toEqual([]);
  }
});

test("an off-scale value marks its pointer with the edge it is pinned to", () => {
  expect(row({ ...cabin, value: 200 })).toContain('class="gauge-pointer" data-clamped="1"');
  expect(row({ ...cabin, value: -50 })).toContain('class="gauge-pointer" data-clamped="-1"');
  expect(row(cabin)).not.toContain("data-clamped");
});

test("every hyphen in a printed limit becomes a true minus (exponents too)", () => {
  expect(row({ ...cabin, limits: { red_low: -1e-7, red_high: 1 } })).toContain(">−1e−7<");
});
