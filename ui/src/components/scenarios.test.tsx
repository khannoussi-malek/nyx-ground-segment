// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Scenarios } from "./Scenarios";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-11-19T14:03:17Z") });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const button = (name: string) => [...host.querySelectorAll("button")].find((b) => (b.getAttribute("aria-label") ?? b.textContent) === name);
const CONTROLS = json(200, { vehicles: ["NYX-TA1", "NYX-TA2"], faults: [] });

async function mount(faultAnswer: () => Promise<Response> = () => Promise.resolve(new Response(null, { status: 204 })), list: () => Response = () => CONTROLS.clone()) {
  const calls: { method: string; url: string; body: unknown; headers: unknown }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body ? JSON.parse(init.body as string) : undefined, headers: init?.headers });
    return url === "/api/test-controls" ? Promise.resolve(list()) : faultAnswer();
  });
  act(() => root.render(<Scenarios />));
  await flush();
  return calls;
}
const open = async () => { act(() => button("Scenarios")!.click()); await flush(); };
const pick = (v: string) => {
  const el = host.querySelector("select")!;
  act(() => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(el, v); el.dispatchEvent(new Event("change", { bubbles: true })); });
};

test("no button when test controls are off (404), and only the one GET is made", async () => {
  const calls = await mount(undefined, () => json(404, { error: "NOT_FOUND", message: "test controls are off" }));
  expect(button("Scenarios")).toBeUndefined();
  expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(["GET /api/test-controls"]);
});

test("button shown on 200; the panel is marked as a test tool", async () => {
  await mount();
  expect(button("Scenarios")).toBeDefined();
  await open();
  expect(host.textContent).toContain("Test scenarios");
  expect(host.textContent).toContain("Simulator faults for testing and demos. Not an operator function. Every use is audited.");
  expect(host.textContent).toContain("YELLOW rate alarm, then RED LOW cabin pressure");
});

test("no vehicle chosen: every scenario button is disabled", async () => {
  const calls = await mount();
  await open();
  expect(button("Cabin leak")!.disabled).toBe(true);
  act(() => button("Cabin leak")!.click());
  expect(calls).toHaveLength(1);
});

test("a click posts the exact body and says what started, in UTC", async () => {
  const calls = await mount();
  await open();
  pick("NYX-TA1");
  act(() => button("Bus voltage spike 5 s")!.click());
  await flush();
  expect(calls[1]).toMatchObject({
    method: "POST",
    url: "/api/test-controls/fault",
    body: { vehicle: "NYX-TA1", fault: "step", params: { param: "bus_voltage_v", value: 33, duration_s: 5 } },
    headers: { "X-GS-Client": "console" },
  });
  expect(host.querySelector('[role="status"]')!.textContent).toBe("Bus voltage spike 5 s started on NYX-TA1 at 14:03:17Z");
});

test("a paramless fault sends no params", async () => {
  const calls = await mount();
  await open();
  pick("NYX-TA2");
  act(() => button("Cabin leak")!.click());
  await flush();
  expect(calls[1]!.body).toEqual({ vehicle: "NYX-TA2", fault: "leak" });
});

test("Clear sends DELETE with vehicle and fault only", async () => {
  const calls = await mount();
  await open();
  pick("NYX-TA1");
  act(() => button("Clear Overheat 30 s")!.click());
  await flush();
  expect(calls[1]).toMatchObject({ method: "DELETE", body: { vehicle: "NYX-TA1", fault: "overheat" } });
  expect(host.querySelector('[role="status"]')!.textContent).toBe("Overheat 30 s cleared on NYX-TA1 at 14:03:17Z");
  expect(button("Clear Loss of signal 10 s")).toBeUndefined(); // timed signal loss ends by itself
});

test("the API's error message is shown", async () => {
  await mount(() => Promise.resolve(json(503, { error: "UNAVAILABLE", message: "simulator unreachable" })));
  await open();
  pick("NYX-TA1");
  act(() => button("Cabin leak")!.click());
  await flush();
  expect(host.querySelector('[role="alert"]')!.textContent).toBe("simulator unreachable");
});

test("buttons are disabled while a request is in flight", async () => {
  let done!: (r: Response) => void;
  const calls = await mount(() => new Promise<Response>((r) => (done = r)));
  await open();
  pick("NYX-TA1");
  act(() => button("Cabin leak")!.click());
  expect(button("Sequence jump")!.disabled).toBe(true);
  act(() => button("Sequence jump")!.click());
  expect(calls).toHaveLength(2);
  await act(async () => done(new Response(null, { status: 204 })));
  expect(button("Sequence jump")!.disabled).toBe(false);
});
