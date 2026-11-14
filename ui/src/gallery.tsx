// Dev-only state gallery (vite dev: /gallery.html; not a build input). Shows every D-UI-06 state
// with the real mission-db labels, units and limits, so the look can be reviewed before day 3 wires it.
import "@fontsource-variable/inter";
import { createRoot } from "react-dom/client";
import type { ParamView } from "./ParamRow";
import type { ParamState } from "./status";
import "./style.css";
import { VehicleCard } from "./VehicleCard";

const P: Record<string, Omit<ParamView, "value" | "state">> = {
  bus_current_a: { id: "bus_current_a", label: "Main bus current", unit: "A", limits: { yellow_high: 8, red_high: 10 } },
  bus_voltage_v: { id: "bus_voltage_v", label: "Main bus voltage", unit: "V", limits: { red_low: 24, yellow_low: 26, yellow_high: 30, red_high: 32 } },
  cabin_pressure_kpa: { id: "cabin_pressure_kpa", label: "Cabin total pressure", unit: "kPa", limits: { red_low: 95, yellow_low: 98, yellow_high: 104, red_high: 106 } },
  cabin_temp_c: { id: "cabin_temp_c", label: "Cabin temperature", unit: "degC", limits: { red_low: 15, yellow_low: 18, yellow_high: 27, red_high: 30 } },
  rate_x_dps: { id: "rate_x_dps", label: "Attitude rate X", unit: "deg/s", limits: { red_low: -5, yellow_low: -2, yellow_high: 2, red_high: 5 } },
  rate_y_dps: { id: "rate_y_dps", label: "Attitude rate Y", unit: "deg/s", limits: { red_low: -5, yellow_low: -2, yellow_high: 2, red_high: 5 } },
  rate_z_dps: { id: "rate_z_dps", label: "Attitude rate Z", unit: "deg/s", limits: { red_low: -5, yellow_low: -2, yellow_high: 2, red_high: 5 } },
  tank_pressure_bar: { id: "tank_pressure_bar", label: "Propellant tank pressure", unit: "bar", limits: { red_low: 15, yellow_low: 17, yellow_high: 23, red_high: 25 } },
};
const ok: ParamState = { acq: "OK", level: "NOMINAL" };
const row = (id: string, value: number | null, state: ParamState = ok): ParamView => ({ ...P[id], value, state });
const T = "2026-11-14T16:10:53.184113Z";

const nominal = [
  row("bus_current_a", 5.01), row("bus_voltage_v", 27.99), row("cabin_pressure_kpa", 101.32),
  row("cabin_temp_c", 22.02), row("rate_x_dps", -0.02), row("rate_y_dps", 0.0),
  row("rate_z_dps", -0.01), row("tank_pressure_bar", 19.98),
];
const mixed = [
  row("bus_current_a", 8.6, { acq: "OK", level: "YELLOW", side: "HIGH" }),
  row("bus_voltage_v", 28.07),
  row("cabin_pressure_kpa", 94.2, { acq: "OK", level: "RED", side: "LOW" }),
  row("cabin_temp_c", 21.94, { acq: "FROZEN", level: "NOMINAL" }),
  row("rate_x_dps", 5.8, { acq: "STALE", level: "RED", side: "HIGH" }),
  row("rate_y_dps", 0.01),
  row("rate_z_dps", null, { acq: "NO_DATA", level: "NOMINAL" }),
  row("tank_pressure_bar", 20.01),
];
const card = { mode: "TEST", seq: 24290, tOnboard: T };

createRoot(document.getElementById("root")!).render(
  <>
    <header>
      <h1>Nyx</h1>
      <span>fc1</span>
      <button>Log out</button>
    </header>
    <main>
      <VehicleCard vehicle="NYX-TA1" {...card} ageMs={340} params={mixed} statesWired />
      <VehicleCard vehicle="NYX-TA2" {...card} ageMs={420} params={nominal} statesWired />
      <VehicleCard vehicle="NYX-TA2" {...card} ageMs={4_800} params={nominal} banner="LOSS OF SIGNAL" />
      <VehicleCard vehicle="NYX-TA1" {...card} ageMs={360} params={nominal.map(({ state: _, ...p }) => p)} />
    </main>
  </>,
);
