// ATP-04: a 10 s dropout → every NYX-TA1 row STALE at 2 s ± 0.5 s, LOSS OF SIGNAL, one tone;
// the AOS strip line counts 100 frames lost (±1); the vehicle recovers on its own.
import { expect, test } from "@playwright/test";
import { clearFault, fault, lastTone, login, row, summary } from "./helpers";

const V = "NYX-TA1";
const DROPOUT_S = 10;

test.afterEach(async () => clearFault(V, "dropout"));

test("dropout 10 s: STALE at 2 s, LOSS OF SIGNAL, one tone, AOS with 100 frames lost, recovery", async ({ page }) => {
  test.setTimeout(60_000);
  await login(page, "fc1");
  const pressure = row(page, V, "cabin_pressure_kpa");
  await expect(pressure).toHaveAttribute("data-acq", "OK");
  const rows = page.locator("tbody[data-acq]").filter({ has: page.locator(`[data-vehicle="${V}"]`) });
  const n = await rows.count();
  expect(n).toBeGreaterThan(0);
  const tone0 = await lastTone(page);
  const aosLines = page.locator('li[data-kind="AOS"]').filter({ hasText: `${V} AOS` });
  // earlier runs' AOS lines stay in the strip (newest first, 200 kept): wait for a new first one
  const aos0 = (await aosLines.count()) > 0 ? await aosLines.first().textContent() : null;

  const t0 = Date.now();
  await fault(V, "dropout", { duration_s: DROPOUT_S });
  const allStale = () => rows.evaluateAll((els) => els.every((e) => e.getAttribute("data-acq") === "STALE"));
  await expect.poll(allStale, { intervals: [50], timeout: 5_000 }).toBe(true);
  const staleS = (Date.now() - t0) / 1000;
  console.log(`every row STALE after ${staleS.toFixed(2)} s`);
  expect(staleS).toBeGreaterThanOrEqual(1.5);
  expect(staleS).toBeLessThanOrEqual(2.5);
  await expect(pressure.locator(".acq")).toContainText("STALE"); // text and glyph, not colour alone
  await expect(summary(page, V)).toContainText("LOSS OF SIGNAL");

  await expect.poll(() => lastTone(page), { timeout: 5_000 }).toMatch(new RegExp(`^LOS:LOS/${V}/`));
  const tone = await lastTone(page);
  expect(tone).not.toBe(tone0);
  await page.waitForTimeout(3_000);
  expect(await lastTone(page)).toBe(tone); // one tone for this onset, no repeat

  await expect.poll(() => aosLines.first().textContent(), { timeout: DROPOUT_S * 1000 + 10_000 }).not.toBe(aos0);
  await expect(aosLines.first()).toHaveText(/AOS – (99|100|101) frames lost/);
  await expect(summary(page, V)).not.toContainText("LOSS OF SIGNAL");
  await expect(pressure).toHaveAttribute("data-acq", "OK");
});
