// One-screen console (ui-one-screen): at 1280×720 and up every pane is on screen without page scroll, both
// vehicle cards show every row, long lists scroll inside their pane, and a pane's resize handle makes it taller
// (the page may then grow). Read-only: logs in and looks, changes nothing on the stack.
import { expect, test } from "@playwright/test";
import { login, VEHICLES } from "./helpers";

const PANES = [".alarms", ".console > main", ".commands", ".chart-panel", ".sessions", ".events"];

for (const [width, height] of [[1280, 720], [1920, 1080]] as const) {
  test(`${width}×${height}: every pane on one screen, no page scroll`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await login(page, "fc1");
    const m = await page.evaluate((sels) => ({
      pageH: document.documentElement.scrollHeight,
      pageW: document.documentElement.scrollWidth,
      panes: sels.map((s) => {
        const r = document.querySelector(s)!.getBoundingClientRect();
        return { s, top: r.top, bottom: r.bottom, h: r.height };
      }),
    }), PANES);
    expect(m.pageH).toBeLessThanOrEqual(height);
    expect(m.pageW).toBeLessThanOrEqual(width);
    for (const p of m.panes) {
      expect(p.h, `${p.s} has room`).toBeGreaterThan(40);
      expect(p.bottom, `${p.s} inside the screen`).toBeLessThanOrEqual(height);
    }
    // the live cards are never cut: their pane does not scroll
    const main = page.locator(".console > main");
    expect(await main.evaluate((e) => e.scrollHeight <= e.clientHeight + 1)).toBe(true);
    for (const v of VEHICLES) {
      await expect(page.locator(`[data-vehicle="${v}"][data-param="rate_z_dps"]`)).toBeInViewport();
    }
  });
}

test("a pane's resize handle makes it taller; the page grows instead of cutting it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await login(page, "fc1");
  const pane = page.locator(".alarms");
  await expect(pane).toHaveCSS("resize", "vertical");
  const box = (await pane.boundingBox())!;
  await page.mouse.move(box.x + box.width - 4, box.y + box.height - 4); // the native handle, bottom-right
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 4, box.y + box.height + 200, { steps: 8 });
  await page.mouse.up();
  expect((await pane.boundingBox())!.height).toBeGreaterThan(box.height + 150);
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeGreaterThan(720);
});
