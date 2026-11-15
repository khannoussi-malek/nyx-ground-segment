import { expect, test } from "@playwright/test";
import { login, sendCommand } from "./helpers";

const REASON = "venting for the confirm spec";

test("Confirm stays disabled for a wrong, partial or lowercase vehicle id; the exact id enables it", async ({ page }) => {
  await login(page, "fc1");
  await sendCommand(page, "NYX-TA2", "vent_cabin", { duration_s: "2" });
  const dialog = page.getByRole("dialog", { name: "Hazardous command" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Reason (at least 10 characters)").fill(REASON);
  const typed = dialog.getByLabel("Type NYX-TA2 to confirm");
  const confirm = dialog.getByRole("button", { name: "Confirm" });
  for (const wrong of ["NYX-TA1", "NYX-TA", "nyx-ta2", " NYX-TA2", "NYX-TA2 "]) {
    await typed.fill(wrong);
    await expect(confirm, wrong).toBeDisabled();
  }
  await typed.fill("NYX-TA2");
  await expect(confirm).toBeEnabled();
  await dialog.getByLabel("Reason (at least 10 characters)").fill("too short");
  await expect(confirm).toBeDisabled(); // the reason needs 10 characters
  await dialog.getByLabel("Reason (at least 10 characters)").fill(REASON);
  await confirm.click(); // the second step completes
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("status").filter({ hasText: "vent_cabin (duration_s = 2) queued for NYX-TA2" })).toBeVisible();
});
