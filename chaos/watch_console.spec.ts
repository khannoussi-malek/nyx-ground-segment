// I4 (design 11.5, D-EXT-15): the console never shows a row as OK (data-acq="OK") while its value is
// older than the stale threshold. Sampled in the page every 250 ms until chaos.soak creates the stop file.
// The limit is the vehicle's own stale threshold, which the value cell exposes as data-stale-ms
// (max(2 s, tm.stale_ms)), plus the UI-02 tolerance of 500 ms (A-EXT-11). Run by chaos.soak through e2e/chaos.config.ts; the result is a JSON file.
import { existsSync, writeFileSync } from "node:fs";
import { expect, login, test } from "../e2e/helpers";

const TOLERANCE_MS = 500;
const OVERRIDE_MS = process.env.CHAOS_I4_MAX_MS ?? ""; // CHAOS_I4_MAX_MS=0: self-check that rows are seen
const STOP = process.env.CHAOS_WATCH_STOP ?? "chaos/out/watch.stop";
const OUT = process.env.CHAOS_WATCH_OUT ?? "chaos/out/watch.json";

test("I4: no OK row shows data older than the stale threshold", async ({ page }) => {
  test.setTimeout(0); // ends when the soak says so
  await login(page, "fc1");
  await page.evaluate(({ tol, override }) => {
    const w = window as unknown as { __i4: { samples: number; rows: number; bad: string[] } };
    w.__i4 = { samples: 0, rows: 0, bad: [] };
    setInterval(() => {
      w.__i4.samples++;
      for (const td of document.querySelectorAll<HTMLElement>("td[data-t-onboard]")) {
        if (td.closest("tbody")?.getAttribute("data-acq") !== "OK") continue;
        w.__i4.rows++;
        const age = Date.now() - Date.parse(td.dataset.tOnboard ?? "");
        const limit = override !== "" ? Number(override) : Math.max(2000, Number(td.dataset.staleMs) || 0) + tol;
        if (age > limit && w.__i4.bad.length < 50)
          w.__i4.bad.push(`${new Date().toISOString()} ${td.dataset.vehicle} ${td.dataset.param} OK at ${age} ms`);
      }
    }, 250);
  }, { tol: TOLERANCE_MS, override: OVERRIDE_MS });
  while (!existsSync(STOP)) await page.waitForTimeout(1000);
  const r = await page.evaluate(() => (window as unknown as { __i4: object }).__i4);
  writeFileSync(OUT, JSON.stringify(r));
  expect((r as { bad: string[] }).bad).toEqual([]);
});
