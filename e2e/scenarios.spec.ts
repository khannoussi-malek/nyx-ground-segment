// Scenarios panel (D-SIM-12): start a cabin leak from the console, see RED LOW, clear it from the console.
import { expect, test } from "@playwright/test";
import { ackLeftovers, clearFault, login } from "./helpers";

const V = "NYX-TA1";
test.afterEach(async () => clearFault(V, "leak"));

test("Scenarios: cabin leak shows RED LOW cabin pressure, Clear stops it", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "fc1");
  const rows = page.locator("tr[data-alarm-id]").filter({ hasText: V }).filter({ hasText: "Cabin total pressure" });
  await ackLeftovers(page, rows);
  await page.getByRole("button", { name: "Scenarios" }).click();
  const panel = page.getByRole("region", { name: "Test scenarios" });
  await expect(panel).toContainText("Not an operator function");
  await expect(panel.getByRole("button", { name: "Cabin leak", exact: true })).toBeDisabled(); // no vehicle yet
  await panel.getByLabel("Vehicle").selectOption(V);
  await panel.getByRole("button", { name: "Cabin leak", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText(new RegExp(`Cabin leak started on ${V} at \\d\\d:\\d\\d:\\d\\dZ`));
  await expect(rows.first()).toContainText(/RED LOW · \d+\.\d\d kPa/, { timeout: 40_000 });
  await page.screenshot({ path: "test-results/scenarios-panel.png" });
  await panel.getByRole("button", { name: "Clear Cabin leak" }).click();
  await expect(panel.getByRole("status")).toContainText("Cabin leak cleared");
});
