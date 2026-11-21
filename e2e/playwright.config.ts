import { defineConfig } from "@playwright/test";

process.loadEnvFile("../.env");

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  globalSetup: "./global-setup.ts", // refuses a mission/ left edited by an interrupted run
  workers: 1, // the specs restart the shared api container
  reporter: [["list"], ["html", { open: "never" }], ["json", { outputFile: "playwright-results.json" }]], // the json file feeds the as-run verification matrix
  use: {
    baseURL: `http://127.0.0.1:${process.env.API_HOST_PORT ?? "8000"}`,
    trace: process.env.CI ? "off" : "retain-on-failure", // traces record the login POST body (password); never in CI artifacts
    video: process.env.DEMO_VIDEO === "1" ? "on" : "off", // the Gate 5 demo (docs/demo); off everywhere else
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
