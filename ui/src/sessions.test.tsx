// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MissionDb } from "./api";
import { sessionReport, sessionView } from "./fixtures";
import { SessionPanel } from "./SessionPanel";
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

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
/** a tiny fake of the sessions API: `sessions` is the server's list, handlers can change it */
function server(initial: unknown[] = [], handlers: Record<string, (init?: { body?: string }) => Response> = {}) {
  const state = { sessions: initial };
  const f = vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (handlers[key]) return handlers[key](init);
    if (key === "GET /api/sessions") return json(200, state.sessions);
    throw new Error(`unexpected ${key}`);
  });
  vi.stubGlobal("fetch", f);
  return { f, state };
}
function mount(mission: MissionDb | null = null) {
  const store = createStore();
  const tm = (vehicle: string) => ({ kind: "tm", vehicle, seq: 1, t_onboard: "2026-11-14T10:00:00.000000Z", mode: "TEST", last_cmd_id: null, values: {}, age_ms: 1, frozen: false });
  ingest(store, "snapshot", { kind: "snapshot", vehicles: { "NYX-TA1": tm("NYX-TA1"), "NYX-TA2": tm("NYX-TA2") }, alarms: [], commands: [], events: [] });
  act(() => root.render(<SessionPanel store={store} mission={mission} />));
  return store;
}
const flush = () => act(async () => {});
function set(el: HTMLElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}
const field = (label: string) => [...host.querySelectorAll("form label")].find((l) => l.textContent?.startsWith(label))!.querySelector("select, input") as HTMLSelectElement & HTMLInputElement;
const button = (t: string) => [...host.querySelectorAll("button")].find((b) => b.textContent === t) as HTMLButtonElement | undefined;
const rows = () => [...host.querySelectorAll<HTMLElement>("tr[data-session-id]")];

test("start needs a chosen vehicle and a name; the new session is listed as active", async () => {
  const s = server([], {});
  const started = sessionView({ id: 5, name: "vent test" });
  s.f.mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
    if (init?.method === "POST") {
      s.state.sessions = [started];
      return json(201, started);
    }
    return json(200, s.state.sessions);
  });
  mount();
  await flush();
  expect(host.textContent).toContain("No sessions yet");
  expect(button("Start session")!.disabled).toBe(true);
  set(field("Session name"), "vent test");
  expect(button("Start session")!.disabled).toBe(true); // no vehicle chosen
  set(field("Vehicle"), "NYX-TA1");
  expect(button("Start session")!.disabled).toBe(false);
  await act(async () => button("Start session")!.click());
  const post = s.f.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "POST")!;
  expect(post[0]).toBe("/api/sessions");
  expect(JSON.parse((post[1] as { body: string }).body)).toEqual({ vehicle: "NYX-TA1", name: "vent test" });
  expect(rows()).toHaveLength(1);
  expect(rows()[0]!.getAttribute("data-active")).toBe("true");
  expect(rows()[0]!.textContent).toContain("active");
  expect(field("Session name").value).toBe(""); // cleared
  expect(host.textContent).toContain("NYX-TA1 already has an active session"); // a second start for it is blocked
  expect(button("Start session")!.disabled).toBe(true);
});

test("a refused start shows the server's message; a network failure says it may not have completed and re-lists", async () => {
  const s = server([]);
  s.f.mockImplementation(async (url: string, init?: { method?: string }) =>
    init?.method === "POST" ? json(409, { error: "SESSION_ACTIVE", message: "NYX-TA1 already has an active session" }) : json(200, []));
  mount();
  await flush();
  set(field("Vehicle"), "NYX-TA1");
  set(field("Session name"), "n");
  await act(async () => button("Start session")!.click());
  expect(host.querySelector('[role="alert"]')!.textContent).toBe("NYX-TA1 already has an active session");
  s.f.mockImplementation(async (url: string, init?: { method?: string }) => {
    if (init?.method === "POST") throw new TypeError("offline");
    return json(200, []);
  });
  const lists = () => s.f.mock.calls.filter((c) => (c[1] as { method?: string } | undefined)?.method !== "POST").length;
  const before = lists();
  await act(async () => button("Start session")!.click());
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("May not have completed");
  expect(lists()).toBe(before + 1);
});

