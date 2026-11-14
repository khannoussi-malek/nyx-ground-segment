import { afterEach, expect, test, vi } from "vitest";
import { login, logout, me, missionDb } from "./api";

const stub = (status: number, body?: unknown) => {
  const f = vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", f);
  return f;
};
afterEach(() => vi.unstubAllGlobals());

test("login sends X-GS-Client and JSON, true on 204, false on 401", async () => {
  const f = stub(204);
  expect(await login("fc1", "pw")).toBe(true);
  expect(f).toHaveBeenCalledWith("/api/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-GS-Client": "console" },
    body: JSON.stringify({ user: "fc1", password: "pw" }),
  });
  stub(401, { error: "LOGIN_FAILED", message: "x" });
  expect(await login("fc1", "bad")).toBe(false);
});

test("login throws on any other status", async () => {
  stub(503);
  await expect(login("a", "b")).rejects.toThrow("503");
});

test("me returns the user, null on 401, throws otherwise", async () => {
  const f = stub(200, { user: "fc1" });
  expect(await me()).toBe("fc1");
  expect(f).toHaveBeenCalledWith("/api/me", { credentials: "same-origin" });
  stub(401, { error: "UNAUTHENTICATED", message: "x" });
  expect(await me()).toBeNull();
  stub(500);
  await expect(me()).rejects.toThrow("500");
});

test("logout posts with the client header and tolerates an already-dead session", async () => {
  const f = stub(204);
  await logout();
  expect(f).toHaveBeenCalledWith("/api/logout", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-GS-Client": "console" },
  });
  stub(401);
  await expect(logout()).resolves.toBeUndefined();
  stub(500);
  await expect(logout()).rejects.toThrow("500");
});

test("missionDb is a plain same-origin GET: the body, null on 401, throws otherwise", async () => {
  const body = { set_sha256: "ab", parameters: {}, limits: {}, commands: {} };
  const f = stub(200, body);
  expect(await missionDb()).toEqual(body);
  expect(f).toHaveBeenCalledWith("/api/mission-db", { credentials: "same-origin" });
  stub(401, { error: "UNAUTHENTICATED", message: "x" });
  expect(await missionDb()).toBeNull();
  stub(503);
  await expect(missionDb()).rejects.toThrow("503");
});
