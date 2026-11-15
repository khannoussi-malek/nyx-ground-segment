// ATP-01 short: sessions on NYX-TA1 and NYX-TA2 for 2 min, stopped, the report pending then ready,
// and the CSV downloaded with every time in UTC with Z (D-DB-10/11, D-ENG-18).
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { cell, fault, login, VEHICLES } from "./helpers";

const RUN_MS = 120_000;
const panel = (page: Page) => page.getByRole("region", { name: "Sessions" });

async function stopActive(page: Page): Promise<void> {
  const stops = panel(page).getByRole("button", { name: /^Stop / });
  while ((await stops.count()) > 0) {
    await stops.first().click(); // a session an earlier run left active
    await expect(panel(page).getByText("Loading the report…")).toBeHidden();
  }
}

test("two 2-minute sessions: report pending, then ready; CSV times end in Z", async ({ page }) => {
  test.setTimeout(RUN_MS + 120_000);
  await login(page, "fc1");
  await stopActive(page);
  const tag = Date.now().toString(36);
  for (const v of VEHICLES) {
    await panel(page).getByLabel("Vehicle").selectOption(v);
    await panel(page).getByLabel("Session name").fill(`atp01 ${v} ${tag}`);
    await panel(page).getByRole("button", { name: "Start session" }).click();
    await expect(panel(page).locator("tr[data-active]").filter({ hasText: `atp01 ${v} ${tag}` })).toBeVisible();
  }
  await page.waitForTimeout(RUN_MS);

  for (const v of VEHICLES) {
    const name = `atp01 ${v} ${tag}`;
    // NYX-TA2 stops inside a short dropout: no frame at or after ended_at is stored until the
    // dropout ends, so its report is deterministically pending first (D-DB-10 watermark)
    if (v === "NYX-TA2") {
      await fault(v, "dropout", { duration_s: 4 });
      // in effect once the newest frame the console shows is older than two 100 ms ticks
      const newest = async () => Date.now() - Date.parse((await cell(page, v, "cabin_pressure_kpa").getAttribute("data-t-onboard")) ?? "");
      await expect.poll(newest, { timeout: 3_000, intervals: [50] }).toBeGreaterThan(200);
    }
    await panel(page).getByRole("button", { name: `Stop ${name}` }).click();
    const pending = panel(page).getByText(/^Report pending/);
    const ready = panel(page).locator(".report h3").filter({ hasText: name });
    await expect(pending.or(ready)).toBeVisible({ timeout: 10_000 });
    const sawPending = await pending.isVisible();
    console.log(`${v}: report ${sawPending ? "pending first" : "ready at once"}`);
    if (v === "NYX-TA2") await expect(pending).toHaveText(/^Report pending: \d+ of \d+ frames/);
    // ask again until the server freezes it: frames past ended_at, or the 30 s incomplete rule
    await expect
      .poll(async () => {
        if (await ready.isVisible()) return true;
        const again = panel(page).getByRole("button", { name: "Check again" });
        if (await again.isVisible()) await again.click();
        return false;
      }, { timeout: 45_000, intervals: [1_000] })
      .toBe(true);
    await expect(panel(page).locator(".report")).toContainText(/complete, \d+ frames|incomplete: \d+ of \d+ frames/);
    const [download] = await Promise.all([page.waitForEvent("download"), panel(page).getByRole("link", { name: "Download CSV" }).click()]);
    const csv = readFileSync((await download.path())!, "utf8");
    expect(csv.split("\n")[0]).toContain("section,key,vehicle,name,kind,time,end_time");
    expect(csv).toMatch(/^summary,\d+,/m);
    // every timestamp in the file, whatever its column: date, time, then Z (no offset, no naive time)
    const stamps = [...csv.matchAll(/\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(.?)/g)];
    expect(stamps.length).toBeGreaterThanOrEqual(3); // started, ended, frozen at least
    for (const m of stamps) expect(m[0]).toMatch(/Z$/);
  }
});
