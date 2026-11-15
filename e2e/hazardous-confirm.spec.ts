import { expect, test } from "@playwright/test";
import { API, apiLogin, commandItem, login, sendCommand } from "./helpers";

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

const AMBIGUOUS = "May have been sent \u2013 check the command list before sending again";

test("the confirm's reply is lost after the server queued it: 'may have been sent', no resend, queued exactly once (P-pf26)", async ({ page }) => {
  const reason = `lost reply ${Date.now().toString(36)}`;
  await login(page, "fc1");
  const cookie = await apiLogin("fc1");
  const listed = async () =>
    (await (await fetch(`${API}/api/commands?vehicle=NYX-TA2`, { headers: { cookie } })).json()) as { name: string; reason: string | null; queued_at: string }[];
  // the 2 s interlock (D-CMD-07) from the spec above's vent_cabin: wait it out on the server's own queued_at
  const lastVent = async () => Date.now() - Math.max(0, ...(await listed()).filter((c) => c.name === "vent_cabin").map((c) => Date.parse(c.queued_at)));
  await expect.poll(lastVent, { timeout: 5_000, intervals: [200] }).toBeGreaterThan(2_200);
  const confirms: number[] = []; // the server's status for every confirm POST that left the console
  await page.route("**/api/commands", async (route) => {
    const body = route.request().method() === "POST" ? (route.request().postDataJSON() as { confirm_token?: string }) : null;
    if (!body?.confirm_token) return route.continue();
    confirms.push((await route.fetch()).status()); // the server answers ...
    await route.abort("connectionreset"); // ... and the reply never reaches the console
  });
  await sendCommand(page, "NYX-TA2", "vent_cabin", { duration_s: "1" });
  const dialog = page.getByRole("dialog", { name: "Hazardous command" });
  await dialog.getByLabel("Reason (at least 10 characters)").fill(reason);
  await dialog.getByLabel("Type NYX-TA2 to confirm").fill("NYX-TA2");
  const refreshed = page.waitForRequest((r) => r.method() === "GET" && new URL(r.url()).pathname === "/api/commands");
  await dialog.getByRole("button", { name: "Confirm" }).click();

  await expect(dialog.getByRole("alert")).toHaveText(AMBIGUOUS);
  await refreshed; // the list is read again
  expect(confirms).toEqual([202]); // the server did queue it
  const confirm = dialog.getByRole("button", { name: "Confirm" });
  await expect(confirm).toBeDisabled(); // the dialog is dead: no resend from it
  await confirm.click({ force: true }).catch(() => {}); // not even a forced click
  const RESEND = /send the command again|try again|retry/i; // the list's own rows may say it of other commands
  await expect(dialog).not.toContainText(RESEND); // no resend is suggested
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  const panel = page.getByRole("region", { name: "Commands" });
  await expect(panel.getByRole("alert")).toHaveText(AMBIGUOUS); // still said, and nothing in the panel's messages offers a resend
  for (const t of await panel.locator(":scope > [role=status]").allTextContents()) expect(t).not.toMatch(RESEND);
  await expect(commandItem(page, "NYX-TA2", "vent_cabin (duration_s = 1)")).toBeVisible(); // the refreshed list has it

  expect(confirms).toEqual([202]); // one confirm POST left the console
  expect((await listed()).filter((c) => c.reason === reason)).toHaveLength(1); // queued exactly once
});
