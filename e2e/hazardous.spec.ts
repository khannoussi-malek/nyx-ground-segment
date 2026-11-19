// ATP-06: hazardous two-step, the 2 s interlock across operators, and the audit trail.
import { expect, test } from "@playwright/test";
import { auditRows, commandItem, fillCommand, login, sendCommand } from "./helpers";

const REASON = "venting for the ATP-06 spec";

test("vent_cabin with the typed id and a reason reaches VERIFIED; without the confirm it never appears", async ({ page }) => {
  const since = new Date().toISOString();
  await login(page, "fc1");
  await sendCommand(page, "NYX-TA1", "vent_cabin", { duration_s: "1" });
  const dialog = page.getByRole("dialog", { name: "Hazardous command" });
  await dialog.getByRole("button", { name: "Cancel" }).click(); // no confirm: nothing is queued
  await expect(dialog).toBeHidden();
  const before = await page.locator("li[data-cmd-id]").filter({ hasText: "vent_cabin" }).count();
  await page.waitForTimeout(1000);
  await expect(page.locator("li[data-cmd-id]").filter({ hasText: "vent_cabin" })).toHaveCount(before);

  await sendCommand(page, "NYX-TA1", "vent_cabin", { duration_s: "1" });
  await dialog.getByLabel("Reason (at least 10 characters)").fill(REASON);
  await dialog.getByLabel("Type NYX-TA1 to confirm").fill("NYX-TA1");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  const item = commandItem(page, "NYX-TA1", "vent_cabin (duration_s = 1)");
  await expect(item).toHaveAttribute("data-state", "VERIFIED", { timeout: 20_000 });
  for (const step of ["QUEUED", "SENT", "RECEIVED", "VERIFIED"])
    await expect(item.locator("li[data-reached]").filter({ hasText: step })).toHaveCount(1);
  const mine = auditRows(since).filter((r) => r.actor === "fc1" && r.details.name === "vent_cabin");
  expect(mine.map((r) => r.action).sort()).toEqual(["CMD_CONFIRM_REQUIRED", "CMD_CONFIRM_REQUIRED", "CMD_REQUESTED"]);
  expect(mine.find((r) => r.action === "CMD_REQUESTED")?.reason).toBe(REASON);
});

test("fc1 and fc2 send isolate_valve A within 2 s: fc2 is told fc1 sent the same command", async ({ browser }) => {
  const since = new Date().toISOString();
  const one = await browser.newPage();
  const two = await (await browser.newContext()).newPage();
  await login(one, "fc1");
  await login(two, "fc2");
  // both forms filled first, then the two Sends back to back: the 2 s window must not depend on how
  // fast a runner fills two forms (a CI run on main failed that way)
  await fillCommand(one, "NYX-TA1", "isolate_valve", { valve: "A" });
  await fillCommand(two, "NYX-TA1", "isolate_valve", { valve: "A" });
  await one.getByRole("region", { name: "Commands" }).getByRole("button", { name: "Send" }).click();
  await two.getByRole("region", { name: "Commands" }).getByRole("button", { name: "Send" }).click();
  await expect(two.getByRole("alert")).toHaveText(/^fc1 sent the same command \d+ s ago$/);
  const rejected = auditRows(since).filter((r) => r.actor === "fc2" && r.action === "CMD_REJECTED");
  expect(rejected.map((r) => r.reason)).toEqual(["DUPLICATE_COMMAND"]);
});
