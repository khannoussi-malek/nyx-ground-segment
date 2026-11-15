// UI-07, D-UI-04: an api restart shows NO LIVE CONNECTION at once and data flows again within
// 5 s; a 10 s broker stop shows GROUND PROCESSING DOWN – BROKER on both vehicles, never LOS.
import { expect, test } from "@playwright/test";
import { apiHealthy, composeAsync, login, seqOf, summary, VEHICLES } from "./helpers";

test.afterEach(async () => {
  await composeAsync("start", "redis");
  await apiHealthy();
});

test("api restart: NO LIVE CONNECTION from the first error, data flowing again in < 5 s", async ({ page }) => {
  await login(page, "fc1");
  const t0 = Date.now();
  const restart = composeAsync("restart", "api");
  for (const v of VEHICLES) await expect(summary(page, v)).toContainText("NO LIVE CONNECTION", { timeout: 5_000 });
  await restart;
  const s1 = await seqOf(page, "NYX-TA1")();
  await expect.poll(seqOf(page, "NYX-TA1"), { timeout: 5_000, intervals: [100] }).toBeGreaterThan(s1);
  for (const v of VEHICLES) await expect(summary(page, v)).not.toContainText("NO LIVE CONNECTION");
  console.log(`data flowing ${Date.now() - t0} ms after the restart began`);
});

test("redis stopped 10 s: GROUND PROCESSING DOWN – BROKER on both vehicles, no LOSS OF SIGNAL", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "fc1");
  await composeAsync("stop", "redis");
  const seen = new Set<string>();
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    for (const v of VEHICLES) seen.add(`${v}: ${await summary(page, v).textContent()}`);
    await page.waitForTimeout(250);
  }
  for (const v of VEHICLES) expect([...seen]).toContain(`${v}: GROUND PROCESSING DOWN – BROKER`);
  expect([...seen].filter((s) => s.includes("LOSS OF SIGNAL"))).toEqual([]);
  // and none while the broker comes back: the frames resume a moment after (sampled, not one read)
  const start = composeAsync("start", "redis");
  const until = Date.now() + 30_000;
  let clear = 0;
  while (Date.now() < until && clear < 30) {
    const texts = await Promise.all(VEHICLES.map(async (v) => `${v}: ${await summary(page, v).textContent()}`));
    texts.forEach((t) => seen.add(t));
    clear = texts.some((t) => t.includes("GROUND PROCESSING DOWN")) ? 0 : clear + 1; // 30 samples (~3 s) clear
    await page.waitForTimeout(100);
  }
  await start;
  expect(clear, "the broker banner never cleared").toBe(30);
  expect([...seen].filter((t) => t.includes("LOSS OF SIGNAL"))).toEqual([]);
});
