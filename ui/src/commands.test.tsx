// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { MissionDb } from "./api";
import { CommandPanel } from "./CommandPanel";
import { commandView } from "./fixtures";
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
  vi.useRealTimers();
});

const MDB = {
  commands: {
    isolate_valve: { description: "Close the cabin isolation valve", hazardous: false, args: { valve: { type: "enum", values: ["A", "B"], required: true } }, receive_timeout_s: 5, verify_timeout_s: 15 },
    vent_cabin: { description: "Open the cabin vent valve", hazardous: true, hazard: "Irreversible loss of cabin atmosphere", args: { duration_s: { type: "float", min: 1, max: 10, unit: "s", required: true } }, receive_timeout_s: 5, verify_timeout_s: 15 },
  },
  parameters: {},
  limits: {},
} as unknown as MissionDb;

function mount() {
  const store = createStore();
  const tm = (vehicle: string) => ({ kind: "tm", vehicle, seq: 1, t_onboard: "2026-11-14T10:00:00.000000Z", mode: "TEST", last_cmd_id: null, values: {}, age_ms: 1, frozen: false });
  ingest(store, "snapshot", { kind: "snapshot", vehicles: { "NYX-TA1": tm("NYX-TA1"), "NYX-TA2": tm("NYX-TA2") }, alarms: [], commands: [], events: [] });
  act(() => root.render(<CommandPanel store={store} mission={MDB} />));
  return store;
}

type Reply = { status: number; body: unknown };
/** POST /api/commands answered in order; GET /api/commands answered with `list`. */
function stubApi(posts: Reply[], list: unknown[] = []) {
  const queue = [...posts];
  const f = vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
    if (init?.method === "POST") {
      const r = queue.shift();
      if (!r) throw new Error("unexpected POST");
      return new Response(JSON.stringify(r.body), { status: r.status });
    }
    return new Response(JSON.stringify(list), { status: 200 });
  });
  vi.stubGlobal("fetch", f);
  return f;
}
const posted = (f: ReturnType<typeof stubApi>) =>
  f.mock.calls.filter((c) => (c[1] as { method?: string } | undefined)?.method === "POST").map((c) => JSON.parse((c[1] as { body: string }).body));

const panelSelect = (label: string) => [...host.querySelectorAll("form label")].find((l) => l.textContent?.startsWith(label))!.querySelector(
  "select, input",
) as HTMLSelectElement & HTMLInputElement;
function set(el: HTMLElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}
const send = () => [...host.querySelectorAll<HTMLButtonElement>("form button")].find((b) => b.textContent === "Send")!;
const dlg = (t: string) => [...host.querySelectorAll<HTMLButtonElement>("dialog button")].find((b) => b.textContent === t)!;
const dialogField = (label: string) => [...host.querySelectorAll("dialog label")].find((l) => l.textContent?.startsWith(label))!.querySelector("input, textarea") as HTMLInputElement;
const alert = () => host.querySelector('[role="alert"]')?.textContent;

test("typed argument inputs come from the mission db: enum select, number input with its limits and unit", () => {
  mount();
  set(panelSelect("Command"), "isolate_valve");
  expect([...panelSelect("valve").options].map((o) => o.value)).toEqual(["", "A", "B"]);
  set(panelSelect("Command"), "vent_cabin");
  const n = panelSelect("duration_s");
  expect([n.type, n.min, n.max, n.step]).toEqual(["number", "1", "10", "any"]);
  expect(host.querySelector(".unit")!.textContent).toBe("s");
  expect(host.querySelector(".desc")!.textContent).toContain("hazardous: Irreversible loss of cabin atmosphere");
});

test("Send stays disabled until every required argument has a value", () => {
  mount();
  expect(send().disabled).toBe(true);
  set(panelSelect("Command"), "isolate_valve");
  expect(send().disabled).toBe(true);
  set(panelSelect("valve"), "A");
  expect(send().disabled).toBe(false);
});

