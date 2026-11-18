// Shared e2e helpers: login, simulator faults, the console's audio counters, mission-db edits
// and docker compose, all against this worktree's stack (ports from ../.env).
import { execFile, execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { expect, type Page } from "@playwright/test";

const REPO = new URL("..", import.meta.url).pathname;
const LIMITS = `${REPO}mission/limits.yaml`;
export const VEHICLES = ["NYX-TA1", "NYX-TA2"] as const;
export const API = `http://127.0.0.1:${process.env.API_HOST_PORT ?? "8000"}`;
const SIM = `http://127.0.0.1:${process.env.SIM_HOST_PORT ?? "8081"}`;
export const CLIENT = { "X-GS-Client": "console" };

export function password(user: string): string {
  const pw = process.env[`DEMO_PW_${user.toUpperCase()}`];
  if (!pw) throw new Error(`no demo password for ${user} in .env`);
  return pw;
}

/** Log in through the form; resolves once the live table shows a value (the console is wired). */
export async function login(page: Page, user: string): Promise<void> {
  await page.goto("/");
  await page.getByLabel("User").fill(user);
  await page.getByLabel("Password").fill(password(user));
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(cell(page, "NYX-TA1", "cabin_pressure_kpa")).toBeVisible();
}

export const cell = (page: Page, vehicle: string, param: string) =>
  page.locator(`[data-vehicle="${vehicle}"][data-param="${param}"]`);
/** The <tbody> of one parameter row: data-level / data-acq carry its state. */
export const row = (page: Page, vehicle: string, param: string) =>
  page.locator("tbody").filter({ has: cell(page, vehicle, param) });
export const card = (page: Page, vehicle: string) =>
  page.locator("table").filter({ has: page.locator(`caption .vehicle`, { hasText: vehicle }) });
export const summary = (page: Page, vehicle: string) => card(page, vehicle).locator("caption .summary");

/** POST /sim/fault on the compose simulator; throws unless 204. */
export async function fault(vehicle: string, name: string, params: Record<string, unknown> = {}): Promise<void> {
  const r = await fetch(`${SIM}/sim/fault`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vehicle, fault: name, params }),
  });
  if (r.status !== 204) throw new Error(`fault ${name} on ${vehicle}: ${r.status} ${await r.text()}`);
}

export async function clearFault(vehicle: string, name: string): Promise<void> {
  await fetch(`${SIM}/sim/fault`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vehicle, fault: name }),
  });
}

/** The console's audio counters (App.tsx data-beeps / data-last-tone). */
export const beeps = async (page: Page): Promise<number> =>
  Number(await page.locator("[data-beeps]").getAttribute("data-beeps"));
export const lastTone = async (page: Page): Promise<string> =>
  (await page.locator("[data-last-tone]").getAttribute("data-last-tone")) ?? "";

let original: string | null = null;
/** Edit the host mission/limits.yaml the stack mounts (the watchers apply it within ~2 s). */
export function editLimits(old: string, next: string): void {
  const text = readFileSync(LIMITS, "utf8");
  if (!text.includes(old)) throw new Error(`limits.yaml has no ${old}`);
  original ??= text;
  writeFileSync(LIMITS, text.replace(old, next));
}
/** Put limits.yaml back (call in afterEach); a no-op when nothing was edited. */
export function restoreLimits(): void {
  if (original !== null) writeFileSync(LIMITS, original);
  original = null;
}

