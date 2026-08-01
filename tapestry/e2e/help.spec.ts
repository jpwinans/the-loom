import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Help overlay + canvas keyboard-alternative coverage, against the built,
 * self-contained page (mirroring smoke.spec.ts's fixture injection). The `?`
 * dialog is a real focus-trapped modal — it opens on the key and a header
 * button, keeps Tab inside, and restores focus to its trigger on Escape. The
 * Semantic Map's cluster-brush gives a keyboard user the lasso's core action
 * (brush a set → view in Explorer) without a pointer.
 */
const OUT = join(tmpdir(), "tapestry-e2e-help.html");

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

test("the ? key opens a focus-trapped help dialog that Escape closes, restoring focus", async ({
  page,
}) => {
  await page.goto("file://" + OUT);

  // Wait for the app to boot before pressing a key. `keyboard.press` dispatches
  // to whatever is there and never auto-waits, and the dialog assertion below
  // is satisfied by an unmounted page too — so without this the key can land
  // before App's window keydown listener is attached. Same boot check as
  // smoke.spec.ts.
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();

  // Nothing open at rest.
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.keyboard.press("?");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");

  // Focus opens inside the dialog (on the close button) and stays trapped
  // across repeated Tabs.
  await expect(dialog.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.locator(":focus")).toHaveCount(1);

  // Escape closes it and returns focus to the header ? trigger.
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Keyboard shortcuts" })).toBeFocused();
});

test("the header ? button opens the dialog by mouse too", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("button", { name: "Keyboard shortcuts" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Keyboard shortcuts" })).toBeVisible();
});

test("the Semantic cluster-brush brushes a cluster from the keyboard", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Semantic" }).click();

  const panel = page.locator("#panel-semantic");
  await expect(panel.locator(".semantic__canvas canvas").first()).toBeVisible();

  // Open the cluster picker and brush the fixture's one "concept" cluster
  // (3 members) — the same effect the pointer lasso produces.
  const brushToggle = panel.getByRole("button", { name: /brush cluster/i });
  await brushToggle.focus();
  await expect(brushToggle).toHaveAttribute("aria-expanded", "false");
  await brushToggle.click();
  await expect(brushToggle).toHaveAttribute("aria-expanded", "true");

  await panel.locator(".semantic__clusteritem").first().click();
  await expect(panel.locator(".semantic__brushcount")).toHaveText("3 brushed");

  // The brush carries across to the Explorer via the same chip.
  await panel.getByRole("button", { name: /view in explorer/i }).click();
  await expect(page.locator("#panel-explorer .explorer__brushcount")).toHaveText("3 brushed");
});