test("a plain command: posted with the chosen vehicle, then the list is fetched so QUEUED shows at once", async () => {
  const store = mount();
  const row = commandView({ cmd_id: "c9", name: "isolate_valve", args: { valve: "A" }, vehicle: "NYX-TA2" });
  const f = stubApi([{ status: 202, body: { cmd_id: "c9" } }], [row]);
  set(panelSelect("Vehicle"), "NYX-TA2");
  set(panelSelect("Command"), "isolate_valve");
  set(panelSelect("valve"), "A");
  await act(async () => send().click());
  expect(posted(f)).toEqual([{ vehicle: "NYX-TA2", name: "isolate_valve", args: { valve: "A" } }]);
  expect(f).toHaveBeenCalledWith("/api/commands?vehicle=NYX-TA2", { credentials: "same-origin" });
  expect(store.commands()).toHaveLength(1);
  expect(host.querySelector('li[data-cmd-id="c9"]')!.textContent).toContain("isolate_valve (valve = A)");
  expect(host.querySelector('[role="status"]')!.textContent).toBe("Command queued for NYX-TA2");
});

test("a failing list refresh is logged, not fatal", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  mount();
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init?: { method?: string }) =>
    init?.method === "POST" ? new Response(JSON.stringify({ cmd_id: "c" }), { status: 202 }) : new Response(null, { status: 503 })));
  set(panelSelect("Command"), "isolate_valve");
  set(panelSelect("valve"), "B");
  await act(async () => send().click());
  expect(warn).toHaveBeenCalledOnce();
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  warn.mockRestore();
});

test("INVALID_ARGS and DUPLICATE_COMMAND show inline", async () => {
  mount();
  stubApi([
    { status: 422, body: { error: "INVALID_ARGS", message: "duration_s: 11 is outside [1.0, 10.0]" } },
    { status: 409, body: { error: "DUPLICATE_COMMAND", message: "dup", by: "fc2", seconds_ago: 1, cmd_id: "c0" } },
  ]);
  set(panelSelect("Command"), "isolate_valve");
  set(panelSelect("valve"), "A");
  await act(async () => send().click());
  expect(alert()).toBe("duration_s: 11 is outside [1.0, 10.0]");
  await act(async () => send().click());
  expect(alert()).toBe("fc2 sent the same command 1 s ago");
});

async function hazardous(extra: Reply[] = []) {
  const store = mount();
  const f = stubApi([{ status: 409, body: { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "m", hazard: "Irreversible loss of cabin atmosphere", confirm_token: "tok1", expires_in_s: 60 } }, ...extra]);
  set(panelSelect("Vehicle"), "NYX-TA2");
  set(panelSelect("Command"), "vent_cabin");
  set(panelSelect("duration_s"), "3");
  await act(async () => send().click());
  return { store, f };
}

test("a hazardous command opens the confirm dialog: it shows what will be sent and needs the typed vehicle id and a reason", async () => {
  await hazardous();
  const d = host.querySelector("dialog")!;
  expect(d.open).toBe(true);
  expect(d.textContent).toContain("NYX-TA2 vent_cabin (duration_s = 3)");
  expect(d.textContent).toContain("Irreversible loss of cabin atmosphere");
  expect(d.textContent).toContain("Type NYX-TA2 to confirm");
  expect(dlg("Confirm").disabled).toBe(true);
  set(dialogField("Reason"), "venting now");
  for (const wrong of ["NYX-TA1", "nyx-ta2", "NYX-TA2 ", "NYX-TA"]) {
    set(dialogField("Type"), wrong);
    expect(dlg("Confirm").disabled).toBe(true);
  }
  set(dialogField("Type"), "NYX-TA2");
  expect(dlg("Confirm").disabled).toBe(false);
  set(dialogField("Reason"), "too short");
  expect(dlg("Confirm").disabled).toBe(true);
});

test("confirm posts the same body with the token and the reason, then closes and refreshes", async () => {
  const { f, store } = await hazardous([{ status: 202, body: { cmd_id: "c5" } }]);
  set(dialogField("Reason"), "  venting now  ");
  set(dialogField("Type"), "NYX-TA2");
  await act(async () => dlg("Confirm").click());
  expect(posted(f)).toEqual([
    { vehicle: "NYX-TA2", name: "vent_cabin", args: { duration_s: 3 } },
    { vehicle: "NYX-TA2", name: "vent_cabin", args: { duration_s: 3 }, confirm_token: "tok1", reason: "venting now" },
  ]);
  expect(host.querySelector("dialog")).toBeNull();
  expect(host.querySelector('[role="status"]')!.textContent).toBe("Command queued for NYX-TA2");
  expect(store.commands()).toEqual([]); // the stub list is empty: nothing invented
});

