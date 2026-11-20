// ATP-E5 / EXT-05: after a leak that was acknowledged with a note and cleared, a command and a dropout, the operator
// logs out and in again; Since… lists them in time order with the ACK note.
import { expect, test } from "@playwright/test";
import { ackLeftovers, clearFault, commandItem, fault, login, sendCommand, summary } from "./helpers";

const V = "NYX-TA1";
const NOTE = "since-summary handover note";
test.afterEach(async () => {
  await clearFault(V, "leak");
  await clearFault(V, "dropout");
});

test("Since… lists the leak ACK with its note, the command and the dropout in order after a logout and login", async ({ page }) => {
  test.setTimeout(150_000);
  await login(page, "fc1");
  const rows = page.locator("tr[data-alarm-id]").filter({ hasText: V }).filter({ hasText: "Cabin total pressure" });
  await ackLeftovers(page, rows);
  const t0 = `${new Date().toISOString().slice(0, 19)}Z`; // whole seconds, never after the first event

  await fault(V, "leak");
  await expect(rows.first()).toContainText(/RED LOW · \d+\.\d\d kPa/, { timeout: 40_000 });
  await page.waitForTimeout(2_500); // an ACK within 2 s of an escalation is superseded (P-pf42b)
  const ack = page.getByRole("button", { name: `Acknowledge ${V} cabin_pressure_kpa` });
  await expect(ack).toBeEnabled();
  await ack.click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge alarm" });
  await dialog.getByLabel("Note (required)").fill(NOTE);
  await dialog.getByRole("button", { name: "ACK" }).click();
  await expect(dialog).toBeHidden();
  await expect(rows.first()).toContainText("ACKNOWLEDGED", { timeout: 5_000 }); // applied, not superseded
  await clearFault(V, "leak");

  const sent = page.locator("li[data-cmd-id]").filter({ hasText: "isolate_valve" });
  const n = await sent.count();
  await sendCommand(page, V, "isolate_valve", { valve: "A" });
  await expect(sent).toHaveCount(n + 1);
  await expect(commandItem(page, V, "isolate_valve")).toHaveAttribute("data-state", "VERIFIED", { timeout: 20_000 });

  await fault(V, "dropout", { duration_s: 5 });
  await expect(summary(page, V)).toContainText("LOSS OF SIGNAL", { timeout: 10_000 });
  await expect(summary(page, V)).not.toContainText("LOSS OF SIGNAL", { timeout: 30_000 });

  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, "fc1");

  const panel = page.getByRole("region", { name: "Sessions" });
  await panel.getByRole("button", { name: "Since…" }).click();
  const since = page.getByRole("region", { name: "Since" });
  await since.getByLabel("Vehicle").selectOption(V);
  await since.getByLabel(/^Since \(UTC/).fill(t0);
  await since.getByRole("button", { name: "Show changes" }).click();
  await expect(since.getByRole("status")).toContainText(`Since ${t0.replace("Z", ".000Z")} (chosen time)`);
  await since.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/since-summary-list.png" });

  const items = since.locator("li");
  await expect(items.filter({ hasText: NOTE })).toHaveCount(1);
  const kinds = await items.evaluateAll((els) => els.map((e) => e.getAttribute("data-kind")));
  const at = (k: string) => kinds.indexOf(k);
  expect(at("ACK")).toBeGreaterThanOrEqual(0);
  expect(at("ACK")).toBeLessThan(at("COMMAND"));
  expect(at("COMMAND")).toBeLessThan(at("LOS"));
  expect(at("LOS")).toBeLessThan(at("AOS"));
  const times = await items.evaluateAll((els) => els.map((e) => e.querySelector("time")!.textContent!));
  expect(times).toEqual([...times].sort());
  expect(times.every((t) => t.endsWith("Z"))).toBe(true);

  // no since: the default is this user's last logout
  await since.getByLabel(/^Since \(UTC/).fill("");
  await since.getByRole("button", { name: "Show changes" }).click();
  await expect(since.getByRole("status")).toContainText("(your last logout)");
});
