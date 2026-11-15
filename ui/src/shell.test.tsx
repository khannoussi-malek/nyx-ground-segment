// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { alarmView, eventView } from "./fixtures";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { Login } from "./Login";

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

/** fetch answered per path; unknown paths fail the test loudly */
function stubApi(routes: Record<string, { status: number; body?: unknown }>) {
  const f = vi.fn(async (url: string) => {
    const r = routes[url];
    if (!r) throw new Error(`unexpected fetch ${url}`);
    return new Response(r.body === undefined ? null : JSON.stringify(r.body), { status: r.status });
  });
  vi.stubGlobal("fetch", f);
  return f;
}

class FakeEventSource {
  static opened: string[] = [];
  constructor(url: string) {
    FakeEventSource.opened.push(url);
  }
  addEventListener() {}
  close() {}
}

const button = (t: string) => [...host.querySelectorAll("button")].find((b) => b.textContent === t);
const buttonText = (t: string) => button(t)?.textContent;

async function submitLogin(user: string, password: string) {
  const input = (name: string) => host.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
  input("user").value = user;
  input("password").value = password;
  await act(async () => host.querySelector("form")!.requestSubmit());
}

test("Login: a right password calls onLogin with the user", async () => {
  stubApi({ "/api/login": { status: 204 } });
  const onLogin = vi.fn();
  act(() => root.render(<Login onLogin={onLogin} />));
  await submitLogin("fc1", "pw");
  expect(onLogin).toHaveBeenCalledWith("fc1", null); // no AudioContext in this environment
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

test("Login: a wrong password shows the alert and stays on the form", async () => {
  stubApi({ "/api/login": { status: 401, body: { error: "LOGIN_FAILED", message: "x" } } });
  const onLogin = vi.fn();
  act(() => root.render(<Login onLogin={onLogin} />));
  await submitLogin("fc1", "bad");
  expect(onLogin).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')?.textContent).toBe("Wrong user or password");
});

test("App: logged out shows the login form", async () => {
  stubApi({ "/api/me": { status: 401, body: { error: "UNAUTHENTICATED", message: "x" } } });
  await act(async () => root.render(<App />));
  expect(host.querySelector('button[type="submit"]')?.textContent).toBe("Log in");
  expect(host.querySelector("header")).toBeNull();
});

test("App: logged in shows the console, opens the event stream and loads the mission db", async () => {
  vi.stubGlobal("EventSource", FakeEventSource);
  FakeEventSource.opened = [];
  const f = stubApi({
    "/api/me": { status: 200, body: { user: "fc1" } },
    "/api/mission-db": { status: 200, body: { parameters: {}, limits: {}, commands: {} } },
  });
  await act(async () => root.render(<App />));
  expect(host.querySelector("header span")?.textContent).toBe("fc1");
  expect(buttonText("Log out")).toBe("Log out");
  expect(host.querySelector("main")).not.toBeNull();
  expect(host.querySelector('section[aria-label="Alarms"]')).not.toBeNull(); // mounted (P-pf10)
  expect(host.querySelector('section[aria-label="Commands"]')).not.toBeNull();
  expect(host.querySelector('section[aria-label="Events"]')).not.toBeNull();
  expect(host.querySelector('section[aria-label="Chart"]')).not.toBeNull();
  expect(host.querySelector('section[aria-label="Sessions"]')).not.toBeNull();
  expect(FakeEventSource.opened).toEqual(["/api/events"]);
  expect(f).toHaveBeenCalledWith("/api/mission-db", { credentials: "same-origin" });
});

test("App: Log out returns to the login form", async () => {
  vi.stubGlobal("EventSource", FakeEventSource);
  stubApi({
    "/api/me": { status: 200, body: { user: "fc1" } },
    "/api/mission-db": { status: 200, body: { parameters: {}, limits: {}, commands: {} } },
    "/api/logout": { status: 204 },
  });
  await act(async () => root.render(<App />));
  await act(async () => button("Log out")!.click());
  expect(host.querySelector('button[type="submit"]')?.textContent).toBe("Log in");
});

// ---- audio (Task 14) ----
class LiveES {
  static all: LiveES[] = [];
  listeners = new Map<string, (e: { data: string }) => void>();
  constructor() {
    LiveES.all.push(this);
  }
  addEventListener(k: string, fn: (e: { data: string }) => void) {
    this.listeners.set(k, fn);
  }
  close() {}
  emit(kind: string, data: unknown) {
    act(() => this.listeners.get(kind)!({ data: JSON.stringify(data) }));
  }
}
const played: number[] = [];
class FakeAudio {
  static states: FakeAudio[] = [];
  static startState = "running";
  static failResume = false;
  closed = false;
  state = FakeAudio.startState;
  listeners: (() => void)[] = [];
  currentTime = 0;
  destination = {};
  constructor() {
    FakeAudio.states.push(this);
  }
  close() {
    this.state = "closed";
    this.listeners.forEach((f) => f());
    return Promise.resolve();
  }
  resume() {
    if (FakeAudio.failResume) return Promise.reject(new Error("no"));
    return Promise.resolve().then(() => {
      this.state = "running";
      this.listeners.forEach((f) => f());
    });
  }
  addEventListener(_: string, f: () => void) {
    this.listeners.push(f);
  }
  removeEventListener(_: string, f: () => void) {
    this.listeners = this.listeners.filter((x) => x !== f);
  }
  createGain() {
    return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {} }, connect: (n: unknown) => n };
  }
  createOscillator() {
    const audible = this.state === "running"; // a suspended context makes no sound
    const o = { frequency: { set value(v: number) { if (audible) played.push(v); } }, connect: (n: unknown) => n, start() {}, stop() {} };
    return o;
  }
}
const tmFrame = { kind: "tm", vehicle: "NYX-TA1", seq: 1, t_onboard: "2026-11-14T10:00:00.000000Z", mode: "TEST", last_cmd_id: null, values: { p: 1 }, age_ms: 10, frozen: false };
const snapshot = (alarms: unknown[] = [], events: unknown[] = []) => ({ kind: "snapshot", vehicles: { "NYX-TA1": tmFrame }, alarms, commands: [], events });

