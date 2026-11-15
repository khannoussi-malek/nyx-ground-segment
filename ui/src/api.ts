import type { components } from "./api/schema";

type LoginRequest = components["schemas"]["LoginRequest"];
type Me = components["schemas"]["Me"];
export type MissionDb = components["schemas"]["MissionDbView"];

const CLIENT = { "X-GS-Client": "console" };
const fail = (what: string, r: Response) => new Error(`${what} failed: ${r.status}`);

export async function login(user: string, password: string): Promise<boolean> {
  const body: LoginRequest = { user, password };
  const r = await fetch("/api/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...CLIENT },
    body: JSON.stringify(body),
  });
  if (r.status === 204) return true;
  if (r.status === 401) return false;
  throw fail("login", r);
}

export async function me(): Promise<string | null> {
  const r = await fetch("/api/me", { credentials: "same-origin" });
  if (r.status === 401) return null;
  if (!r.ok) throw fail("me", r);
  return ((await r.json()) as Me).user;
}

export async function logout(): Promise<void> {
  const r = await fetch("/api/logout", { method: "POST", credentials: "same-origin", headers: CLIENT });
  if (!r.ok && r.status !== 401) throw fail("logout", r);
}

/** The live mission db (labels, units, limits); null on 401 like me(). */
export async function missionDb(): Promise<MissionDb | null> {
  const r = await fetch("/api/mission-db", { credentials: "same-origin" });
  if (r.status === 401) return null;
  if (!r.ok) throw fail("mission-db", r);
  return (await r.json()) as MissionDb;
}

type AckAccepted = components["schemas"]["AckAccepted"];
type AckRequest = components["schemas"]["AckRequest"];
type ErrorBody = components["schemas"]["ErrorBody"];
export type AckResult = { ok: true; status: AckAccepted["status"] } | { ok: false; error: string; message: string };

/** Ask for an ACK. 202 = the request is accepted (it may still be pending); anything else comes back as the server's
 *  error + message for the dialog, a network failure as NETWORK. The same request id may be sent again: the server
 *  treats it as the same request, so a retry must carry the same `seenKey` (another one is 422 REQUEST_ID_REUSED).
 *  `seenKey` is the alarm's last event key the operator was shown (P-pf43): the owner supersedes the ACK if it moved on. */
export async function ackAlarm(alarmId: string, requestId: string, note: string, seenKey: string): Promise<AckResult> {
  const req: AckRequest = { request_id: requestId, note, seen_key: seenKey };
  try {
    const r = await fetch(`/api/alarms/${encodeURIComponent(alarmId)}/ack`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...CLIENT },
      body: JSON.stringify(req),
    });
    if (r.status === 202) return { ok: true, status: ((await r.json()) as AckAccepted).status };
    const body = (await r.json().catch(() => null)) as Partial<ErrorBody> | null;
    return { ok: false, error: body?.error ?? `HTTP_${r.status}`, message: body?.message ?? `ACK failed (${r.status})` };
  } catch {
    return { ok: false, error: "NETWORK", message: "Could not reach the ground segment" };
  }
}

export type CommandRequest = components["schemas"]["CommandRequest"];
export type CommandView = components["schemas"]["CommandView"];
type CommandAccepted = components["schemas"]["CommandAccepted"];
type CommandConflict = components["schemas"]["CommandConflict"];
export type SendResult =
  | { kind: "accepted"; cmdId: string }
  | { kind: "confirm"; token: string; hazard: string; expiresInS: number }
  | { kind: "rejected"; error: string; message: string; ambiguous: boolean; by?: string; secondsAgo?: number };

/** `ambiguous`: a network failure or any 5xx. The command MAY have been accepted (a timed-out XADD leaves it SENT,
 *  P-pf12), so the answer is never "send it again": check the command list first (P-pf26). */
const rejected = (error: string, message: string, ambiguous = false): SendResult => ({ kind: "rejected", error, message, ambiguous });

/** Send a command. A hazardous one answers 409 HAZARDOUS_CONFIRMATION_REQUIRED with a token: that is `confirm`, not an
 *  error; resend the same request with `confirm_token` and `reason`. Every other refusal keeps the server's words. */
export const SEND_TIMEOUT_MS = 10_000;

