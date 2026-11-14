import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import { ParamRow, type ParamView } from "./ParamRow";
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

test("LiveTable from a real store + mission db: labels and gauges, no state marks while unwired", async () => {
  const { createStore } = await import("./store");
  const { LiveTable } = await import("./LiveTable");
  const store = createStore(() => 0);
  store.applyTm({
    kind: "tm", vehicle: "NYX-TA1", seq: 3, t_onboard: T, mode: "TEST",
    last_cmd_id: null, values: { cabin_pressure_kpa: 101.3, unknown_p: 1 }, age_ms: 40,
  });
  const mission = {
    parameters: { cabin_pressure_kpa: { unit: "kPa", type: "float", description: "Cabin total pressure" } },
    limits: { cabin_pressure_kpa: { checks: [{ type: "limit", red_low: 95, red_high: 106 }] } },
  } as unknown as import("./api").MissionDb;
  const html = renderToStaticMarkup(<LiveTable store={store} mission={mission} />);
  expect(html).toContain("Cabin total pressure");
  expect(html).toContain('class="gauge"');
  expect(html).toContain('class="label">unknown_p');
  for (const mark of ['class="summary"', 'class="badge"', 'class="acq"', "data-level", "data-acq"]) {
    expect(html).not.toContain(mark);
  }
});
