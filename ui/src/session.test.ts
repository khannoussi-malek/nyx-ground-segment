import { expect, test, vi } from "vitest";
import { attemptLogin, bootUser, checkSession, safeLogout } from "./session";

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
