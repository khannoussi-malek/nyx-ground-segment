// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MissionDb } from "./api";
import { AlarmList } from "./AlarmList";
import { alarmView } from "./fixtures";
import { createStore, ingest } from "./store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const MDB = {
  parameters: { cabin_pressure_kpa: { unit: "kPa", type: "float", description: "Cabin total pressure" } },
  limits: { cabin_pressure_kpa: { checks: [{ type: "limit", red_low: 95, red_high: 106 }] } },
} as unknown as MissionDb;

const a1 = alarmView({ alarm_id: "a1", vehicle: "NYX-TA1", value: 107, level: "RED_HIGH" });
const a2 = alarmView({ alarm_id: "a2", vehicle: "NYX-TA2", severity: "YELLOW", level: "YELLOW_HIGH", value: 105, opened_at: "2026-11-14T10:00:05Z" });
const a3 = alarmView({ alarm_id: "a3", vehicle: "NYX-TA1", state: "RTN_UNACK", param: "other" });

function mount(alarms = [a1, a2, a3]) {
  const store = createStore();
  ingest(store, "snapshot", { kind: "snapshot", vehicles: {}, alarms, commands: [], events: [] });
  act(() => root.render(<AlarmList store={store} mission={MDB} user="fc1" />));
  return store;
}
const rows = () => [...host.querySelectorAll<HTMLElement>("tr[data-alarm-id]")].map((r) => r.dataset.alarmId);
const button = (t: string) => [...host.querySelectorAll("dialog button")].find((b) => b.textContent === t) as HTMLButtonElement | undefined;
const ackButton = (id: string) => host.querySelector<HTMLButtonElement>(`tr[data-alarm-id="${id}"] button`);
function pick(label: string, value: string) {
  const sel = [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label))!.querySelector("select")!;
  act(() => {
    sel.value = value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
function typeNote(v: string) {
  const ta = host.querySelector("textarea")!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(ta, v);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function stubAck(status: number, body: unknown) {
  const f = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", f);
  return f;
}

test("rows show severity, vehicle, label, text, hashes, UTC time and state; RED first", () => {
  mount();
  expect(rows()).toEqual(["a1", "a3", "a2"]);
  const r = host.querySelector('tr[data-alarm-id="a1"]')!;
  expect(r.textContent).toContain("RED");
  expect(r.textContent).toContain("NYX-TA1");
  expect(r.textContent).toContain("Cabin total pressure");
  expect(r.textContent).toContain("RED HIGH · 107.00 kPa (limit 106)");
  expect(r.textContent).toContain("limits l · set s");
  expect(r.querySelector("time")!.textContent).toBe("10:00:00Z");
  expect(r.textContent).toContain("UNACKNOWLEDGED");
});

test("an RTN_UNACK row stays listed with its returned text and an ACK button", () => {
  mount();
  const r = host.querySelector('tr[data-alarm-id="a3"]')!;
  expect(r.textContent).toContain("RETURNED · was RED HIGH");
  expect(ackButton("a3")).not.toBeNull();
});

test("filters by vehicle, severity and state", () => {
  mount();
  pick("Vehicle", "NYX-TA2");
  expect(rows()).toEqual(["a2"]);
  pick("Vehicle", "ALL");
  pick("Severity", "RED");
  expect(rows()).toEqual(["a1", "a3"]);
  pick("State", "RTN_UNACK");
  expect(rows()).toEqual(["a3"]);
  pick("State", "ACKED");
  expect(host.textContent).toContain("No alarms match the filters");
});

test("before the first snapshot the list does not claim there are no alarms", () => {
  act(() => root.render(<AlarmList store={createStore()} mission={null} user="fc1" />));
  expect(host.textContent).toContain("Waiting for the first snapshot");
  expect(host.textContent).not.toContain("No open alarms");
  const store = createStore();
  ingest(store, "snapshot", { kind: "snapshot", vehicles: {}, alarms: [], commands: [], events: [] });
  act(() => root.render(<AlarmList store={store} mission={null} user="fc1" />));
  expect(host.textContent).toContain("No open alarms");
});

test("an acknowledged alarm stays listed without an ACK button", () => {
  mount([alarmView({ alarm_id: "a1", state: "ACKED" })]);
  expect(rows()).toEqual(["a1"]);
  expect(ackButton("a1")).toBeNull();
});

test("the ACK dialog shows the alarm; ACK stays disabled until the note has text; Cancel closes it", () => {
  mount();
  act(() => ackButton("a1")!.click());
  const dialog = host.querySelector("dialog")!;
  expect(dialog.open).toBe(true);
  expect(dialog.textContent).toContain("RED HIGH · 107.00 kPa (limit 106)");
  expect(button("ACK")!.disabled).toBe(true);
  typeNote("   ");
  expect(button("ACK")!.disabled).toBe(true);
  typeNote("seen on console");
  expect(button("ACK")!.disabled).toBe(false);
  act(() => button("Cancel")!.click());
  expect(host.querySelector("dialog")).toBeNull();
});

test("Escape (the dialog's cancel event) closes it too", () => {
  mount();
  act(() => ackButton("a1")!.click());
  act(() => void host.querySelector("dialog")!.dispatchEvent(new Event("cancel")));
  expect(host.querySelector("dialog")).toBeNull();
});

test("202 pending: the dialog closes and the row says ACK PENDING (by fc1)", async () => {
  mount();
  const f = stubAck(202, { request_id: "r", status: "pending" });
  act(() => ackButton("a1")!.click());
  typeNote("seen");
  await act(async () => button("ACK")!.click());
  const [url, init] = f.mock.calls[0] as unknown as [string, { body: string }];
  expect(url).toBe("/api/alarms/a1/ack");
  expect(JSON.parse(init.body)).toMatchObject({ note: "seen" });
  expect(host.querySelector("dialog")).toBeNull();
  const r = host.querySelector('tr[data-alarm-id="a1"]')!;
  expect(r.textContent).toContain("ACK PENDING (by fc1)");
  expect(ackButton("a1")!.disabled).toBe(true);
});

test("409 ALARM_CLEARED: the server's message shows inline and the dialog stays open", async () => {
  mount();
  stubAck(409, { error: "ALARM_CLEARED", message: "Alarm a1 is already cleared" });
  act(() => ackButton("a1")!.click());
  typeNote("seen");
  await act(async () => button("ACK")!.click());
  expect(host.querySelector('dialog [role="alert"]')!.textContent).toBe("Alarm a1 is already cleared");
  expect(host.querySelector("dialog")).not.toBeNull();
});

test("the same request id is resent on a retry within one dialog", async () => {
  mount();
  const f = stubAck(503, { error: "BROKER_DOWN", message: "try again" });
  act(() => ackButton("a1")!.click());
  typeNote("seen");
  await act(async () => button("ACK")!.click());
  await act(async () => button("ACK")!.click());
  const ids = f.mock.calls.map((c) => JSON.parse((c as unknown as [string, { body: string }])[1].body).request_id);
  expect(ids[0]).toBe(ids[1]);
});

test("a pending request shows NOT APPLIED while that vehicle's processing is down, PENDING again after", async () => {
  const store = mount();
  stubAck(202, { request_id: "r", status: "pending" });
  act(() => ackButton("a1")!.click());
  typeNote("seen");
  await act(async () => button("ACK")!.click());
  act(() => ingest(store, "hb", { kind: "hb", server_ms: 1, processing: { "NYX-TA1": false } }));
  expect(host.querySelector('tr[data-alarm-id="a1"]')!.textContent).toContain("ACK NOT APPLIED – processing down");
  act(() => ingest(store, "hb", { kind: "hb", server_ms: 2, processing: { "NYX-TA1": true } }));
  expect(host.querySelector('tr[data-alarm-id="a1"]')!.textContent).toContain("ACK PENDING (by fc1)");
});

test("ack_request from the stream: another user's pending ACK, a rejected one shows its reason", () => {
  const store = mount();
  const req = (status: "pending" | "rejected", reason: string | null) =>
    alarmView({ alarm_id: "a1", ack_request: { by_user: "fc2", request_id: "r", status, status_reason: reason } });
  act(() => ingest(store, "alarm", { kind: "alarm", alarm: req("pending", null) }));
  const row = () => host.querySelector('tr[data-alarm-id="a1"]')!.textContent;
  expect(row()).toContain("ACK PENDING (by fc2)");
  act(() => ingest(store, "alarm", { kind: "alarm", alarm: req("rejected", "alarm changed level") }));
  expect(row()).toContain("alarm changed level");
  expect(ackButton("a1")!.disabled).toBe(false);
});

test("my new pending ACK overrides an older refused request; the server's word on my own request wins", async () => {
  const old = { by_user: "fc2", request_id: "old", status: "rejected" as const, status_reason: "alarm changed level" };
  const store = mount([alarmView({ alarm_id: "a1", ack_request: old })]);
  const row = () => host.querySelector('tr[data-alarm-id="a1"]')!.textContent;
  expect(row()).toContain("alarm changed level");
  const f = stubAck(202, { request_id: "r", status: "pending" });
  act(() => ackButton("a1")!.click());
  typeNote("seen again");
  await act(async () => button("ACK")!.click());
  expect(row()).toContain("ACK PENDING (by fc1)");
  expect(ackButton("a1")!.disabled).toBe(true); // no double-submit window
  const mineId = JSON.parse((f.mock.calls[0] as unknown as [string, { body: string }])[1].body).request_id;
  act(() => ingest(store, "alarm", { kind: "alarm", alarm: alarmView({ alarm_id: "a1", ack_request: { ...old, request_id: mineId, by_user: "fc1", status_reason: "level changed again" } }) }));
  expect(row()).toContain("level changed again");
  expect(row()).not.toContain("ACK PENDING");
});

test("while the ACK request is in flight Cancel is disabled and Esc is ignored", async () => {
  mount();
  let release!: (r: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((res) => (release = res))));
  act(() => ackButton("a1")!.click());
  typeNote("seen");
  await act(async () => void button("ACK")!.click());
  expect(button("Cancel")!.disabled).toBe(true);
  const esc = new Event("cancel", { cancelable: true });
  act(() => void host.querySelector("dialog")!.dispatchEvent(esc));
  expect(esc.defaultPrevented).toBe(true);
  expect(host.querySelector("dialog")).not.toBeNull();
  await act(async () => release(new Response(JSON.stringify({ request_id: "r", status: "pending" }), { status: 202 })));
  expect(host.querySelector('tr[data-alarm-id="a1"]')!.textContent).toContain("ACK PENDING (by fc1)"); // the 202 was not dropped
});

test("closing the dialog gives the focus back to the ACK button that opened it", () => {
  mount();
  const opener = ackButton("a2")!;
  act(() => opener.focus());
  act(() => opener.click());
  act(() => button("Cancel")!.click());
  expect(document.activeElement).toBe(opener);
});

test("row ACK buttons name their alarm; the table has column headers", () => {
  mount();
  expect(ackButton("a1")!.getAttribute("aria-label")).toBe("Acknowledge NYX-TA1 cabin_pressure_kpa");
  expect([...host.querySelectorAll('thead th[scope="col"]')].map((h) => h.textContent)).toContain("Opened (UTC)");
});

test("a vehicle filter whose alarms are all gone resets instead of hiding everything", () => {
  const store = mount();
  pick("Vehicle", "NYX-TA2");
  expect(rows()).toEqual(["a2"]);
  act(() => ingest(store, "alarm", { kind: "alarm", alarm: alarmView({ alarm_id: "a2", vehicle: "NYX-TA2", state: "CLEARED" }) }));
  expect(rows()).toEqual(["a1", "a3"]);
});

test("a submit that bypasses the disabled button still needs a note, and two submits send once", async () => {
  mount();
  let release!: (r: Response) => void;
  const f = vi.fn(() => new Promise<Response>((res) => (release = res)));
  vi.stubGlobal("fetch", f);
  act(() => ackButton("a1")!.click());
  const form = host.querySelector("dialog form") as HTMLFormElement;
  await act(async () => form.requestSubmit()); // blank note
  typeNote("   ");
  await act(async () => form.requestSubmit()); // blank after trimming
  expect(f).not.toHaveBeenCalled();
  typeNote("seen");
  await act(async () => {
    form.requestSubmit();
    form.requestSubmit();
  });
  expect(f).toHaveBeenCalledTimes(1);
  await act(async () => release(new Response(JSON.stringify({ request_id: "r", status: "pending" }), { status: 202 })));
});
