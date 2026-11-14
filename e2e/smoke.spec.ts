import { execSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

const VEHICLES = ["NYX-TA1", "NYX-TA2"];
const cell = (page: Page, v: string) =>
  page.locator(`[data-vehicle="${v}"][data-param="cabin_pressure_kpa"]`);
const seqOf = (page: Page, v: string) => async () => Number(await cell(page, v).getAttribute("data-seq"));

async function login(page: Page, user: string, pw: string | undefined) {
  if (!pw) throw new Error(`no demo password for ${user} in .env`);
  await page.goto("/");
  await page.getByLabel("User").fill(user);
  await page.getByLabel("Password").fill(pw);
  await page.getByRole("button", { name: "Log in" }).click();
}

const restartApi = () => execSync("docker compose restart api", { cwd: "..", stdio: "ignore" });

test("login, live values update, recovers after api restart, logout", async ({ page }) => {
  await login(page, "fc1", process.env.DEMO_PW_FC1);
  for (const v of VEHICLES) {
    await expect(cell(page, v)).toBeVisible();
    const s0 = await seqOf(page, v)();
    await expect.poll(seqOf(page, v), { timeout: 3000 }).toBeGreaterThan(s0);
  }

  const t0 = Date.now(); // proves early: SSE reconnect through compose
  restartApi();
  const s1 = await seqOf(page, "NYX-TA1")();
  await expect.poll(seqOf(page, "NYX-TA1"), { timeout: 5000 }).toBeGreaterThan(s1);
  const ms = Date.now() - t0;
  console.log(`reconnect after api restart: ${ms} ms`);
  expect(ms).toBeLessThan(5000); // UI-07 budget

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
});

test("a revoked session returns to the login form", async ({ page }) => {
  await login(page, "fc2", process.env.DEMO_PW_FC2);
  await expect(cell(page, "NYX-TA1")).toBeVisible();
  // another "context": the same cookie jar logs out server-side
  const r = await page.context().request.post("/api/logout", { headers: { "X-GS-Client": "console" } });
  expect(r.ok()).toBeTruthy();
  restartApi(); // drops the stream; the console's session check then finds it dead
  await expect(page.getByRole("button", { name: "Log in" })).toBeVisible({ timeout: 15000 });
  await expect(cell(page, "NYX-TA1")).toHaveCount(0);
});
