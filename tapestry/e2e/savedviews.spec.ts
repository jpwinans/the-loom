import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Saved-view management e2e coverage — save, rename (with collision refusal),
 * export, import, and apply-on-load via `#view=<name>` — against the built,
 * self-contained Tapestry page. Mirrors smoke.spec.ts's fixture-injection
 * `beforeAll` so this stays honest about the shipped artifact, not just what
 * `npm run dev` serves. Each Playwright test gets its own browser context (a
 * fresh `localStorage`), so tests don't leak saved views into one another.
 */
const OUT = join(tmpdir(), "tapestry-e2e-savedviews.html");

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

async function openViews(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Views" }).click();
}

test("saves views, refuses a rename collision, renames, and exports as JSON", async ({ page }) => {
  await page.goto("file://" + OUT);
  await openViews(page);

  await page.getByLabel("View name").fill("alpha");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".views__itemname")).toHaveText(["alpha"]);

  await page.getByLabel("View name").fill("beta");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".views__itemname")).toHaveText(["alpha", "beta"]);

  // Renaming "alpha" to the already-taken "beta" is refused with an inline
  // notice, and mutates nothing.
  await page.getByRole("button", { name: "Rename saved view alpha" }).click();
  await page.locator(".views__renameinput").fill("beta");
  await page.getByRole("button", { name: "Confirm rename" }).click();
  await expect(page.locator(".views__renameerror")).toContainText("already taken");
  await page.getByRole("button", { name: "Cancel rename" }).click();
  await expect(page.locator(".views__itemname")).toHaveText(["alpha", "beta"]);

  // A non-colliding rename applies immediately and preserves the row's
  // position (renamed in place, not re-sorted).
  await page.getByRole("button", { name: "Rename saved view alpha" }).click();
  await page.locator(".views__renameinput").fill("alpha-renamed");
  await page.getByRole("button", { name: "Confirm rename" }).click();
  await expect(page.locator(".views__itemname")).toHaveText(["alpha-renamed", "beta"]);

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Export" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^tapestry-dev-views-\d{4}-\d{2}-\d{2}\.json$/);
});

test("imports a saved-views JSON file, repopulates the list, and applies on click", async ({ page }) => {
  await page.goto("file://" + OUT);
  await openViews(page);
  await expect(page.locator(".views__empty")).toBeVisible();

  const hash = "#s=" + encodeURIComponent(JSON.stringify({ view: "systems" }));
  const payload = join(tmpdir(), "tapestry-e2e-import.json");
  writeFileSync(
    payload,
    JSON.stringify({
      schema: "tapestry-views@1",
      graph: "tapestry-dev",
      views: [{ name: "imported-view", hash, savedAt: new Date().toISOString() }],
    }),
  );

  await page.locator('input[type="file"]').setInputFiles(payload);
  await expect(page.locator(".views__notice")).toContainText("Imported 1 saved view");
  await expect(page.locator(".views__itemname")).toHaveText(["imported-view"]);

  // Applying a saved view takes effect immediately (no reload) and closes
  // the panel.
  await page.locator(".views__itemapply").click();
  await expect(page.locator("#views-panel")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Systems" })).toHaveAttribute("aria-selected", "true");
});

test("a malformed import file reports an error and keeps existing views intact", async ({ page }) => {
  await page.goto("file://" + OUT);
  await openViews(page);
  await page.getByLabel("View name").fill("keep-me");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  const bad = join(tmpdir(), "tapestry-e2e-import-bad.json");
  writeFileSync(bad, "not json");
  await page.locator('input[type="file"]').setInputFiles(bad);

  await expect(page.locator(".views__notice--error")).toBeVisible();
  await expect(page.locator(".views__itemname")).toHaveText(["keep-me"]);
});

test("#view=<name> on load applies the named saved view", async ({ page }) => {
  await page.goto("file://" + OUT);

  // The Views panel only lives in the Explorer, so a UI-driven save can only
  // ever capture "explorer" as the hash's view — reaching the Systems tab
  // this way exercises the same import path a real cross-machine saved-views
  // file would (see the import test above), which is the realistic way a
  // saved view would ever point at a non-Explorer tab.
  await openViews(page);
  const hash = "#s=" + encodeURIComponent(JSON.stringify({ view: "systems" }));
  const payload = join(tmpdir(), "tapestry-e2e-import-viewload.json");
  writeFileSync(
    payload,
    JSON.stringify({
      schema: "tapestry-views@1",
      graph: "tapestry-dev",
      views: [{ name: "sys-check", hash, savedAt: new Date().toISOString() }],
    }),
  );
  await page.locator('input[type="file"]').setInputFiles(payload);
  await expect(page.locator(".views__itemname")).toHaveText(["sys-check"]);

  // Opening `#view=sys-check` resolves the saved view for this browser +
  // graph and applies it on mount — no click required. A real deep link
  // arrives with the hash already present on the very first load, so leave
  // the document first (`page.goto` from the bare URL to the same URL plus a
  // fragment is a same-document anchor jump in Chromium — it would not
  // re-run the mount effect and would give a false pass/fail either way).
  await page.goto("about:blank");
  await page.goto("file://" + OUT + "#view=sys-check");
  await expect(page.getByRole("tab", { name: "Systems" })).toHaveAttribute("aria-selected", "true");
});

test("#view=<name> for an unknown name falls through without erroring", async ({ page }) => {
  await page.goto("file://" + OUT + "#view=does-not-exist");
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();
  await expect(page.getByRole("tab", { name: "Explorer" })).toHaveAttribute("aria-selected", "true");
});
