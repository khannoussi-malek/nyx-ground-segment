// The ACK binds to what the operator saw (P-pf42/42b/43): the request carries the alarm's last key from when the
// dialog opened; a change while it is open closes it; no ACK on a stale screen (NO LIVE CONNECTION, GROUND
// PROCESSING DOWN).
import { expect, test, type Page } from "@playwright/test";
import { ackLeftovers, API, apiHealthy, apiLogin, clearFault, composeAsync, fault, login, summary } from "./helpers";

const V = "NYX-TA1";
const CHANGED = "Alarm changed – review it before acknowledging";
const TEMP = { param: "cabin_temp_c", text: "Cabin temperature" }; // yellow_high 27, red_high 30
const BUS = { param: "bus_voltage_v", text: "Main bus voltage" }; // yellow_high 30, red_high 32

const rowsOf = (page: Page, p: { text: string }) => page.locator("tr[data-alarm-id]").filter({ hasText: V }).filter({ hasText: p.text });
const ackButton = (page: Page, p: { param: string }) => page.getByRole("button", { name: `Acknowledge ${V} ${p.param}` });

/** A fresh UNACK alarm on `p` from a held step (leftovers of earlier runs acknowledged first). */
async function raise(page: Page, p: { param: string; text: string }, value: number, severity: "RED" | "YELLOW") {
  await ackLeftovers(page, rowsOf(page, p));
  await fault(V, "step", { param: p.param, value, duration_s: 60 });
  const row = rowsOf(page, p).first();
  await expect(row).toHaveAttribute("data-severity", severity, { timeout: 10_000 });
  await expect(ackButton(page, p)).toBeEnabled();
  return row;
}

test.afterEach(async () => {
  await clearFault(V, "step");
  await composeAsync("start", "redis");
  await apiHealthy();
});

test("the ACK request carries seen_key = the alarm's last key when the dialog opened", async ({ page }) => {
  await login(page, "fc1");
  const row = await raise(page, TEMP, 31, "RED");
  const id = await row.getAttribute("data-alarm-id");
  await ackButton(page, TEMP).click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge alarm" });
  const cookie = await apiLogin("fc1");
  const alarms = (await (await fetch(`${API}/api/alarms`, { headers: { cookie } })).json()) as { alarm_id: string; last: { key: string } }[];
  const shown = alarms.find((a) => a.alarm_id === id)!.last.key; // a held step: the key does not move while open
  await dialog.getByLabel("Note (required)").fill("seen key spec");
  const sent = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/api/alarms/${id}/ack`));
  await dialog.getByRole("button", { name: "ACK" }).click();
  expect((await sent).postDataJSON()).toMatchObject({ seen_key: shown, note: "seen key spec" });
});

test("the alarm escalates while the dialog is open: it closes and the row says the alarm changed", async ({ page }) => {
  await login(page, "fc1");
  const row = await raise(page, BUS, 31, "YELLOW");
  await ackButton(page, BUS).click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge alarm" });
  await dialog.getByLabel("Note (required)").fill("typed before the change");
  await fault(V, "step", { param: BUS.param, value: 33, duration_s: 60 }); // replaces the step: RED
  await expect(dialog).toBeHidden({ timeout: 10_000 });
  await expect(row).toHaveAttribute("data-severity", "RED");
  await expect(row.getByRole("status")).toHaveText(CHANGED);
});

test("no ACK while NO LIVE CONNECTION (the live stream blocked)", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "fc1");
  await raise(page, TEMP, 31, "RED");
  await page.route("**/api/events", (route) => route.abort()); // every reconnect of the live stream fails ...
  await composeAsync("restart", "api"); // ... once the open one is cut
  await expect(summary(page, V)).toContainText("NO LIVE CONNECTION", { timeout: 10_000 });
  await apiHealthy(); // the api is back; only the stream is blocked
  await expect(summary(page, V)).toContainText("NO LIVE CONNECTION");
  await expect(ackButton(page, TEMP)).toBeDisabled();
  await page.unroute("**/api/events");
  await expect(summary(page, V)).not.toContainText("NO LIVE CONNECTION", { timeout: 10_000 });
  await expect(ackButton(page, TEMP)).toBeEnabled();
});

test("no ACK while GROUND PROCESSING DOWN (Redis stopped)", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "fc1");
  await raise(page, TEMP, 31, "RED");
  await composeAsync("stop", "redis");
  await expect(summary(page, V)).toContainText("GROUND PROCESSING DOWN", { timeout: 10_000 });
  await expect(ackButton(page, TEMP)).toBeDisabled();
  await composeAsync("start", "redis");
  await expect(summary(page, V)).not.toContainText("GROUND PROCESSING DOWN", { timeout: 30_000 });
  await expect(ackButton(page, TEMP)).toBeEnabled();
});
