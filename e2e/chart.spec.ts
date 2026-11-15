// D-UI-11, D-DB-04: the chart asks for exactly its pixel width, draws the 2 min window and
// leaves a gap where frames are missing (empty buckets are absent, spanGaps off).
import { expect, test, type Page } from "@playwright/test";
import { fault, login } from "./helpers";

type Bucket = { t: string; n: number };
const isTelemetry = (url: string) => /\/api\/telemetry\/NYX-TA1\/cabin_pressure_kpa\?/.test(url);

async function nextBuckets(page: Page): Promise<{ width: number; buckets: Bucket[] }> {
  const res = await page.waitForResponse((r) => isTelemetry(r.url()) && r.status() === 200, { timeout: 10_000 });
  return { width: Number(new URL(res.url()).searchParams.get("width")), buckets: (await res.json()) as Bucket[] };
}

test("width = the plot's pixel width; the 2 min window draws; a dropout leaves a gap", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 1000 });
  const first = nextBuckets(page);
  await login(page, "fc1");
  const panel = page.getByRole("region", { name: "Chart" });
  await panel.getByLabel("Vehicle").selectOption("NYX-TA1");
  await panel.getByLabel("Parameter").selectOption("cabin_pressure_kpa");
  await expect(panel.getByRole("button", { name: "2 min" })).toHaveAttribute("aria-pressed", "true");
  const { width } = await first;
  const plot = await panel.locator(".plot").boundingBox();
  expect(plot).not.toBeNull();
  expect(width).toBe(Math.min(4000, Math.max(100, Math.round(plot!.width))));
  await expect(panel.locator(".plot canvas").first()).toBeVisible(); // uPlot drew

  const t0 = Date.now();
  await fault("NYX-TA1", "dropout", { duration_s: 3 });
  await page.waitForTimeout(5_000); // the dropout and the first frames after it
  let gapS = 0;
  await expect
    .poll(async () => {
      const { buckets } = await nextBuckets(page);
      const ts = buckets.map((b) => Date.parse(b.t)).filter((t) => t >= t0 - 2_000);
      gapS = Math.max(0, ...ts.slice(1).map((t, i) => (t - ts[i]) / 1000));
      return gapS;
    }, { timeout: 15_000 })
    .toBeGreaterThan(2.5); // ~3 s without a bucket; the 2 min window's buckets are ~0.1 s
  expect(gapS).toBeLessThan(5);
});
