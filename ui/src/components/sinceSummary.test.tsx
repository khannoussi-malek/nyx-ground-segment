// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SinceSummary } from "./SinceSummary";
import { createStore, ingest } from "../state/store";

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

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const item = (t: string, kind: string, text: string) => ({ t, source: "alarm", kind, text, alarm: null, command: null, event: null });
const summary = (since_source: string, items: unknown[] = []) => ({ vehicle: "NYX-TA1", since: "2026-11-14T10:00:00Z", until: "2026-11-14T12:00:00Z", since_source, items });

function mount(reply: () => Response) {
  const f = vi.fn(async (_url: string) => reply());
  vi.stubGlobal("fetch", f);
  const store = createStore();
  const tm = { kind: "tm", vehicle: "NYX-TA1", seq: 1, t_onboard: "2026-11-14T10:00:00.000000Z", mode: "TEST", last_cmd_id: null, values: {}, age_ms: 1, frozen: false };
  ingest(store, "snapshot", { kind: "snapshot", vehicles: { "NYX-TA1": tm }, alarms: [], commands: [], events: [] });
  act(() => root.render(<SinceSummary store={store} />));
  return f;
}
function set(el: HTMLElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}
const field = (label: string) => [...host.querySelectorAll("label")].find((l) => l.textContent?.startsWith(label))!.querySelector("select, input") as HTMLSelectElement & HTMLInputElement;
const show = () => act(async () => host.querySelector<HTMLButtonElement>("button[type=submit]")!.click());

test("no vehicle chosen: Show is disabled and nothing is requested", async () => {
  const f = mount(() => json(200, summary("query")));
  expect(host.querySelector<HTMLButtonElement>("button[type=submit]")!.disabled).toBe(true);
  await show();
  expect(f).not.toHaveBeenCalled();
});

test("items are listed in the server's order with Z times and the operator text (the ACK note)", async () => {
  const f = mount(() => json(200, summary("last_logout", [
    item("2026-11-14T10:05:00Z", "ACK", "NYX-TA1 cabin_pressure_kpa ACK by fc1: valve checked"),
    item("2026-11-14T10:06:00Z", "COMMAND", "NYX-TA1 set_mode TEST: EXECUTED"),
  ])));
  set(field("Vehicle"), "NYX-TA1");
  await show();
  expect(f.mock.calls[0]![0]).toBe("/api/summary?vehicle=NYX-TA1"); // no since: the server's default
  const lis = [...host.querySelectorAll("li")];
  expect(lis).toHaveLength(2);
  expect(lis[0]!.textContent).toContain("2026-11-14T10:05:00.000Z");
  expect(lis[0]!.textContent).toContain("valve checked");
  expect(lis[0]!.textContent!.split("NYX-TA1")).toHaveLength(2); // the vehicle is named once per item
  expect(lis[1]!.textContent).toContain("COMMAND");
});

test.each([
  ["last_logout", "your last logout"],
  ["session_start", "the session start"],
  ["query", "chosen time"],
])("header names the since and its source: %s", async (src, words) => {
  mount(() => json(200, summary(src)));
  set(field("Vehicle"), "NYX-TA1");
  await show();
  expect(host.querySelector('[role="status"]')!.textContent).toContain(`Since 2026-11-14T10:00:00.000Z (${words})`);
});

test("an API error is shown as its message; a since without Z is refused before any request", async () => {
  const f = mount(() => json(400, { error: "SINCE_REQUIRED", message: "no logout and no session: pass since" }));
  set(field("Vehicle"), "NYX-TA1");
  await show();
  expect(host.querySelector('[role="alert"]')!.textContent).toBe("no logout and no session: pass since");
  set(field("Since"), "2026-11-14T10:00:00");
  await show();
  expect(f).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("ending in Z");
  set(field("Since"), "2026-11-14T10:00Z");
  await show();
  expect(f.mock.calls[1]![0]).toBe("/api/summary?vehicle=NYX-TA1&since=2026-11-14T10%3A00Z");
  set(field("Since"), "2026-11-14T10:00:00.000Z"); // the time the header prints
  await show();
  expect(f.mock.calls[2]![0]).toBe("/api/summary?vehicle=NYX-TA1&since=2026-11-14T10%3A00%3A00.000Z");
});
