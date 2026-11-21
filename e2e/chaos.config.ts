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
    trace: "off", // a trace of the login holds the password; the spec traces after login (no snapshots)
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
