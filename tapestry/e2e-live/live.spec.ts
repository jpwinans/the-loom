import { expect, test } from "@playwright/test";

/**
 * Live-mode smoke: against a real `loom serve` process (seeded by
 * scripts/seed_live_dev.py — no embeddings). Asserts the boot path (marker →
 * /api/bundle → Explorer renders), the live indicator, the graph switcher, and
 * the refresh button. Semantic/search live features need vectors and are covered
 * locally, not here.
 */
test("live mode boots, shows the indicator, switches graphs, and refreshes", async ({ page }) => {
  await page.goto("/");

  // The app fetched /api/bundle and built the graph — Sigma's canvas mounts.
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();

  // The live indicator is present (absent in the static file:// build).
  await expect(page.getByRole("status", { name: "Live server" })).toBeVisible();

  // The switcher lists the two seeded graphs and can switch.
  const select = page.locator(".live__select");
  await expect(select).toBeVisible();
  await select.selectOption("tapestry-alt");
  await expect(page.locator(".brand__graph")).toHaveText("tapestry-alt");

  // Refresh re-fetches without error (Explorer still renders).
  await page.getByRole("button", { name: /refresh/i }).click();
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();
});