async function liveConsole() {
  vi.stubGlobal("EventSource", LiveES);
  vi.stubGlobal("AudioContext", FakeAudio);
  LiveES.all = [];
  played.length = 0;
  FakeAudio.startState = "running";
  FakeAudio.failResume = false;
  FakeAudio.states = [];
  sessionStorage.clear();
  stubApi({
    "/api/me": { status: 200, body: { user: "fc1" } },
    "/api/mission-db": { status: 200, body: { parameters: {}, limits: {}, commands: {} } },
  });
  await act(async () => root.render(<App />));
  return LiveES.all[0]!;
}
const attr = (n: string) => host.firstElementChild!.getAttribute(n);

test("App: SOUND OFF badge until the audio runs; the click arms it", async () => {
  await liveConsole();
  expect(buttonText("SOUND OFF – click to enable")).toBeDefined();
  await act(async () => button("SOUND OFF – click to enable")!.click());
  expect(FakeAudio.states).toHaveLength(1);
  expect(button("SOUND OFF – click to enable")).toBeUndefined(); // running: badge gone
});

test("Login arms the audio inside the click and hands it to onLogin", async () => {
  vi.stubGlobal("AudioContext", FakeAudio);
  FakeAudio.states = [];
  stubApi({ "/api/login": { status: 204 } });
  const onLogin = vi.fn();
  act(() => root.render(<Login onLogin={onLogin} />));
  await submitLogin("fc1", "pw");
  expect(onLogin).toHaveBeenCalledWith("fc1", FakeAudio.states[0]);
});

test("App: a RED alert key beeps once, also when a resync repeats it and after a reload", async () => {
  const es = await liveConsole();
  await act(async () => button("SOUND OFF – click to enable")!.click());
  es.emit("snapshot", snapshot([alarmView()]));
  expect(attr("data-beeps")).toBe("1");
  expect(played).toEqual([880]);
  es.emit("snapshot", snapshot([alarmView()])); // D-UI-18 resync
  es.emit("alarm", { kind: "alarm", alarm: alarmView() });
  expect(attr("data-beeps")).toBe("1");
  es.emit("alarm", { kind: "alarm", alarm: alarmView({ last: { ...alarmView().last, key: "k2" } }) });
  expect(attr("data-beeps")).toBe("2");
  act(() => root.unmount());
  root = createRoot(host); // reload: new console, same sessionStorage
  await act(async () => root.render(<App />));
  LiveES.all.at(-1)!.emit("snapshot", snapshot([alarmView()]));
  expect(attr("data-beeps")).toBe("0");
});

