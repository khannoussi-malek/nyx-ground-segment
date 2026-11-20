// ATP-E5 / EXT-05, the shift handover: fc1 logs out (end of shift); fc2 runs a leak (ACK with a note, cleared),
// a command and a 10 s dropout; fc1 logs in again and Since… with NO since (the default: fc1's last logout)
// lists all of it in time order.
import { expect, test } from "@playwright/test";
import { ackLeftovers, clearFault, commandItem, fault, login, sendCommand, summary } from "./helpers";

const V = "NYX-TA1";
const NOTE = "since-summary handover note";
test.afterEach(async () => {
  await clearFault(V, "leak");
  await clearFault(V, "dropout");
});

test("Since… with the default since lists the leak, its ACK note, the command and the dropout after fc1's logout", async ({ browser }) => {
  test.setTimeout(180_000);
  const one = await (await browser.newContext()).newPage();
  const two = await (await browser.newContext()).newPage();
  const rows = (p: typeof one) => p.locator("tr[data-alarm-id]").filter({ hasText: V }).filter({ hasText: "Cabin total pressure" });

  // fc1 ends the shift (leftover alarms of earlier runs are acknowledged first, so they cannot disturb the handover)
  await login(one, "fc1");
  await ackLeftovers(one, rows(one));
  await one.getByRole("button", { name: "Log out" }).click();

  // fc2 runs the shift
  await login(two, "fc2");
  await fault(V, "leak");
  await expect(rows(two).first()).toContainText(/RED LOW · \d+\.\d\d kPa/, { timeout: 40_000 });
  await two.waitForTimeout(2_500); // an ACK within 2 s of an escalation is superseded (P-pf42b)
  const ack = two.getByRole("button", { name: `Acknowledge ${V} cabin_pressure_kpa` });
  await expect(ack).toBeEnabled();
  await ack.click();
  const dialog = two.getByRole("dialog", { name: "Acknowledge alarm" });
  await dialog.getByLabel("Note (required)").fill(NOTE);
  await dialog.getByRole("button", { name: "ACK" }).click();
  await expect(dialog).toBeHidden();
  await expect(rows(two).first()).toContainText("ACKNOWLEDGED", { timeout: 5_000 }); // applied, not superseded
  await clearFault(V, "leak");
  await expect(rows(two)).toHaveCount(0, { timeout: 40_000 }); // back in limits and acknowledged: cleared

  const sent = two.locator("li[data-cmd-id]").filter({ hasText: "isolate_valve" });
  const n = await sent.count();
  await sendCommand(two, V, "isolate_valve", { valve: "A" });
  await expect(sent).toHaveCount(n + 1);
  await expect(commandItem(two, V, "isolate_valve")).toHaveAttribute("data-state", "VERIFIED", { timeout: 20_000 });

  await fault(V, "dropout", { duration_s: 10 });
  await expect(summary(two, V)).toContainText("LOSS OF SIGNAL", { timeout: 10_000 });
  await expect(summary(two, V)).not.toContainText("LOSS OF SIGNAL", { timeout: 40_000 });

  // fc1 starts the next shift: Since… with no since
  await login(one, "fc1");
  await one.getByRole("region", { name: "Sessions" }).getByRole("button", { name: "Since…" }).click();
  const since = one.getByRole("region", { name: "Since" });
  await since.getByLabel("Vehicle").selectOption(V);
  const items = since.locator("li");
  const list = async () => items.evaluateAll((els) => els.map((e) => `${e.getAttribute("data-kind")}|${e.textContent}`));
  await expect.poll(async () => {
    await since.getByRole("button", { name: "Show changes" }).click();
    return (await list()).some((l) => l.startsWith("AOS|"));
  }, { timeout: 30_000 }).toBe(true);
  await expect(since.getByRole("status")).toContainText("(your last logout)");
  await expect(since.getByLabel(/^Since \(UTC/)).toHaveValue("");

  const all = await list();
  const at = (kind: string, re: RegExp) => all.findIndex((l) => l.startsWith(`${kind}|`) && re.test(l));
  const order = [
    at("RAISE", /cabin_pressure_kpa/),
    at("ACK", new RegExp(`acknowledged by fc2: ${NOTE}`)),
    Math.max(at("CLEAR", /cabin_pressure_kpa/), at("RTN", /cabin_pressure_kpa/)),
    at("COMMAND", /fc2 sent isolate_valve.*VERIFIED/),
    at("LOS", /LOS since/),
    at("AOS", /frames? lost/),
  ];
  expect(order.every((i) => i >= 0), `all six items listed: ${JSON.stringify(order)}\n${all.join("\n")}`).toBe(true);
  expect(order).toEqual([...order].sort((a, b) => a - b));
  const times = await items.evaluateAll((els) => els.map((e) => e.querySelector("time")!.textContent!));
  expect(times).toEqual([...times].sort());
  expect(times.every((t) => t.endsWith("Z"))).toBe(true);

  await one.getByRole("region", { name: "Sessions" }).evaluate((e) => e.scrollTo(0, e.scrollHeight)); // the pane is the one scroll area
  await one.screenshot({ path: "test-results/since-summary-list.png" });
});