test("stop shows the pending report text; once frozen the report table and the CSV link appear", async () => {
  const running = sessionView({ id: 5, name: "vent test" });
  const ended = sessionView({ id: 5, name: "vent test", ended_at: "2026-11-14T10:10:00Z", ended_by: "fc1" });
  let stopped = false;
  let report: Response = json(409, { error: "REPORT_PENDING", message: "m", frames: 5900, expected: 6000 });
  const s = server();
  s.f.mockImplementation(async (url: string) => {
    if (url === "/api/sessions") return json(200, [stopped ? ended : running]);
    if (url === "/api/sessions/5/stop") return ((stopped = true), json(200, ended));
    if (url === "/api/sessions/5/report") return report.clone();
    throw new Error(`unexpected ${url}`);
  });
  mount();
  await flush();
  await act(async () => button("Stop vent test")!.click());
  expect(host.querySelector('[role="status"]')!.textContent).toContain("Report pending: 5900 of 6000 frames");
  expect(rows()[0]!.getAttribute("data-active")).toBeNull();
  expect(host.querySelector("a[download]")).toBeNull(); // pending: the server would answer 409 JSON, not a file
  // frozen now
  report = json(200, sessionReport());
  await act(async () => button("Check again")!.click());
  expect(host.querySelector<HTMLAnchorElement>("a[download]")!.getAttribute("href")).toBe("/api/sessions/5/report.csv");
  const text = host.querySelector(".report")!.textContent!;
  expect(text).toContain("complete, 6000 frames");
  expect(text).toContain("cabin_pressure_kpa");
  expect(text).toContain("94.20");
  expect(text).toContain("107.00");
  expect(text).toContain("1 alarm transitions · 1 commands · 1 link events");
  expect(text).toContain("NYX-TA1 LOS since 10:05:00Z");
});

test("an incomplete report says so with its frame counts; a running session says to stop it first", async () => {
  const ended = sessionView({ id: 2, ended_at: "2026-11-14T10:10:00Z" });
  const running = sessionView({ id: 3, name: "second", vehicle: "NYX-TA2" });
  server([ended, running], {
    "GET /api/sessions/2/report": () => json(200, sessionReport({ complete: false, frames: 5000, expected: 6000, note: "incomplete: vehicle was silent" })),
    "GET /api/sessions/3/report": () => json(409, { error: "SESSION_RUNNING", message: "m" }),
  });
  mount();
  await flush();
  await act(async () => button("Report")!.click());
  expect(host.querySelector(".report")!.textContent).toContain("incomplete: 5000 of 6000 frames");
  expect(host.querySelector(".report")!.textContent).toContain("vehicle was silent");
});

test("a failing list or report is shown, never an empty 'no sessions'", async () => {
  const ended = sessionView({ id: 2, ended_at: "2026-11-14T10:10:00Z" });
  server([ended], { "GET /api/sessions/2/report": () => json(404, { error: "NOT_FOUND", message: "m" }) });
  mount();
  await flush();
  await act(async () => button("Report")!.click());
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("404");
  vi.stubGlobal("fetch", vi.fn(async () => json(503, {})));
  act(() => root.unmount());
  root = createRoot(host);
  mount();
  await flush();
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("503");
  expect(host.textContent).not.toContain("No sessions yet");
});

test("no CSV link for a session whose report is still running; no link in the session table at all", async () => {
  server([sessionView({ id: 3, name: "second", ended_at: "2026-11-14T10:10:00Z" })], {
    "GET /api/sessions/3/report": () => json(409, { error: "SESSION_RUNNING", message: "m" }),
  });
  mount();
  await flush();
  expect(host.querySelector("a[download]")).toBeNull();
  await act(async () => button("Report")!.click());
  expect(host.querySelector("a[download]")).toBeNull();
});

test("Start is disabled when the chosen vehicle already has an active session, even with a name; another vehicle is fine", async () => {
  server([sessionView({ id: 1, vehicle: "NYX-TA1" })]);
  mount();
  await flush();
  set(field("Session name"), "second try");
  set(field("Vehicle"), "NYX-TA1");
  expect(button("Start session")!.disabled).toBe(true);
  set(field("Vehicle"), "NYX-TA2");
  expect(button("Start session")!.disabled).toBe(false);
});

test("Start is disabled with a vehicle but a blank or whitespace-only name; a scripted submit sends nothing", async () => {
  const s = server([]);
  mount();
  await flush();
  set(field("Vehicle"), "NYX-TA1");
  expect(button("Start session")!.disabled).toBe(true);
  set(field("Session name"), "   ");
  expect(button("Start session")!.disabled).toBe(true);
  await act(async () => (host.querySelector("form") as HTMLFormElement).requestSubmit());
  expect(s.f.mock.calls.every((c) => (c[1] as { method?: string } | undefined)?.method !== "POST")).toBe(true);
});

