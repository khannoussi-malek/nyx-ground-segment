import { defineConfig } from "@playwright/test";

process.loadEnvFile("../.env");

// The soak's console watcher (I4), run by chaos/soak.py; not part of `make e2e`.
export default defineConfig({
  testDir: "../chaos",
  testMatch: "watch_console.spec.ts",
  outputDir: process.env.CHAOS_WATCH_TRACE ?? "../chaos/out/trace",
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${process.env.API_HOST_PORT ?? "8000"}`,
    trace: process.env.CI ? "off" : "retain-on-failure", // traces record the login POST body (password); never in CI artifacts
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
