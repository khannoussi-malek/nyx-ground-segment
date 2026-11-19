import type { FaultRequest } from "../server/api";

export type Scenario = { label: string; hint: string; fault: FaultRequest["fault"]; params?: Record<string, unknown>; clear?: boolean };
export const GROUPS: { title: string; items: Scenario[] }[] = [
  {
    title: "Alarms",
    items: [
      { label: "Cabin leak", fault: "leak", clear: true, hint: "YELLOW rate alarm, then RED LOW cabin pressure. Runs until cleared." },
      { label: "Overheat 30 s", fault: "overheat", params: { duration_s: 30 }, clear: true, hint: "RED HIGH cabin temperature." },
      { label: "Bus voltage spike 5 s", fault: "step", params: { param: "bus_voltage_v", value: 33, duration_s: 5 }, hint: "RED HIGH bus voltage after 3 frames." },
    ],
  },
  {
    title: "Signal",
    items: [
      { label: "Loss of signal 10 s", fault: "dropout", params: { duration_s: 10 }, hint: "STALE, then LOSS OF SIGNAL, then one gap event." },
      { label: "Frozen sensor 15 s", fault: "freeze", params: { duration_s: 15 }, clear: true, hint: "FROZEN on the sensor that stops changing." },
    ],
  },
  {
    title: "Data",
    items: [
      { label: "Bad frame", fault: "malformed", params: { kind: "schema" }, hint: "The frame is quarantined; FRAMES REJECTED shows." },
      { label: "Duplicate frames", fault: "duplicate", params: { count: 5 }, hint: "Stored once; nothing changes on screen." },
      { label: "Sequence jump", fault: "seq_jump", hint: "One sequence event in the event list." },
    ],
  },
  {
    title: "Commands",
    items: [
      { label: "Vehicle ignores commands 20 s", fault: "pause", params: { duration_s: 20 }, hint: "Send a command now: it ends FAILED." },
      { label: "Late execution 16 s", fault: "hold_exec", params: { duration_s: 16 }, hint: "A command is acknowledged at once, executed late." },
    ],
  },
];