export function compose(...args: string[]): string {
  return execFileSync("docker", ["compose", ...args], { cwd: REPO, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

/** Wait until the API answers /healthz 200 (after a compose stop/start). */
export async function apiHealthy(timeoutMs = 60_000): Promise<void> {
  await expect
    .poll(async () => (await fetch(`${API}/healthz`).catch(() => null))?.status ?? 0, { timeout: timeoutMs, intervals: [500] })
    .toBe(200);
}

/** A logged-in API request context's cookie header for `user` (for checks outside the page). */
export async function apiLogin(user: string): Promise<string> {
  const r = await fetch(`${API}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...CLIENT },
    body: JSON.stringify({ user, password: password(user) }),
  });
  if (r.status !== 204) throw new Error(`login ${user}: ${r.status}`);
  return (r.headers.get("set-cookie") ?? "").split(";")[0];
}

/** The Commands panel: choose vehicle, command and its args, then Send. */
export async function sendCommand(page: Page, vehicle: string, name: string, args: Record<string, string> = {}, reason = ""): Promise<void> {
  const panel = page.getByRole("region", { name: "Commands" });
  await panel.getByLabel(/^Vehicle/).selectOption(vehicle);
  await panel.getByLabel(/^Command/).selectOption(name);
  for (const [k, v] of Object.entries(args)) {
    // an arg's <label> wraps its control (and a unit): match the label by its leading name
    const field = panel.locator("label").filter({ hasText: new RegExp(`^${k}`) }).locator("select, input");
    if ((await field.evaluate((el) => el.tagName)) === "SELECT") await field.selectOption(v);
    else await field.fill(v);
  }
  if (reason) await panel.getByLabel("Reason", { exact: true }).fill(reason);
  await panel.getByRole("button", { name: "Send" }).click();
}

/** The newest command list entry of `name` on `vehicle` (data-state is its state). */
export const commandItem = (page: Page, vehicle: string, name: string) =>
  page.locator("li[data-cmd-id]").filter({ hasText: vehicle }).filter({ hasText: name }).first();

/** audit_log rows since `sinceIso` through the Python test helper (tests/support/pg.audit_rows): the log has no REST route. */
export function auditRows(sinceIso: string): { action: string; actor: string; reason: string | null; details: Record<string, unknown> }[] {
  const code =
    "import json,sys;from datetime import datetime;from tests.support.pg import audit_rows;" +
    "print(json.dumps([{k: r[k] for k in ('action','actor','reason','details')} for r in audit_rows(since=datetime.fromisoformat(sys.argv[1]))]))";
  return JSON.parse(execFileSync("uv", ["run", "-q", "python", "-c", code, sinceIso], { cwd: REPO, encoding: "utf8", env: { ...process.env, PYTHONPATH: REPO } }));
}

/** ACK every open alarm row matching `rows` (leftovers of earlier runs) until none is listed:
 *  a returned one is CLEARED by its ACK. */
export async function ackLeftovers(page: Page, rows: ReturnType<Page["locator"]>, note = "leftover from an earlier run"): Promise<void> {
  for (let i = 0; i < 5 && (await rows.count()) > 0; i++) {
    const button = rows.first().getByRole("button", { name: /^Acknowledge / });
    if ((await button.count()) === 0) break; // ACKED and still violating: nothing to do
    const id = await rows.first().getAttribute("data-alarm-id");
    await button.click();
    const dialog = page.getByRole("dialog", { name: "Acknowledge alarm" });
    await dialog.getByLabel("Note (required)").fill(note);
    await dialog.getByRole("button", { name: "ACK" }).click();
    await expect(dialog).toBeHidden();
    // applied: the row is ACKED (no ACK button) or CLEARED (gone); superseded keeps it, and the loop asks again
    const acked = page.locator(`tr[data-alarm-id="${id}"]`).getByRole("button", { name: /^Acknowledge / });
    await expect(acked).toHaveCount(0, { timeout: 5_000 }).catch(() => {});
  }
  // ACKED and still violating is allowed (it neither beeps nor counts as unacknowledged)
  await expect(rows.filter({ has: page.getByRole("button", { name: /^Acknowledge / }) })).toHaveCount(0, { timeout: 5_000 });
}

/** `docker compose ...` without blocking the test's event loop (the page keeps being observed). */
export function composeAsync(...args: string[]): Promise<void> {
  return new Promise((resolve, reject) =>
    execFile("docker", ["compose", ...args], { cwd: REPO }, (e) => (e ? reject(e) : resolve())),
  );
}

export const seqOf = (page: Page, vehicle: string) => async () =>
  Number(await cell(page, vehicle, "cabin_pressure_kpa").getAttribute("data-seq"));

/** Record every data-last-tone value from now on (window.__tones): one entry per sounded tone batch. */
export async function recordTones(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __tones: string[] };
    w.__tones = [];
    const el = document.querySelector("[data-last-tone]");
    if (!el) throw new Error("no data-last-tone element");
    new MutationObserver(() => w.__tones.push(el.getAttribute("data-last-tone") ?? "")).observe(el, {
      attributes: true,
      attributeFilter: ["data-last-tone"],
    });
  });
}
export const tones = (page: Page): Promise<string[]> =>
  page.evaluate(() => (window as unknown as { __tones: string[] }).__tones);
/** The onset keys the console has toned (sessionStorage nyx.tones, D-UI-21). */
export const tonedKeys = (page: Page): Promise<string[]> =>
  page.evaluate(() => JSON.parse(sessionStorage.getItem("nyx.tones") ?? "[]") as string[]);

/** Add a mission parameter the simulator never sends: its column is migrated first (host-side
 *  gs.common.migrate on a copy, so the watchers never see an unmigrated set), then the stack's
 *  parameters.yaml is edited. The column stays (migrate only adds); restoreParameters() removes the
 *  parameter again. */
let originalParams: string | null = null;
const PARAMS = `${REPO}mission/parameters.yaml`;
export function addParameter(id: string): void {
  const line = `  ${id}: {unit: pct, type: float, description: e2e spare}\n`;
  const text = readFileSync(PARAMS, "utf8");
  const tmp = mkdtempSync(`${tmpdir()}/nyx-e2e-`);
  cpSync(`${REPO}mission`, tmp, { recursive: true });
  writeFileSync(`${tmp}/parameters.yaml`, text + line);
  execFileSync("uv", ["run", "-q", "python", "-c", "from gs.common.migrate import main; raise SystemExit(main())"], {
    cwd: REPO,
    env: { ...process.env, MISSION_DB: tmp, DATABASE_URL: `postgresql://gs_owner:${process.env.GS_OWNER_PASSWORD}@127.0.0.1:${process.env.DB_HOST_PORT}/gs` },
    stdio: "ignore",
  });
  originalParams ??= text;
  writeFileSync(PARAMS, text + line);
}
export function restoreParameters(): void {
  if (originalParams !== null) writeFileSync(PARAMS, originalParams);
  originalParams = null;
}

/**
 * Start a dropout on `vehicle`, then XADD `n` copies of its newest tm frame with t_onboard 10 min ahead
 * (the DLQ rejects them as t_onboard_range). One Python process does both, so process start-up never
 * eats into the 2 s between the last good frame and the LOS.
 */
export function dropoutWithBadFrames(vehicle: string, n: number, durationS: number): void {
  const code =
    "import json,sys,urllib.request as u;from datetime import datetime,timedelta;from tests.support.redisx import client;" +
    "r=client('admin');v=sys.argv[1];" +
    "u.urlopen(u.Request(sys.argv[3]+'/sim/fault',json.dumps({'vehicle':v,'fault':'dropout','params':{'duration_s':float(sys.argv[4])}}).encode(),{'Content-Type':'application/json'}));" +
    "last=r.xrevrange('tm:'+v,count=1)[0][1];f=json.loads(last['data']);" +
    "t=datetime.fromisoformat(f['t_onboard'].replace('Z','+00:00'))+timedelta(minutes=10);" +
    "[r.xadd('tm:'+v,{'data':json.dumps({**f,'t_onboard':t.isoformat(),'seq':f['seq']+1+i})}) for i in range(int(sys.argv[2]))]";
  execFileSync("uv", ["run", "-q", "python", "-c", code, vehicle, String(n), SIM, String(durationS)], {
    cwd: REPO,
    env: { ...process.env, PYTHONPATH: REPO },
  });
}
