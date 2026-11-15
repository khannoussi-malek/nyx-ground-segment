import { defineConfig } from "@playwright/test";

// run from the repo root: npx --prefix e2e playwright test -c perf/playwright.config.ts (make perf does)
process.loadEnvFile(".env");
const duration = Number(process.env.PERF_DURATION_S ?? 600);

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  workers: 1,
  timeout: (duration + 180) * 1000, // the whole run is one test
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${process.env.API_HOST_PORT ?? "8000"}`,
    trace: "off", // traces record the login POST body (password)
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
