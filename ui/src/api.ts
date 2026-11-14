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
