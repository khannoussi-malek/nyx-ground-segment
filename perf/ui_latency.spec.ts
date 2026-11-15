import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

// D-UI-14 / D-PERF-02: the page half of UI-01. The probe is opt-in (P-pf28): it records only when
// window.__nyxLatency is already an array when the app starts, so the init script arms it.
type Sample = { vehicle: string; seq: number; age_ms: number | null; paint_ms: number };
declare global {
  interface Window {
    __nyxLatency?: Sample[];
  }
}

const duration = Number(process.env.PERF_DURATION_S ?? 600);
const outDir = process.env.PERF_OUT ?? "docs/perf/run-n-local";

test("screen latency over the whole run", async ({ page }) => {
  const pw = process.env.DEMO_PW_FC1;
  if (!pw) throw new Error("no DEMO_PW_FC1 in .env");
  await page.addInitScript(() => {
    window.__nyxLatency = [];
  });
  await page.goto("/");
  await page.getByLabel("User").fill("fc1");
  await page.getByLabel("Password").fill(pw);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.locator('[data-vehicle="NYX-TA1"]').first()).toBeVisible();

  await page.waitForTimeout(duration * 1000);
  const samples = await page.evaluate(() => window.__nyxLatency ?? []);
  // 2 vehicles x 10 Hz; a mostly dead probe must not pass
  const expected = 2 * 10 * duration;
  expect(samples.length, "no latency samples: probe not armed or UI build lacks probe.ts").toBeGreaterThan(0);
  expect(samples.length, `only ${samples.length} of about ${expected} frames were sampled (need 80%)`).toBeGreaterThanOrEqual(
    Math.floor(0.8 * expected),
  );

  const rows = samples.map((s) =>
    [s.vehicle, s.seq, s.age_ms ?? "", s.paint_ms.toFixed(3), s.age_ms === null ? "" : (s.age_ms + s.paint_ms).toFixed(3)].join(","),
  );
  mkdirSync(outDir, { recursive: true });
  writeFileSync(join(outDir, "ui.csv"), ["vehicle,seq,age_ms,paint_ms,total_ms", ...rows].join("\n") + "\n");
});
