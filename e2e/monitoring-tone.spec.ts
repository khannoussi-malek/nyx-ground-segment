// D-UI-15 / D-UI-21 and ruling R-m: one tone per loss-of-monitoring onset. Both workers stopped
// → one GROUND PROCESSING DOWN tone per vehicle and none after; api stopped → one NO LIVE
// CONNECTION tone; a parameter missing from the frames (NO DATA) → no tone; a RED step while the
// other vehicle has GROUND PROCESSING DOWN still beeps once.
import { expect, test } from "@playwright/test";
import {
  addParameter, apiHealthy, beeps, composeAsync, fault, login, recordTones, restoreParameters,
  row, summary, tonedKeys, tones, VEHICLES,
} from "./helpers";

const DETECT_MS = 10_000; // hb TTL 3 s + the hub's 1 s hb + the console's tick, with margin
const SPARE = "e2e_spare_pct";

test.afterEach(async () => {
  restoreParameters();
  await composeAsync("unpause", "ingest-2").catch(() => {});
  await composeAsync("start", "ingest-1", "ingest-2", "api");
  await apiHealthy();
});

test("both workers stopped: one tone per vehicle, none after; restarted: monitoring is back", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "fc1");
  await recordTones(page);
  await composeAsync("stop", "ingest-1", "ingest-2");
  for (const v of VEHICLES) await expect(summary(page, v)).toContainText("GROUND PROCESSING DOWN", { timeout: DETECT_MS });
  await expect.poll(async () => (await tones(page)).length, { timeout: 5_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(1_000); // the second vehicle's onset, when it lands in a later hb
  // one entry per tone; two onsets in one store update sound once (the entry names the last key)
  const atOnset = await tones(page);
  expect(atOnset.every((t) => /^GPD:NYX-TA[12]\|GPD$/.test(t)), atOnset.join()).toBe(true);
  expect(new Set(atOnset).size).toBe(atOnset.length); // no vehicle toned twice
  await page.waitForTimeout(5_000);
  expect(await tones(page)).toEqual(atOnset); // none after
  await composeAsync("start", "ingest-1", "ingest-2");
  for (const v of VEHICLES) await expect(summary(page, v)).not.toContainText("GROUND PROCESSING DOWN", { timeout: 30_000 });
});

test("api stopped: one NO LIVE CONNECTION tone", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "fc1");
  await recordTones(page);
  await composeAsync("stop", "api");
  for (const v of VEHICLES) await expect(summary(page, v)).toContainText("NO LIVE CONNECTION", { timeout: 5_000 });
  await page.waitForTimeout(5_000); // reconnect attempts keep failing: still one tone
  expect(await tones(page)).toEqual(["NLC:*|NLC"]);
});

test("a parameter missing from the frames shows NO DATA without a tone", async ({ page }) => {
  test.setTimeout(60_000);
  await login(page, "fc1");
  await recordTones(page);
  const before = await tonedKeys(page);
  addParameter(SPARE);
  await expect(row(page, "NYX-TA1", SPARE)).toHaveAttribute("data-acq", "NO_DATA", { timeout: 15_000 });
  await expect(row(page, "NYX-TA1", SPARE).locator(".acq")).toContainText("NO DATA");
  await page.waitForTimeout(3_000);
  expect(await tones(page)).toEqual([]);
  expect(await tonedKeys(page)).toEqual(before);
});

test("a RED step on NYX-TA1 while NYX-TA2 has GROUND PROCESSING DOWN still beeps once", async ({ page }) => {
  test.setTimeout(60_000);
  await login(page, "fc1");
  const b0 = await beeps(page);
  await composeAsync("pause", "ingest-2"); // TA2's owner hangs; ingest-1 steals it only after 30 s (R-m)
  await expect(summary(page, "NYX-TA2")).toContainText("GROUND PROCESSING DOWN", { timeout: DETECT_MS });
  await fault("NYX-TA1", "step", { param: "cabin_temp_c", value: 31, duration_s: 2 });
  await expect.poll(() => beeps(page), { timeout: 10_000 }).toBe(b0 + 1);
  await page.waitForTimeout(3_000);
  expect(await beeps(page)).toBe(b0 + 1);
  await composeAsync("unpause", "ingest-2");
});
