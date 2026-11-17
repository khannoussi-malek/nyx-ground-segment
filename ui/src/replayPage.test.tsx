// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { alarmView } from "./fixtures";
import { ReplayPage } from "./ReplayPage";
import { createStore, ingest } from "./store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  FakeES.last = undefined;
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
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

class FakeES {
  static last: FakeES | undefined;
  handlers: Record<string, (e: MessageEvent<string>) => void> = {};
  closed = false;
  constructor(public url: string) { FakeES.last = this; }
  addEventListener(k: string, f: (e: MessageEvent<string>) => void) { this.handlers[k] = f; }
  close() { this.closed = true; }
  emit(k: string, data: unknown = {}) { act(() => this.handlers[k]?.({ data: JSON.stringify(data) } as MessageEvent<string>)); }
}

function mount(fetchMock: (url: string, init?: RequestInit) => Promise<Response>, live = createStore()) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", (u: string, i?: RequestInit) => { calls.push(`${i?.method ?? "GET"} ${u}`); return fetchMock(u, i); });
  vi.stubGlobal("EventSource", FakeES);
  act(() => root.render(<ReplayPage live={live} mission={null} user="op" />));
  return calls;
}
const vehicles = (u: string) => (u === "/api/vehicles" ? Promise.resolve(json(200, { vehicles: ["NYX-TA1"], parameters: {} })) : Promise.reject(new Error(u)));
const setInput = (label: string, v: string) => {
  const el = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => { set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); });
};
const button = (name: string) => [...host.querySelectorAll("button")].find((b) => b.textContent === name)!;
async function fillAndStart() {
  await flush();
  setInput("From (UTC)", "2026-11-10T14:00");
  setInput("To (UTC)", "2026-11-10T14:20");
  act(() => button("Start").click());
  await flush();
}

test("inputs are UTC", async () => {
  let body: { from: string; to: string; vehicle: string; speed: number } | undefined;
  mount((u, i) => {
    if (i?.method === "POST") { body = JSON.parse(String(i.body)); return Promise.resolve(json(201, { replay_id: "r1" })); }
    return vehicles(u);
  });
  expect(host.textContent).toContain("From (UTC)");
  await fillAndStart();
  expect(body).toEqual({ vehicle: "NYX-TA1", from: "2026-11-10T14:00:00.000Z", to: "2026-11-10T14:20:00.000Z", speed: 1 });
  expect(FakeES.last?.url).toBe("/api/replays/r1/events");
});

test("banner shows the live counter and cannot be dismissed", async () => {
  const live = createStore();
  const a = (id: string, severity: "RED" | "YELLOW") => alarmView({ alarm_id: id, severity, last: { ...alarmView().last, key: id } });
  ingest(live, "snapshot", { kind: "snapshot", vehicles: {}, alarms: [a("1", "RED"), a("2", "YELLOW")], commands: [], events: [] });
  mount(vehicles, live);
  expect(host.textContent).toContain("REPLAY");
  expect(host.textContent).toContain("LIVE: 2 unacknowledged (1 RED)");
  act(() => ingest(live, "alarm", { kind: "alarm", alarm: a("3", "RED") }));
  expect(host.textContent).toContain("LIVE: 3 unacknowledged (2 RED)");
  expect([...host.querySelectorAll("[role=status] button, .replay-banner button")]).toHaveLength(0);
});

test("409 shows the hash message", async () => {
  mount((u, i) => i?.method === "POST"
    ? Promise.resolve(json(409, { error: "ARCHIVE_CORRUPT", message: "archive object failed hash check" }))
    : vehicles(u));
  await fillAndStart();
  expect(host.querySelector("[role=alert]")?.textContent).toContain("archive object failed hash check");
  expect(FakeES.last).toBeUndefined();
});

test("a stream that dies without replay_end says the archive or database is unavailable", async () => {
  mount((u, i) => (i?.method === "POST" ? Promise.resolve(json(201, { replay_id: "r1" })) : vehicles(u)));
  await fillAndStart();
  const es = FakeES.last!;
  es.emit("open");
  es.emit("error");
  expect(es.closed).toBe(true); // no reconnect, so no misleading 404
  expect(host.textContent).toContain("Replay stopped: archive or database unavailable – start it again");
});

test("replay alarms show without an ACK button", async () => {
  mount((u, i) => (i?.method === "POST" ? Promise.resolve(json(201, { replay_id: "r1" })) : vehicles(u)));
  await fillAndStart();
  const es = FakeES.last!;
  es.emit("replay_start", { kind: "replay_start", replay: true, replay_id: "r1", vehicle: "NYX-TA1", t_from: "2026-11-10T14:00:00Z", t_to: "2026-11-10T14:20:00Z", speed: 1, alarms: [alarmView()], los_open: false });
  expect(host.textContent).toContain("UNACKNOWLEDGED");
  expect(host.querySelector("button[aria-label^=Acknowledge]")).toBeNull();
  es.emit("replay_end", { kind: "replay_end", replay: true, reason: "done", detail: null });
  expect(host.textContent).toContain("Replay finished");
  expect(es.closed).toBe(true);
});

test("Stop deletes the replay", async () => {
  const calls = mount((u, i) => (i?.method === "POST" ? Promise.resolve(json(201, { replay_id: "r1" })) : i?.method === "DELETE" ? Promise.resolve(new Response(null, { status: 204 })) : vehicles(u)));
  await fillAndStart();
  act(() => button("Stop").click());
  await flush();
  expect(calls).toContain("DELETE /api/replays/r1");
});
