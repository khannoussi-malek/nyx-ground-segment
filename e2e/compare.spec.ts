// EXT-02 / D-EXT-12: two sessions overlaid on the Compare page: legend text, dash pattern per session,
// the paint time (logged, not asserted), and a CSV export of one session through the form.
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { login, stopActiveSessions, VEHICLES } from "./helpers";

const RUN_MS = 8_000;
const PARAM = "cabin_pressure_kpa";
const panel = (page: Page) => page.getByRole("region", { name: "Sessions" });

test("two sessions overlaid: legend, dash patterns, paint time; one session exported as CSV", async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, "fc1");
  await stopActiveSessions(page);
  const tag = Date.now().toString(36);
  const names = VEHICLES.map((v) => `cmp ${v} ${tag}`);
  for (const [i, v] of VEHICLES.entries()) {
    await panel(page).getByLabel(/^Vehicle/).selectOption(v);
    await panel(page).getByLabel("Session name").fill(names[i]!);
    await panel(page).getByRole("button", { name: "Start session" }).click();
    await expect(panel(page).locator("tr[data-active]").filter({ hasText: names[i]! })).toBeVisible();
  }
  await page.waitForTimeout(RUN_MS);
  await stopActiveSessions(page);

  await page.getByRole("button", { name: "Compare" }).click();
  const form = page.locator(".compare form").first();
  for (const n of names) await form.getByLabel(new RegExp(n)).check();
  await form.getByLabel(/^Cabin total pressure/).check();
  const t0 = Date.now();
  await form.getByRole("button", { name: "Compare" }).click();
  await expect(page.locator(`.compare-plot[data-param="${PARAM}"] canvas`)).toBeVisible();
  await expect(page.locator(`.compare-plot[data-param="${PARAM}"]`)).toHaveAttribute("data-bands", String(names.length));
  await expect(page.getByRole("heading", { name: /^Cabin total pressure/ })).toBeVisible();
  console.log(`compare paint time: ${Date.now() - t0} ms`);

  const items = page.locator(".compare-legend li");
  await expect(items).toHaveCount(2);
  for (const [i, v] of VEHICLES.entries())
    await expect(items.nth(i)).toHaveText(new RegExp(`${names[i]} · ${v} · started \\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d\\.\\d{3}Z`));
  const dash = (i: number) => items.nth(i).locator("line").getAttribute("stroke-dasharray");
  expect(await dash(0)).not.toBe(await dash(1));
  await page.screenshot({ path: "test-results/compare-two-sessions.png", fullPage: true });

  const exp = page.getByRole("region", { name: "Export data" });
  await exp.getByLabel("Session").selectOption({ label: `${names[0]} · ${VEHICLES[0]}` });
  const [download] = await Promise.all([page.waitForEvent("download"), exp.getByRole("button", { name: "Export" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^NYX-TA1_.*Z_.*Z\.csv$/);
  const head = readFileSync((await download.path())!, "utf8").split("\n").find((l) => !l.startsWith("#"));  // R23 M1: comment lines first
  expect(head).toMatch(/^vehicle,seq,t_onboard,t_received,mode,last_cmd_id/);
});
