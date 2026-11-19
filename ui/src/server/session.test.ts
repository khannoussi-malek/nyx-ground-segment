import { expect, test, vi } from "vitest";
import type { MissionDb } from "./api";
import { attemptLogin, bootUser, checkSession, loadMission, safeLogout } from "./session";

test("attemptLogin: null on success, message on wrong password, message on error", async () => {
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  expect(await attemptLogin(async () => true, "u", "p")).toBeNull();
  expect(await attemptLogin(async () => false, "u", "p")).toBe("Wrong user or password");
  expect(await attemptLogin(async () => { throw new Error("503"); }, "u", "p")).toBe("Login failed, try again");
  expect(err).toHaveBeenCalledOnce();
  err.mockRestore();
});

test("bootUser: the user, null when logged out, null (logged) when the API fails", async () => {
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  expect(await bootUser(async () => "fc1")).toBe("fc1");
  expect(await bootUser(async () => null)).toBeNull();
  expect(await bootUser(async () => { throw new Error("down"); })).toBeNull();
  expect(err).toHaveBeenCalledOnce();
  err.mockRestore();
});

test("safeLogout swallows nothing silently but never throws", async () => {
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  await expect(safeLogout(async () => {})).resolves.toBeUndefined();
  expect(err).not.toHaveBeenCalled();
  await expect(safeLogout(async () => { throw new Error("500"); })).resolves.toBeUndefined();
  expect(err).toHaveBeenCalledOnce();
  err.mockRestore();
});

test("checkSession: an expired session (me -> null) goes back to login", async () => {
  const expired = vi.fn();
  await checkSession(async () => null, expired);
  expect(expired).toHaveBeenCalledOnce();
});

test("checkSession: a transient error with a valid session keeps reconnecting", async () => {
  const expired = vi.fn();
  await checkSession(async () => "fc1", expired);
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  await checkSession(async () => { throw new Error("network"); }, expired);
  expect(expired).not.toHaveBeenCalled();
  expect(err).toHaveBeenCalledOnce();
  err.mockRestore();
});

test("loadMission: the set, or back to login on 401, or ids-only (logged) on any error", async () => {
  const set = { set_sha256: "ab", parameters: {}, limits: { p: { checks: [] } } } as unknown as MissionDb;
  const expired = vi.fn();
  expect(await loadMission(async () => set, expired)).toBe(set);
  expect(await loadMission(async () => null, expired)).toBeNull();
  expect(expired).toHaveBeenCalledOnce();
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  expect(await loadMission(async () => { throw new Error("503"); }, expired)).toBeNull();
  expect(expired).toHaveBeenCalledOnce(); // an API error is not a dead session
  expect(err).toHaveBeenCalledOnce();
  err.mockRestore();
});

test("loadMission: a 200 with the wrong shape is logged and gives ids only, never a crash", async () => {
  const expired = vi.fn();
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  const bad: unknown[] = [
    {},
    { parameters: {}, limits: null },
    { parameters: { p: "kPa" }, limits: {} },
    { parameters: {}, limits: { p: {} } },
    { parameters: {}, limits: { p: { checks: "limit" } } },
    { parameters: {}, limits: { p: { checks: [null] } } },
    null,
  ];
  for (const body of bad) {
    expect(await loadMission(async () => body as MissionDb, expired)).toBeNull();
  }
  expect(err).toHaveBeenCalledTimes(bad.length - 1); // null is the 401 case, not a shape error
  expect(expired).toHaveBeenCalledOnce();
  err.mockRestore();
});