test("App: LOS and BROKER sound one quieter tone each, keyed for the tests", async () => {
  const es = await liveConsole();
  await act(async () => button("SOUND OFF – click to enable")!.click());
  es.emit("snapshot", snapshot());
  expect(attr("data-last-tone")).toBe("");
  es.emit("link", { kind: "link", event: eventView("LOS/NYX-TA1/1/7", { kind: "LOS", vehicle: "NYX-TA1" }) });
  expect(attr("data-last-tone")).toBe("LOS:LOS/NYX-TA1/1/7");
  expect(played).toEqual([440]);
  es.emit("link", { kind: "link", event: eventView("AOS/NYX-TA1/1/8", { kind: "AOS", vehicle: "NYX-TA1" }) });
  es.emit("ground", { kind: "ground", broker: "down" });
  expect(attr("data-last-tone")).toBe("BROKER:*|BROKER");
});

test("App: 3 s of silence sounds NO LIVE CONNECTION once (fake timers)", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    const es = await liveConsole();
  await act(async () => button("SOUND OFF – click to enable")!.click());
    es.emit("snapshot", snapshot());
    act(() => void vi.advanceTimersByTime(2999));
    expect(attr("data-last-tone")).toBe("");
    act(() => void vi.advanceTimersByTime(1));
    expect(attr("data-last-tone")).toBe("NLC:*|NLC");
  } finally {
    vi.useRealTimers();
  }
});

test("App: a throttled background tab still sounds NO LIVE CONNECTION when it is shown again", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }); // the silence timer never fires here
  let now = 1000;
  const spy = vi.spyOn(performance, "now").mockImplementation(() => now);
  try {
    const es = await liveConsole();
  await act(async () => button("SOUND OFF – click to enable")!.click());
    es.emit("snapshot", snapshot());
    now = 9000;
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    expect(attr("data-last-tone")).toBe("NLC:*|NLC");
  } finally {
    spy.mockRestore();
    vi.useRealTimers();
  }
});

const arm = () => act(async () => button("SOUND OFF – click to enable")!.click());
const LOS = eventView("LOS/NYX-TA1/1/7", { kind: "LOS", vehicle: "NYX-TA1" });

test("a RED onset while the audio is off is not lost: arming sounds exactly one beep (P-pf23)", async () => {
  const es = await liveConsole();
  es.emit("snapshot", snapshot([alarmView()]));
  expect([attr("data-beeps"), played]).toEqual(["0", []]); // pending, not marked
  await arm();
  expect([attr("data-beeps"), played]).toEqual(["1", [880]]);
  es.emit("snapshot", snapshot([alarmView()])); // resync: no second sound
  expect(played).toEqual([880]);
});

test("a LOS onset while the audio is off sounds one tone on arming; a suspended context counts as off", async () => {
  FakeAudio.startState = "suspended";
  const es = await liveConsole();
  es.emit("snapshot", snapshot([], [LOS]));
  expect([attr("data-last-tone"), played]).toEqual(["", []]);
  FakeAudio.startState = "suspended"; // armAudio's context starts suspended and resumes asynchronously
  await arm();
  expect([attr("data-last-tone"), played]).toEqual(["LOS:LOS/NYX-TA1/1/7", [440]]);
});

test("arming with both open sounds one beep and one tone, never twice", async () => {
  const es = await liveConsole();
  es.emit("snapshot", snapshot([alarmView()], [LOS]));
  await arm();
  expect(played).toEqual([880, 440]);
});

test("an RED already beeped before a reload still beeps once when the reloaded console is armed", async () => {
  const es = await liveConsole();
  await arm();
  es.emit("snapshot", snapshot([alarmView()]));
  expect(played).toEqual([880]);
  act(() => root.unmount());
  root = createRoot(host);
  played.length = 0;
  await act(async () => root.render(<App />));
  LiveES.all.at(-1)!.emit("snapshot", snapshot([alarmView()]));
  expect([attr("data-beeps"), played]).toEqual(["0", []]);
  await arm();
  expect(played).toEqual([880]); // the open unacknowledged RED, once
});

