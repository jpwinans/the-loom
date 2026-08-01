import { defineConfig, devices } from "@playwright/test";

/**
 * Live-mode config: a separate project pointing at a running `loom serve`
 * process. No `webServer` here — CI (and the local run instructions in the
 * phase-4 plan) start the server explicitly so startup ordering is
 * deterministic (seed the graphs, then serve, then wait for /api/graphs).
 */
const PORT = process.env.LIVE_PORT ?? "8100";

export default defineConfig({
  testDir: "e2e-live",
  fullyParallel: false,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, ...devices["Desktop Chrome"] },
  projects: [{ name: "chromium-live", use: { ...devices["Desktop Chrome"] } }],
});
