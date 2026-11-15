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
type ErrorBody = components["schemas"]["ErrorBody"];
export type AckResult = { ok: true; status: AckAccepted["status"] } | { ok: false; error: string; message: string };

/** Ask for an ACK. 202 = the request is accepted (it may still be pending); anything else comes back as the server's
 *  error + message for the dialog, a network failure as NETWORK. The same request id may be sent again: the server
 *  treats it as the same request. */
export async function ackAlarm(alarmId: string, requestId: string, note: string): Promise<AckResult> {
  try {
    const r = await fetch(`/api/alarms/${encodeURIComponent(alarmId)}/ack`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...CLIENT },
      body: JSON.stringify({ request_id: requestId, note }),
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
  | { kind: "rejected"; error: string; message: string; by?: string; secondsAgo?: number };

const rejected = (error: string, message: string): SendResult => ({ kind: "rejected", error, message });

/** Send a command. A hazardous one answers 409 HAZARDOUS_CONFIRMATION_REQUIRED with a token: that is `confirm`, not an
 *  error; resend the same request with `confirm_token` and `reason`. Every other refusal keeps the server's words. */
export async function sendCommand(req: CommandRequest): Promise<SendResult> {
  try {
    const r = await fetch("/api/commands", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...CLIENT },
      body: JSON.stringify(req),
    });
    if (r.status === 202) return { kind: "accepted", cmdId: ((await r.json()) as CommandAccepted).cmd_id };
    const b = (await r.json().catch(() => null)) as Partial<CommandConflict> | null;
    if (r.status === 409 && b?.error === "HAZARDOUS_CONFIRMATION_REQUIRED" && b.confirm_token)
      return { kind: "confirm", token: b.confirm_token, hazard: b.hazard ?? "", expiresInS: b.expires_in_s ?? 60 };
    return {
      kind: "rejected",
      error: b?.error ?? `HTTP_${r.status}`,
      message: b?.message ?? `Command failed (${r.status})`,
      ...(b?.by != null && { by: b.by }),
      ...(b?.seconds_ago != null && { secondsAgo: b.seconds_ago }),
    };
  } catch {
    return rejected("NETWORK", "Could not reach the ground segment");
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
export async function fetchBuckets(vehicle: string, param: string, fromIso: string, toIso: string, width: number): Promise<Bucket[]> {
  const q = new URLSearchParams({ from: fromIso, to: toIso, width: String(width) });
  const r = await fetch(`/api/telemetry/${encodeURIComponent(vehicle)}/${encodeURIComponent(param)}?${q}`, { credentials: "same-origin" });
  if (!r.ok) throw fail("telemetry", r);
  return (await r.json()) as Bucket[];
}