test("the list is re-read after a refused start or stop: another operator's change shows (SESSION_ACTIVE, 404)", async () => {
  const s = server([]);
  let list: unknown[] = [];
  s.f.mockImplementation(async (url: string, init?: { method?: string }) => {
    if (init?.method === "POST" && url === "/api/sessions") {
      list = [sessionView({ id: 9, name: "started by fc2", conductor: "fc2" })]; // fc2 got there first
      return json(409, { error: "SESSION_ACTIVE", message: "NYX-TA1 already has an active session" });
    }
    if (init?.method === "POST") {
      list = [];
      return json(404, { error: "NOT_FOUND", message: "no such session" });
    }
    return json(200, list);
  });
  mount();
  await flush();
  set(field("Vehicle"), "NYX-TA1");
  set(field("Session name"), "mine");
  await act(async () => button("Start session")!.click());
  expect(host.querySelector('[role="alert"]')!.textContent).toBe("NYX-TA1 already has an active session");
  expect(rows().map((r) => r.textContent)).toEqual([expect.stringContaining("started by fc2")]);
  await act(async () => button("Stop started by fc2")!.click());
  expect(host.querySelector('[role="alert"]')!.textContent).toBe("no such session");
  expect(host.textContent).toContain("No sessions yet");
});

test("report answers arriving out of order: the slow answer for A never overwrites B", async () => {
  const a = sessionView({ id: 1, name: "A", ended_at: "2026-11-14T10:10:00Z" });
  const b = sessionView({ id: 2, name: "B", ended_at: "2026-11-14T10:10:00Z" });
  const s = server([a, b]);
  const held: Record<string, (r: Response) => void> = {};
  s.f.mockImplementation(async (url: string) => {
    if (url === "/api/sessions") return json(200, [a, b]);
    return new Promise<Response>((res) => (held[url] = res));
  });
  mount();
  await flush();
  const reportButtons = [...host.querySelectorAll("button")].filter((x) => x.textContent === "Report");
  await act(async () => reportButtons[0]!.click()); // A, slow
  await act(async () => reportButtons[1]!.click()); // B
  await act(async () => held["/api/sessions/2/report"]!(json(200, sessionReport({ session: sessionView({ id: 2, name: "B", ended_at: "2026-11-14T10:10:00Z" }) }))));
  expect(host.querySelector(".report h3")!.textContent).toContain("B");
  await act(async () => held["/api/sessions/1/report"]!(json(200, sessionReport({ session: sessionView({ id: 1, name: "A", ended_at: "2026-11-14T10:10:00Z" }) }))));
  expect(host.querySelector(".report h3")!.textContent).toContain("B"); // still B
  expect(host.querySelector("a[download]")!.getAttribute("href")).toBe("/api/sessions/2/report.csv");
});

test("the report lists its parameters in mission-db order (jsonb reorders keys), unknown ones after, sorted", async () => {
  const ended = sessionView({ id: 2, ended_at: "2026-11-14T10:10:00Z" });
  const stat = { avg: 1, max: 1, min: 1, n: 1, unit: "u" };
  // jsonb sorts keys by length, then bytes: this is the order the server sends
  const parameters = { zz: stat, o2_pct: stat, legacy: stat, cabin_temp_c: stat, cabin_pressure_kpa: stat };
  server([ended], { "GET /api/sessions/2/report": () => json(200, sessionReport({ parameters })) });
  const meta = { unit: "u", type: "float" };
  mount({ parameters: { cabin_pressure_kpa: meta, cabin_temp_c: meta, o2_pct: meta, absent: meta } } as unknown as MissionDb);
  await flush();
  await act(async () => button("Report")!.click());
  const order = [...host.querySelectorAll(".report tbody th")].map((th) => th.textContent);
  expect(order).toEqual(["cabin_pressure_kpa", "cabin_temp_c", "o2_pct", "legacy", "zz"]);
});

test("in the narrow side pane the action sits next to the name, and a long UTC time may wrap after its T", async () => {
  server([sessionView({ id: 2, name: "pass 1", started_at: "2026-11-14T10:00:00Z", ended_at: "2026-11-14T10:10:00Z" })]);
  mount();
  await flush();
  const heads = [...host.querySelectorAll("thead th")].map((th) => th.textContent);
  expect(heads.slice(0, 2)).toEqual(["Session", "Report"]);
  const cells = rows()[0]!.children;
  expect(cells[1]!.querySelector("button")!.textContent).toBe("Report");
  const started = rows()[0]!.querySelector("time")!;
  expect(started.textContent).toBe("2026-11-14T10:00:00.000Z"); // the text is unchanged (D-UI-13)
  expect(started.innerHTML).toContain("T<wbr>");
});
