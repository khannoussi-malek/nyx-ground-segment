// ATP-07 step 5 (DAT-06, UI-04): replay of NYX-TA9 backfilled with a leak and a 10 s dropout.
// beforeAll backfills two hours through tests/support/replay_fixture.py (a few minutes: ingest and
// the archiver's 300 s loop) and corrupts the second one; each run uses two unused TA9 hours.
import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { ackLeftovers, beeps, clearFault, fault, login, summary } from "./helpers";

const REPO = new URL("..", import.meta.url).pathname;
const V = "NYX-TA9";
let w: { a: string; a_to: string; b: string; b_to: string; open_params: string[] };

test.beforeAll(() => {
  test.setTimeout(30 * 60_000);
  const out = execFileSync("uv", ["run", "-q", "python", "-m", "tests.support.replay_fixture"], {
    cwd: REPO, encoding: "utf8", env: { ...process.env, PYTHONPATH: REPO }, stdio: ["ignore", "pipe", "inherit"],
  });
  w = JSON.parse(out.trim().split("\n").at(-1)!);
});
test.afterEach(async () => clearFault("NYX-TA1", "step"));

const start = async (page: import("@playwright/test").Page, from: string, to: string) => {
  await page.locator(".replay-controls").getByLabel(/^Vehicle/).selectOption(V);
  await page.getByLabel("From (UTC)").fill(from);
  await page.getByLabel("To (UTC)").fill(to);
  await page.getByLabel("Speed").selectOption("10");
  await page.getByRole("button", { name: "Start" }).click();
};

test("replay at 10x: banner, recorded alarms, STALE + LOS on replay time, live RED, 409 on a corrupt hour", async ({ page }) => {
  test.setTimeout(420_000);
  await login(page, "fc1");
  await ackLeftovers(page, page.locator("tr[data-alarm-id]")); // so the live RED below is a new raise
  const b0 = await beeps(page);
  await page.getByRole("button", { name: "Replay" }).click();

  const banner = page.getByRole("banner").filter({ hasText: "REPLAY" });
  await expect(banner).toBeVisible();
  expect(await banner.getByRole("button").count()).toBe(0); // not dismissible
  const bg = await banner.evaluate((e) => getComputedStyle(e).backgroundColor);
  const [r, g, b] = bg.match(/\d+/g)!.map(Number);
  expect(b).toBeGreaterThan(g); // purple: blue and red over green
  expect(r).toBeGreaterThan(g);
  await expect(page.getByLabel("Speed")).toBeVisible();

  await start(page, w.a, w.a_to);
  await expect(page.getByLabel("Speed")).toHaveValue("10");
  const counter = banner.getByRole("status");
  await expect(counter).toHaveText(/LIVE: 0 unacknowledged \(0 RED\)/);

  // 15 min of replay time is 90 s at 10x: the dropout shows STALE and LOSS OF SIGNAL
  const stale = page.locator("tbody[data-acq=STALE]").filter({ has: page.locator(`[data-vehicle="${V}"]`) });
  await expect(stale.first()).toBeVisible({ timeout: 240_000 });
  await expect(summary(page, V)).toContainText("LOSS OF SIGNAL");
  const shown = new Date((await page.locator(".replay-progress time").textContent())!.replace("Replay time ", "")).getTime();
  const t0 = Date.parse(`${w.a}:00Z`);
  expect(shown - t0).toBeGreaterThanOrEqual(900_000); // on replay time, not wall time
  expect(shown - t0).toBeLessThan(930_000);

  // a live RED on NYX-TA1 during the replay: one beep, and the banner counts it
  await fault("NYX-TA1", "step", { param: "cabin_temp_c", value: 31, duration_s: 2 });
  await expect.poll(() => beeps(page), { timeout: 10_000 }).toBe(b0 + 1);
  await expect(counter).toHaveText(/LIVE: [1-9]\d* unacknowledged \([1-9]\d* RED\)/);
  await page.waitForTimeout(3_000);
  expect(await beeps(page)).toBe(b0 + 1);

  // the replay's alarm list is the recorded one
  await expect(page.getByText("Replay finished")).toBeVisible({ timeout: 120_000 });
  const params = await page.locator(".replay-frame tr[data-alarm-id] [data-plot-param]").evaluateAll((els) =>
    [...new Set(els.map((e) => e.getAttribute("data-plot-param")))].sort());
  expect(params).toEqual(w.open_params);
  expect(params.length).toBeGreaterThan(0);

  // a window over the corrupted hour is refused
  await start(page, w.b, w.b_to);
  await expect(page.getByRole("alert")).toHaveText("archive object failed hash check");
});
