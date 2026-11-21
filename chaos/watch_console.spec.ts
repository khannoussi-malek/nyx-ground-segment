// I4 (design 11.5, D-EXT-15): the console never shows a row as OK (data-acq="OK") while its value is
// older than the stale threshold. Sampled in the page every 250 ms until chaos.soak creates the stop file.
// The limit is the vehicle's own stale threshold, which the value cell exposes as data-stale-ms
// (max(2 s, tm.stale_ms)), plus the UI-02 tolerance of 500 ms (A-EXT-11). Run by chaos.soak through e2e/chaos.config.ts; the result is a JSON file.
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, login, test } from "../e2e/helpers";

const TOLERANCE_MS = 500;
const OVERRIDE_MS = process.env.CHAOS_I4_MAX_MS ?? ""; // CHAOS_I4_MAX_MS=0: self-check that rows are seen
const STOP = process.env.CHAOS_WATCH_STOP ?? "chaos/out/watch.stop";
const OUT = process.env.CHAOS_WATCH_OUT ?? "chaos/out/watch.json";
const TRACE_DIR = process.env.CHAOS_WATCH_TRACE ?? "chaos/out/trace";

test("I4: no OK row shows data older than the stale threshold", async ({ page }) => {
  test.setTimeout(0); // ends when the soak says so
  // a non-numeric limit would make `age > NaN` false for ever: I4 would pass without checking
  if (OVERRIDE_MS !== "" && !Number.isFinite(Number(OVERRIDE_MS)))
    throw new Error(`CHAOS_I4_MAX_MS is not a number: ${OVERRIDE_MS}`);
  await login(page, "fc1");
  // started after the login, without DOM snapshots: the trace never holds the password or the
  // login POST body (the config has tracing off), and stays small over 30 min
  await page.context().tracing.start({ screenshots: true, snapshots: false });
  await page.evaluate(({ tol, override }) => {
    const w = window as unknown as {
      __i4: { samples: number; rows: number; bad: string[]; blind_ms: number };
    };
    w.__i4 = { samples: 0, rows: 0, bad: [], blind_ms: 0 };
    let lastOk = Date.now(); // the longest stretch with no OK row at all: a blank console shows
    const report = (msg: string) => {
      if (w.__i4.bad.length < 50) w.__i4.bad.push(`${new Date().toISOString()} ${msg}`);
    };
    setInterval(() => {
      w.__i4.samples++;
      let ok = 0;
      for (const td of document.querySelectorAll<HTMLElement>("td[data-t-onboard]")) {
        if (td.closest("tbody")?.getAttribute("data-acq") !== "OK") continue;
        ok++;
        w.__i4.rows++;
        const who = `${td.dataset.vehicle} ${td.dataset.param}`;
        const stale = Number(td.dataset.staleMs);
        if (override === "" && (td.dataset.staleMs === undefined || !Number.isFinite(stale))) {
          report(`${who} malformed row: no data-stale-ms`); // never a silent default
          continue;
        }
        const age = Date.now() - Date.parse(td.dataset.tOnboard ?? "");
        const limit = override !== "" ? Number(override) : Math.max(2000, stale) + tol;
        if (age > limit) report(`${who} OK at ${age} ms`);
      }
      const now = Date.now();
      if (ok > 0) lastOk = now;
      w.__i4.blind_ms = Math.max(w.__i4.blind_ms, now - lastOk);
    }, 250);
  }, { tol: TOLERANCE_MS, override: OVERRIDE_MS });
  while (!existsSync(STOP)) await page.waitForTimeout(1000);
  const r = await page.evaluate(() => (window as unknown as { __i4: object }).__i4);
  writeFileSync(OUT, JSON.stringify(r));
  const bad = (r as { bad: string[] }).bad;
  await page.context().tracing.stop(bad.length ? { path: join(TRACE_DIR, "trace.zip") } : undefined);
  expect(bad).toEqual([]);
});
