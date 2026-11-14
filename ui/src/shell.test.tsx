// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
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
  expect(onLogin).toHaveBeenCalledWith("fc1");
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
  expect(host.querySelector("header button")?.textContent).toBe("Log out");
  expect(host.querySelector("main")).not.toBeNull();
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
  await act(async () => host.querySelector<HTMLButtonElement>("header button")!.click());
  expect(host.querySelector('button[type="submit"]')?.textContent).toBe("Log in");
});
