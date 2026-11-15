// ATP-02 (§3.4 timeline, 60 s budget): leak → YELLOW (rate) → limits edit applied → RED (new
// limits hash) → ACK → isolate_valve → VERIFIED → DEESCALATE YELLOW → CLEARED. One beep only.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { ackLeftovers, beeps, clearFault, commandItem, editLimits, fault, login, restoreLimits, sendCommand } from "./helpers";

const V = "NYX-TA1";
const LIMITS = new URL("../mission/limits.yaml", import.meta.url);
const ACK_PROPAGATION_MS = 2_500; // P-pf42b: an ACK within 2 s of an escalation is superseded
const ACK_MS = 1_000; // click to ACKNOWLEDGED (ATP-02 brief); an overshoot is reported, never loosened

test.afterEach(async () => {
  restoreLimits();
  await clearFault(V, "leak");
});

test("leak → yellow → red → ack → isolate → verified → clear", async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, "fc1");
  const pressureRows = page.locator("tr[data-alarm-id]").filter({ hasText: V }).filter({ hasText: "Cabin total pressure" });
  await ackLeftovers(page, pressureRows); // e.g. a returned rate alarm from the system tests
  const b0 = await beeps(page);

  editLimits("yellow_high: 27.0", "yellow_high: 26.5"); // cabin_temp_c: a new limits set, same pressure limits
  const limits = createHash("sha256").update(readFileSync(LIMITS)).digest("hex").slice(0, 8);
  await expect(page.locator('li[data-kind="LIMITS_APPLIED"]').filter({ hasText: `limits ${limits}` }).first()).toBeVisible({ timeout: 10_000 });

  await fault(V, "leak");
  const alarm = pressureRows.first();
  await expect(alarm).toContainText(/YELLOW · RATE FALLING −0\.\d\d kPa\/s \(limit 0\.15\)/, { timeout: 5_000 });
  await expect(alarm.locator("th")).toContainText("YELLOW"); // severity cell: glyph + word
  expect(await beeps(page)).toBe(b0); // YELLOW: no beep

  await expect(alarm).toContainText(/RED LOW · \d+\.\d\d kPa \(limit 95\)/, { timeout: 30_000 });
  await expect(alarm.locator("th")).toContainText("RED");
  await expect(alarm.locator(".hash")).toContainText(`limits ${limits}`); // the escalation used the new set
  await expect.poll(() => beeps(page)).toBe(b0 + 1);
  await page.screenshot({ path: new URL("../docs/img/console-leak.png", import.meta.url).pathname }); // the alarm list and the first vehicle

  await page.waitForTimeout(ACK_PROPAGATION_MS);
  await alarm.getByRole("button", { name: `Acknowledge ${V} cabin_pressure_kpa` }).click();
  const dialog = page.getByRole("dialog", { name: "Acknowledge alarm" });
  await dialog.getByLabel("Note (required)").fill("leak seen, isolating");
  const t = Date.now();
  await dialog.getByRole("button", { name: "ACK" }).click();
  await expect(alarm).toContainText(/ACK PENDING \(by fc1\)|ACKNOWLEDGED/);
  await expect(alarm).toContainText("ACKNOWLEDGED", { timeout: ACK_MS }); // the brief: ACKED within 1 s
  const ackMs = Date.now() - t;
  console.log(`ACK applied after ${ackMs} ms`);
  expect(ackMs, "click to ACKNOWLEDGED").toBeLessThanOrEqual(ACK_MS);

  await sendCommand(page, V, "isolate_valve", { valve: "A" });
  const item = commandItem(page, V, "isolate_valve (valve = A)");
  await expect(item).toHaveAttribute("data-state", "VERIFIED", { timeout: 20_000 });
  for (const step of ["QUEUED", "SENT", "RECEIVED", "VERIFIED"])
    await expect(item.locator("li[data-reached]").filter({ hasText: new RegExp(`^${step} \\d\\d:\\d\\d:\\d\\d(\\.\\d+)?Z$`) })).toHaveCount(1);

  await expect(alarm).toContainText(/YELLOW LOW/, { timeout: 30_000 }); // repressurising: DEESCALATE
  await expect(pressureRows).toHaveCount(0, { timeout: 30_000 }); // acknowledged and back: CLEARED
  expect(await beeps(page)).toBe(b0 + 1);
});
