import { defineConfig, devices } from "@playwright/test";

/**
 * Smoke-test config: chromium only, against the static file:// build produced
 * by injecting the dev fixture into the committed template (same path as
 * `loom visualize` — see e2e/smoke.spec.ts). No dev server involved.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  reporter: "list",
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