test("the reason typed in the panel is carried into the dialog", async () => {
  const store = mount();
  stubApi([{ status: 409, body: { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "m", hazard: "h", confirm_token: "t", expires_in_s: 60 } }]);
  set(panelSelect("Command"), "vent_cabin");
  set(panelSelect("duration_s"), "3");
  set(panelSelect("Reason"), "planned vent test");
  await act(async () => send().click());
  expect(dialogField("Reason").value).toBe("planned vent test");
  expect(store.commands()).toEqual([]);
});

test("CONFIRMATION_INVALID shows inline and the token is not tried again", async () => {
  await hazardous([{ status: 409, body: { error: "CONFIRMATION_INVALID", message: "Confirmation token expired or already used" } }]);
  set(dialogField("Reason"), "venting now");
  set(dialogField("Type"), "NYX-TA2");
  await act(async () => dlg("Confirm").click());
  expect(host.querySelector("dialog [role=alert]")!.textContent).toBe("Confirmation token expired or already used");
  expect(dlg("Confirm").disabled).toBe(true);
  act(() => dlg("Cancel").click());
  expect(host.querySelector("dialog")).toBeNull();
});

test("REASON_REQUIRED keeps the dialog usable (the token is not consumed)", async () => {
  await hazardous([{ status: 422, body: { error: "REASON_REQUIRED", message: "reason must be at least 10 characters" } }]);
  set(dialogField("Reason"), "venting now");
  set(dialogField("Type"), "NYX-TA2");
  await act(async () => dlg("Confirm").click());
  expect(host.querySelector("dialog [role=alert]")!.textContent).toBe("reason must be at least 10 characters");
  expect(dlg("Confirm").disabled).toBe(false);
});

test("a second confirm request from the server, or a dead dialog after its expiry, is told to start over", async () => {
  await hazardous([{ status: 409, body: { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "m", hazard: "h", confirm_token: "tok2", expires_in_s: 60 } }]);
  set(dialogField("Reason"), "venting now");
  set(dialogField("Type"), "NYX-TA2");
  await act(async () => dlg("Confirm").click());
  expect(host.querySelector("dialog [role=alert]")!.textContent).toContain("cancel and send the command again");
  expect(dlg("Confirm").disabled).toBe(true);
});

test("the dialog expires after expires_in_s with one timer", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  await hazardous();
  set(dialogField("Reason"), "venting now");
  set(dialogField("Type"), "NYX-TA2");
  expect(dlg("Confirm").disabled).toBe(false);
  act(() => void vi.advanceTimersByTime(60_000));
  expect(host.querySelector("dialog [role=alert]")!.textContent).toContain("expired");
  expect(dlg("Confirm").disabled).toBe(true);
});

test("the timeline shows the four state times, a dash for one not reached", () => {
  const store = mount();
  act(() => ingest(store, "cmd", { kind: "cmd", command: commandView({ cmd_id: "c1", state: "RECEIVED", sent_at: "2026-11-14T10:00:01Z", received_at: "2026-11-14T10:00:02Z" }) }));
  const li = host.querySelector('li[data-cmd-id="c1"]')!;
  expect([...li.querySelectorAll("ol li")].map((x) => x.textContent)).toEqual(["QUEUED 10:00:00Z", "SENT 10:00:01Z", "RECEIVED 10:00:02Z", "VERIFIED —"]);
  expect(li.querySelector("[data-failed]")).toBeNull();
});

test("a failed command shows its reason and what the operator may do", () => {
  const store = mount();
  act(() => ingest(store, "cmd", { kind: "cmd", command: commandView({ cmd_id: "c2", state: "FAILED", failure_reason: "NOT_VERIFIED", failed_at: "2026-11-14T10:00:20Z", outcome: "EXECUTED_LATE", late_verified_at: "2026-11-14T10:00:25Z" }) }));
  const li = host.querySelector('li[data-cmd-id="c2"]')!;
  expect(li.querySelector("[data-failed]")!.textContent).toContain("NOT_VERIFIED");
  expect(li.querySelector(".outcome")!.textContent).toBe("FAILED (no verification in 15 s) – vehicle executed it at 10:00:25Z – do NOT resend");
});

test("without the mission db the panel waits instead of offering commands", () => {
  act(() => root.render(<CommandPanel store={createStore()} mission={null} />));
  expect(host.textContent).toContain("Loading the mission database");
  expect(host.querySelector("form")).toBeNull();
});
