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
    <table><tbody><ParamRow vehicle="NYX-TA1" seq={7} tOnboard={T} p={p} /></tbody></table>,
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

test("the gauge marker sits at the value's track position; no limits, no gauge", () => {
  expect(row(cabin)).toMatch(/class="gauge-marker" style="left:\d+\.\d{2}%"/);
  expect(row({ ...cabin, limits: undefined })).not.toContain('class="gauge"');
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