export async function sendCommand(req: CommandRequest): Promise<SendResult> {
  // a hung request must not leave the operator waiting: the abort is an ambiguous failure like any network error
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), SEND_TIMEOUT_MS);
  try {
    const r = await fetch("/api/commands", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...CLIENT },
      body: JSON.stringify(req),
      signal: abort.signal,
    });
    if (r.status === 202) return { kind: "accepted", cmdId: ((await r.json()) as CommandAccepted).cmd_id };
    const b = (await r.json().catch(() => null)) as Partial<CommandConflict> | null;
    if (r.status === 409 && b?.error === "HAZARDOUS_CONFIRMATION_REQUIRED" && b.confirm_token)
      return { kind: "confirm", token: b.confirm_token, hazard: b.hazard ?? "", expiresInS: b.expires_in_s ?? 60 };
    return {
      kind: "rejected",
      error: b?.error ?? `HTTP_${r.status}`,
      message: b?.message ?? `Command failed (${r.status})`,
      ambiguous: r.status >= 500 || r.status === 408,
      ...(b?.by != null && { by: b.by }),
      ...(b?.seconds_ago != null && { secondsAgo: b.seconds_ago }),
    };
  } catch {
    return rejected("NETWORK", "Could not reach the ground segment", true);
  } finally {
    clearTimeout(timer);
  }
}

/** Newest first, read-only; called after every accepted command so QUEUED / SENT show at once. */
export async function listCommands(vehicle?: string): Promise<CommandView[]> {
  const q = vehicle === undefined ? "" : `?vehicle=${encodeURIComponent(vehicle)}`;
  const r = await fetch(`/api/commands${q}`, { credentials: "same-origin" });
  if (!r.ok) throw fail("commands", r);
  return (await r.json()) as CommandView[];
}

export type Bucket = components["schemas"]["Bucket"];

/** M4 buckets for one parameter (D-DB-04): `width` is the plot's pixel width, the server picks the bucket from it. */
export async function fetchBuckets(vehicle: string, param: string, fromIso: string, toIso: string, width: number, signal?: AbortSignal): Promise<Bucket[]> {
  const q = new URLSearchParams({ from: fromIso, to: toIso, width: String(width) });
  const r = await fetch(`/api/telemetry/${encodeURIComponent(vehicle)}/${encodeURIComponent(param)}?${q}`, { credentials: "same-origin", ...(signal && { signal }) });
  if (!r.ok) throw fail("telemetry", r);
  return (await r.json()) as Bucket[];
}

export type SessionView = components["schemas"]["SessionView"];
export type SessionReport = components["schemas"]["SessionReport"];
type ReportConflict = components["schemas"]["ReportConflict"];
export type SessionResult =
  | { ok: true; session: SessionView }
  | { ok: false; error: string; message: string; ambiguous: boolean };
export type ReportResult =
  | { kind: "ready"; report: SessionReport }
  | { kind: "pending"; frames: number | null; expected: number | null }
  | { kind: "running" };

async function sessionCall(path: string, body?: unknown): Promise<SessionResult> {
  try {
    const r = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...CLIENT },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (r.ok) return { ok: true, session: (await r.json()) as SessionView };
    const b = (await r.json().catch(() => null)) as Partial<ErrorBody> | null;
    return { ok: false, error: b?.error ?? `HTTP_${r.status}`, message: b?.message ?? `Request failed (${r.status})`, ambiguous: r.status >= 500 };
  } catch {
    return { ok: false, error: "NETWORK", message: "Could not reach the ground segment", ambiguous: true };
  }
}

export const startSession = (vehicle: string, name: string): Promise<SessionResult> =>
  sessionCall("/api/sessions", { vehicle, name });
export const stopSession = (id: number): Promise<SessionResult> => sessionCall(`/api/sessions/${id}/stop`);

export async function listSessions(): Promise<SessionView[]> {
  const r = await fetch("/api/sessions", { credentials: "same-origin" });
  if (!r.ok) throw fail("sessions", r);
  return (await r.json()) as SessionView[];
}

/** The frozen report (D-DB-10): ready, still waiting for in-flight frames (409 REPORT_PENDING) or the session still runs. */
export async function getReport(id: number): Promise<ReportResult> {
  const r = await fetch(`/api/sessions/${id}/report`, { credentials: "same-origin" });
  if (r.ok) return { kind: "ready", report: (await r.json()) as SessionReport };
  const b = r.status === 409 ? ((await r.json().catch(() => null)) as Partial<ReportConflict> | null) : null;
  if (b?.error === "REPORT_PENDING") return { kind: "pending", frames: b.frames ?? null, expected: b.expected ?? null };
  if (b?.error === "SESSION_RUNNING") return { kind: "running" };
  throw fail("report", r);
}

export const reportCsvUrl = (id: number): string => `/api/sessions/${id}/report.csv`;