test("arming with nothing active is silent; an ACKED RED does not beep", async () => {
  const es = await liveConsole();
  es.emit("snapshot", snapshot([alarmView({ state: "ACKED", last: { ...alarmView().last, kind: "ACK", alert: false } })]));
  await arm();
  expect(played).toEqual([]);
});

test("a state key from before a reload cannot silence the next onset", async () => {
  const es = await liveConsole();
  await arm();
  es.emit("snapshot", snapshot());
  es.emit("ground", { kind: "ground", broker: "down" });
  expect(played).toEqual([440]);
  act(() => root.unmount());
  root = createRoot(host);
  played.length = 0;
  await act(async () => root.render(<App />));
  await arm();
  const es2 = LiveES.all.at(-1)!;
  es2.emit("snapshot", snapshot());
  es2.emit("ground", { kind: "ground", broker: "up" });
  es2.emit("ground", { kind: "ground", broker: "down" });
  expect(played).toEqual([440]);
  expect(attr("data-last-tone")).toBe("BROKER:*|BROKER");
});

test("a failed login closes the audio context it armed", async () => {
  const close = vi.fn(() => Promise.resolve());
  vi.stubGlobal("AudioContext", class { resume = () => Promise.resolve(); close = close; });
  stubApi({ "/api/login": { status: 401, body: { error: "LOGIN_FAILED", message: "x" } } });
  act(() => root.render(<Login onLogin={vi.fn()} />));
  await submitLogin("fc1", "bad");
  expect(close).toHaveBeenCalledOnce();
});

test("a closed AudioContext brings the SOUND OFF badge back; the click makes a fresh context and replays what is open (P-pf24)", async () => {
  const es = await liveConsole();
  await arm();
  es.emit("snapshot", snapshot([alarmView()]));
  expect(played).toEqual([880]);
  act(() => void FakeAudio.states[0]!.close()); // the browser (or a bug) closed it
  expect(button("SOUND OFF – click to enable")).toBeDefined();
  await arm();
  expect(FakeAudio.states).toHaveLength(2);
  expect(played).toEqual([880, 880]); // the open unacknowledged RED sounds again on the fresh context
  expect(button("SOUND OFF – click to enable")).toBeUndefined();
});

test("a context whose resume fails is replaced by a fresh one", async () => {
  const es = await liveConsole();
  await arm();
  FakeAudio.states[0]!.state = "suspended";
  act(() => void FakeAudio.states[0]!.listeners.forEach((f) => f()));
  es.emit("snapshot", snapshot([alarmView()]));
  expect(played).toEqual([]);
  FakeAudio.failResume = true;
  await arm(); // resume rejects -> rearm
  FakeAudio.failResume = false;
  await act(async () => {});
  expect(FakeAudio.states).toHaveLength(2);
  expect(played).toEqual([880]); // the fresh context replays the open RED
});

test("a browser-suspended context that resumes by itself (statechange to running) sounds what was pending", async () => {
  const es = await liveConsole();
  await arm();
  const ctx = FakeAudio.states[0]!;
  const change = (state: string) => act(() => void ((ctx.state = state), ctx.listeners.forEach((f) => f())));
  change("suspended");
  expect(button("SOUND OFF – click to enable")).toBeDefined();
  es.emit("snapshot", snapshot([alarmView()]));
  expect(played).toEqual([]); // pending while the audio is off
  change("running"); // the browser resumed it, no click
  expect(played).toEqual([880]);
  expect(button("SOUND OFF – click to enable")).toBeUndefined();
});

test("App: each live tm frame leaves a latency sample after the second animation frame (D-UI-14)", async () => {
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => (cb(), 0));
  window.__nyxLatency = [];
  const es = await liveConsole();
  es.emit("snapshot", snapshot());
  es.emit("tm", { ...tmFrame, seq: 2, t_onboard: "2026-11-14T10:00:01.000000Z", age_ms: 10 });
  expect(window.__nyxLatency).toEqual([expect.objectContaining({ vehicle: "NYX-TA1", seq: 2, age_ms: 10 })]);
  expect(typeof window.__nyxLatency![0]!.paint_ms).toBe("number");
});
