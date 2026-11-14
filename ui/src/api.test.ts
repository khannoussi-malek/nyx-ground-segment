import { afterEach, expect, test, vi } from "vitest";
import { ackAlarm, listCommands, login, logout, me, missionDb, sendCommand } from "./api";

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

const RID = "7c0e1a64-3c58-4b43-a3b0-0d0a6c0d0e11";

test("ackAlarm posts the request id and note; 202 is ok with the status", async () => {
  const f = stub(202, { request_id: RID, status: "pending" });
  expect(await ackAlarm("a/1", RID, "seen")).toEqual({ ok: true, status: "pending" });
  expect(f).toHaveBeenCalledWith("/api/alarms/a%2F1/ack", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-GS-Client": "console" },
    body: JSON.stringify({ request_id: RID, note: "seen" }),
  });
});

test("ackAlarm hands the server's error and message back, never throws", async () => {
  stub(409, { error: "ALARM_CLEARED", message: "alarm already cleared" });
  expect(await ackAlarm("a", RID, "n")).toEqual({ ok: false, error: "ALARM_CLEARED", message: "alarm already cleared" });
  stub(502); // a proxy page, no JSON body
  expect(await ackAlarm("a", RID, "n")).toEqual({ ok: false, error: "HTTP_502", message: "ACK failed (502)" });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
  expect(await ackAlarm("a", RID, "n")).toMatchObject({ ok: false, error: "NETWORK" });
});

const REQ = { vehicle: "NYX-TA2", name: "vent_cabin", args: { duration_s: 3 } };

test("sendCommand: 202 accepted with the command id", async () => {
  const f = stub(202, { cmd_id: "c1" });
  expect(await sendCommand(REQ)).toEqual({ kind: "accepted", cmdId: "c1" });
  expect(f).toHaveBeenCalledWith("/api/commands", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-GS-Client": "console" },
    body: JSON.stringify(REQ),
  });
});

test("sendCommand: HAZARDOUS_CONFIRMATION_REQUIRED is a confirm step, not an error", async () => {
  stub(409, { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "m", hazard: "Irreversible", confirm_token: "tok", expires_in_s: 60 });
  expect(await sendCommand(REQ)).toEqual({ kind: "confirm", token: "tok", hazard: "Irreversible", expiresInS: 60 });
  stub(409, { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "m", confirm_token: "tok" });
  expect(await sendCommand(REQ)).toEqual({ kind: "confirm", token: "tok", hazard: "", expiresInS: 60 });
});

test("sendCommand: refusals keep the server's words; a duplicate carries who and when", async () => {
  stub(409, { error: "DUPLICATE_COMMAND", message: "dup", by: "fc2", seconds_ago: 1, cmd_id: "c0" });
  expect(await sendCommand(REQ)).toEqual({ kind: "rejected", error: "DUPLICATE_COMMAND", message: "dup", by: "fc2", secondsAgo: 1 });
  stub(422, { error: "INVALID_ARGS", message: "duration_s: 11 is outside [1.0, 10.0]" });
  expect(await sendCommand(REQ)).toEqual({ kind: "rejected", error: "INVALID_ARGS", message: "duration_s: 11 is outside [1.0, 10.0]" });
  stub(409, { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "no token in the body" }); // malformed: not a confirm
  expect(await sendCommand(REQ)).toMatchObject({ kind: "rejected" });
  stub(502);
  expect(await sendCommand(REQ)).toEqual({ kind: "rejected", error: "HTTP_502", message: "Command failed (502)" });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
  expect(await sendCommand(REQ)).toMatchObject({ kind: "rejected", error: "NETWORK" });
});

test("listCommands: GET with the vehicle filter, throws on an error status", async () => {
  const f = stub(200, [{ cmd_id: "c1" }]);
  expect(await listCommands("NYX-TA2")).toEqual([{ cmd_id: "c1" }]);
  expect(f).toHaveBeenCalledWith("/api/commands?vehicle=NYX-TA2", { credentials: "same-origin" });
  await listCommands();
  expect(f).toHaveBeenLastCalledWith("/api/commands", { credentials: "same-origin" });
  stub(503);
  await expect(listCommands()).rejects.toThrow("503");
});
