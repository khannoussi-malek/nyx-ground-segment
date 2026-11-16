// The operator's board (ui-one-screen): panes in the operator's own order and width, kept in this browser; panes
// of a row share its height, also after a resize; a click on a parameter plots it. Read-only on the stack: it logs
// in, rearranges this browser's layout and looks; it sends nothing.
import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

const order = (page: Page) => page.locator(".pane").evaluateAll((ps) => ps.map((p) => (p as HTMLElement).dataset.pane));
const edit = (page: Page) => page.getByRole("button", { name: "Edit layout" }).click();
const height = async (page: Page, id: string) => (await page.locator(`.pane[data-pane="${id}"]`).boundingBox())!.height;

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page, "fc1");
  await edit(page);
  await page.getByRole("button", { name: "Reset layout" }).click();
});

test("the layout is locked until Edit layout: no grips, no resize; Done locks it again", async ({ page }) => {
  await expect(page.locator(".grip").first()).toBeVisible();
  await expect(page.locator(".pane").first()).toHaveCSS("resize", "vertical");
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.locator(".grip")).toHaveCount(0);
  await expect(page.locator(".pane").first()).toHaveCSS("resize", "none");
  await expect(page.getByRole("button", { name: "Reset layout" })).toHaveCount(0);
  await expect(page.locator(".pane-title").first()).toHaveText("Alarms");
});

test("drag a pane by its grip onto another: the order changes and survives a reload; Reset layout restores it", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 2400 }); // both panes on screen: a scroll mid-drag would pick up another grip
  expect(await order(page)).toEqual(["alarms", "live", "chart", "commands", "sessions", "events"]);
  await page.locator('.pane[data-pane="events"] .grip').dragTo(page.locator('.pane[data-pane="live"]'));
  expect(await order(page)).toEqual(["alarms", "events", "live", "chart", "commands", "sessions"]);
  await page.reload();
  await expect(page.locator(".pane").nth(1)).toHaveAttribute("data-pane", "events");
  await edit(page);
  await page.getByRole("button", { name: "Reset layout" }).click();
  expect((await order(page))[1]).toBe("live");
});

test("the keyboard path: move buttons and the width toggle", async ({ page }) => {
  await page.getByRole("button", { name: "Move Sessions earlier" }).click();
  expect(await order(page)).toEqual(["alarms", "live", "chart", "sessions", "commands", "events"]);
  const toggle = page.getByRole("button", { name: "Commands full width" });
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(page.locator('.pane[data-pane="commands"]')).toHaveAttribute("data-wide", "true");
});

test("two panes of a row share its height, also after one is resized by its handle", async ({ page }) => {
  expect(Math.abs((await height(page, "commands")) - (await height(page, "sessions")))).toBeLessThan(1);
  await page.locator('.pane[data-pane="commands"]').evaluate((e) => e.scrollIntoView({ block: "start" }));
  const box = (await page.locator('.pane[data-pane="commands"]').boundingBox())!; // in view: the mouse reaches its corner
  await page.mouse.move(box.x + box.width - 3, box.y + box.height - 3); // the native handle, bottom-right
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 3, box.y + box.height + 200, { steps: 8 });
  await page.mouse.up();
  const [c, s] = [await height(page, "commands"), await height(page, "sessions")];
  expect(c).toBeGreaterThan(box.height + 150);
  expect(Math.abs(c - s)).toBeLessThan(1); // the neighbour followed
  await page.reload();
  expect(await height(page, "commands")).toBeGreaterThan(box.height + 150); // kept in this browser
});

test("long lists scroll inside their pane: the page does not grow with the history", async ({ page }) => {
  const pane = await height(page, "sessions");
  const list = page.locator(".sessions .scroll");
  if (await list.count()) {
    expect((await list.boundingBox())!.height).toBeLessThan(pane);
  }
  await expect(page.locator(".sessions [role=search]")).toBeVisible();
});

test("a click on a parameter in a card plots it; the header counts the unacknowledged alarms", async ({ page }) => {
  await page.locator('main .plot-link[data-plot-vehicle="NYX-TA2"][data-plot-param="cabin_temp_c"]').click();
  const chart = page.locator(".chart-panel");
  await expect(chart.getByLabel("Vehicle")).toHaveValue("NYX-TA2");
  await expect(chart.getByLabel("Parameter")).toHaveValue("cabin_temp_c");
  await expect(page.locator("header .alarm-summary")).toHaveText(/unacknowledged/);
});

test("the chart: hovering reads the value, a drag zooms into history, Older moves back, Live returns to now", async ({ page }) => {
  await page.locator('main .plot-link[data-plot-vehicle="NYX-TA1"][data-plot-param="cabin_pressure_kpa"]').click();
  await page.locator('.pane[data-pane="chart"]').evaluate((e) => e.scrollIntoView({ block: "start" }));
  const range = page.locator(".chart .range");
  await expect(range).toContainText("LIVE");
  await expect(page.locator(".chart canvas").first()).toBeVisible();
  const over = (await page.locator(".chart .u-over").boundingBox())!;
  await page.mouse.move(over.x + over.width * 0.7, over.y + over.height / 2);
  await expect(page.locator(".chart-tip")).toContainText(/\d\d:\d\d:\d\d\.\dZ/);
  await expect(page.locator(".chart-tip")).toContainText("kPa");
  await page.mouse.move(over.x + over.width * 0.4, over.y + 30);
  await page.mouse.down();
  await page.mouse.move(over.x + over.width * 0.6, over.y + 30, { steps: 6 });
  await page.mouse.up();
  await expect(range).toContainText("HISTORY");
  const zoomed = await range.textContent();
  await page.getByRole("button", { name: "Older" }).click();
  await expect(range).not.toHaveText(zoomed!);
  await page.getByRole("button", { name: "Live" }).click();
  await expect(range).toContainText("LIVE");
});

test("every pane's info button opens a short note under it; Esc closes it; the keyboard reaches it", async ({ page }) => {
  await page.getByRole("button", { name: "Done" }).click(); // the notes are there outside edit mode too
  for (const title of ["Alarms", "Vehicles", "Chart", "Commands", "Sessions", "Events"]) {
    await expect(page.getByRole("button", { name: `About ${title}` })).toBeVisible();
  }
  const info = page.getByRole("button", { name: "About Chart" });
  await info.click();
  const note = page.locator("#help-chart");
  await expect(note).toBeVisible();
  await expect(note).toContainText("Hover to read a value");
  const [b, n] = [(await info.boundingBox())!, (await note.boundingBox())!];
  expect(n.y).toBeGreaterThanOrEqual(b.y + b.height - 1); // opens under its button
  await page.keyboard.press("Escape");
  await expect(note).toBeHidden();
  await info.focus();
  await page.keyboard.press("Enter");
  await expect(note).toBeVisible();
});
