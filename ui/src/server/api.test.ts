import { afterEach, expect, test, vi } from "vitest";
import { ackAlarm, createReplay, fetchBuckets, getReport, listCommands, listSessions, login, logout, me, missionDb, reportCsvUrl, sendCommand, startSession, stopSession } from "./api";

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

test("ackAlarm posts the request id, note and seen_key; 202 is ok with the status", async () => {
  const f = stub(202, { request_id: RID, status: "pending" });
  expect(await ackAlarm("a/1", RID, "seen", "ALARM/k1")).toEqual({ ok: true, status: "pending" });
  expect(f).toHaveBeenCalledWith("/api/alarms/a%2F1/ack", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-GS-Client": "console" },
    body: JSON.stringify({ request_id: RID, note: "seen", seen_key: "ALARM/k1" }),
  });
});

test("ackAlarm hands the server's error and message back, never throws", async () => {
  stub(409, { error: "ALARM_CLEARED", message: "alarm already cleared" });
  expect(await ackAlarm("a", RID, "n", "k")).toEqual({ ok: false, error: "ALARM_CLEARED", message: "alarm already cleared" });
  stub(502); // a proxy page, no JSON body
  expect(await ackAlarm("a", RID, "n", "k")).toEqual({ ok: false, error: "HTTP_502", message: "ACK failed (502)" });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
  expect(await ackAlarm("a", RID, "n", "k")).toMatchObject({ ok: false, error: "NETWORK" });
});

test("createReplay: 201 gives the id; refusals keep the server's words; no body and no network are named", async () => {
  const req = { vehicle: "NYX-TA1", from: "2026-11-10T14:00:00Z", to: "2026-11-10T14:01:00Z", speed: 1 } as const;
  const f = stub(201, { replay_id: "r1" });
  expect(await createReplay(req)).toEqual({ ok: true, replayId: "r1" });
  expect(f).toHaveBeenCalledWith("/api/replays", expect.objectContaining({ method: "POST", body: JSON.stringify(req) }));
  stub(409, { error: "REPLAY_BUSY", message: "a replay is already running" });
  expect(await createReplay(req)).toEqual({ ok: false, error: "REPLAY_BUSY", message: "a replay is already running" });
  stub(502);
  expect(await createReplay(req)).toEqual({ ok: false, error: "HTTP_502", message: "Replay failed (502)" });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
  expect(await createReplay(req)).toEqual({ ok: false, error: "NETWORK", message: "Could not reach the ground segment" });
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
    signal: expect.any(AbortSignal),
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
  expect(await sendCommand(REQ)).toEqual({ kind: "rejected", error: "DUPLICATE_COMMAND", message: "dup", ambiguous: false, by: "fc2", secondsAgo: 1 });
  stub(422, { error: "INVALID_ARGS", message: "duration_s: 11 is outside [1.0, 10.0]" });
  expect(await sendCommand(REQ)).toEqual({ kind: "rejected", error: "INVALID_ARGS", message: "duration_s: 11 is outside [1.0, 10.0]", ambiguous: false });
  stub(409, { error: "HAZARDOUS_CONFIRMATION_REQUIRED", message: "no token in the body" }); // malformed: not a confirm
  expect(await sendCommand(REQ)).toMatchObject({ kind: "rejected" });
  stub(502);
  expect(await sendCommand(REQ)).toEqual({ kind: "rejected", error: "HTTP_502", message: "Command failed (502)", ambiguous: true });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
  expect(await sendCommand(REQ)).toMatchObject({ kind: "rejected", error: "NETWORK", ambiguous: true });
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

test("fetchBuckets: from, to and the pixel width in the query; throws on an error status", async () => {
  const f = stub(200, [{ t: "2026-11-14T10:00:00Z", n: 1, first: 1, last: 1, lo: 1, hi: 1, mean: 1 }]);
  const got = await fetchBuckets("NYX-TA1", "cabin_pressure_kpa", "2026-11-14T10:00:00.000Z", "2026-11-14T10:02:00.000Z", 800);
  expect(got).toHaveLength(1);
  expect(f).toHaveBeenCalledWith(
    "/api/telemetry/NYX-TA1/cabin_pressure_kpa?from=2026-11-14T10%3A00%3A00.000Z&to=2026-11-14T10%3A02%3A00.000Z&width=800",
    { credentials: "same-origin" },
  );
  stub(400);
  await expect(fetchBuckets("v", "p", "a", "b", 1)).rejects.toThrow("400");
});

test("sendCommand: only a network failure or a 5xx is ambiguous; a 4xx refusal is a clear no (P-pf26)", async () => {
  stub(503, { error: "BROKER_DOWN", message: "Redis error" });
  expect(await sendCommand(REQ)).toMatchObject({ error: "BROKER_DOWN", ambiguous: true });
  stub(500, { error: "INTERNAL", message: "x" });
  expect(await sendCommand(REQ)).toMatchObject({ ambiguous: true });
  stub(422, { error: "INVALID_ARGS", message: "x" });
  expect(await sendCommand(REQ)).toMatchObject({ ambiguous: false });
  stub(409, { error: "CONFIRMATION_INVALID", message: "x" });
  expect(await sendCommand(REQ)).toMatchObject({ ambiguous: false });
  vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 202 }))); // an unreadable 202 body: unknown outcome
  expect(await sendCommand(REQ)).toMatchObject({ error: "NETWORK", ambiguous: true });
});

test("startSession and stopSession: POST, the session back; refusals keep their words; network and 5xx are ambiguous", async () => {
  const s = { id: 3, name: "n", vehicle: "NYX-TA1", conductor: "fc1", started_at: "2026-11-14T10:00:00Z", ended_at: null, ended_by: null };
  const f = stub(201, s);
  expect(await startSession("NYX-TA1", "n")).toEqual({ ok: true, session: s });
  expect(f).toHaveBeenCalledWith("/api/sessions", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-GS-Client": "console" },
    body: JSON.stringify({ vehicle: "NYX-TA1", name: "n" }),
  });
  const g = stub(200, s);
  expect(await stopSession(3)).toEqual({ ok: true, session: s });
  expect(g).toHaveBeenCalledWith("/api/sessions/3/stop", { method: "POST", credentials: "same-origin", headers: { "X-GS-Client": "console" } });
  stub(409, { error: "SESSION_ACTIVE", message: "NYX-TA1 already has an active session" });
  expect(await startSession("NYX-TA1", "n")).toEqual({ ok: false, error: "SESSION_ACTIVE", message: "NYX-TA1 already has an active session", ambiguous: false });
  stub(503, { error: "DB_DOWN", message: "x" });
  expect(await stopSession(3)).toMatchObject({ ok: false, ambiguous: true });
  stub(502);
  expect(await stopSession(3)).toMatchObject({ error: "HTTP_502", ambiguous: true });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
  expect(await stopSession(3)).toMatchObject({ error: "NETWORK", ambiguous: true });
});

test("listSessions, getReport (ready, pending, running) and the CSV url", async () => {
  const f = stub(200, [{ id: 1 }]);
  expect(await listSessions()).toEqual([{ id: 1 }]);
  expect(f).toHaveBeenCalledWith("/api/sessions", { credentials: "same-origin" });
  stub(503);
  await expect(listSessions()).rejects.toThrow("503");
  stub(200, { frames: 1 });
  expect(await getReport(1)).toEqual({ kind: "ready", report: { frames: 1 } });
  stub(409, { error: "REPORT_PENDING", message: "m", frames: 90, expected: 100 });
  expect(await getReport(1)).toEqual({ kind: "pending", frames: 90, expected: 100 });
  stub(409, { error: "REPORT_PENDING", message: "m" });
  expect(await getReport(1)).toEqual({ kind: "pending", frames: null, expected: null });
  stub(409, { error: "SESSION_RUNNING", message: "m" });
  expect(await getReport(1)).toEqual({ kind: "running" });
  stub(409, { error: "OTHER", message: "m" });
  await expect(getReport(1)).rejects.toThrow("409");
  stub(404);
  await expect(getReport(1)).rejects.toThrow("404");
  expect(reportCsvUrl(7)).toBe("/api/sessions/7/report.csv");
});

test("sendCommand: 408 is ambiguous; a hung request is aborted after 10 s and is ambiguous too", async () => {
  stub(408, { error: "TIMEOUT", message: "x" });
  expect(await sendCommand(REQ)).toMatchObject({ ambiguous: true });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    vi.stubGlobal("fetch", vi.fn((_u: string, init: { signal: AbortSignal }) => new Promise((_res, rej) => init.signal.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError"))))));
    const p = sendCommand(REQ);
    await vi.advanceTimersByTimeAsync(9_999);
    let done = false;
    void p.then(() => (done = true));
    await Promise.resolve();
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await p).toMatchObject({ kind: "rejected", error: "NETWORK", ambiguous: true });
  } finally {
    vi.useRealTimers();
  }
});
