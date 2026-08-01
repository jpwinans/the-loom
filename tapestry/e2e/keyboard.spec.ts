import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Keyboard-operability coverage for the header's two composite widgets — the
 * view tablist and the theme radiogroup — against the built, self-contained
 * page (mirroring smoke.spec.ts's fixture injection so it exercises the shipped
 * artifact). Each is a single tab stop whose arrow keys move focus + selection
 * within it (the WAI-ARIA tabs / radio-group patterns), and a keyboard-focused
 * control shows the visible focus ring.
 */
const OUT = join(tmpdir(), "tapestry-e2e-keyboard.html");

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

test("arrow keys drive the tablist as one tab stop, with a visible focus ring", async ({ page }) => {
  await page.goto("file://" + OUT);

  const explorerTab = page.getByRole("tab", { name: "Explorer" });
  const overviewTab = page.getByRole("tab", { name: "Overview" });
  const semanticTab = page.getByRole("tab", { name: "Semantic" });

  // The active tab is the group's single tab stop (tabindex 0); the rest are
  // tabindex -1. Focus it, then drive with arrows.
  await explorerTab.focus();
  await expect(explorerTab).toBeFocused();

  // ArrowRight moves selection AND focus to the next tab (automatic activation,
  // matching the click behaviour), the panel changes with it, and the
  // keyboard-focused tab shows the visible focus ring.
  await page.keyboard.press("ArrowRight");
  await expect(overviewTab).toBeFocused();
  await expect(overviewTab).toHaveAttribute("aria-selected", "true");
  await expect(explorerTab).toHaveAttribute("aria-selected", "false");
  await expect(overviewTab).toHaveCSS("outline-style", "solid");
  await expect(page.locator("#panel-overview")).toBeVisible();

  // Home returns to the first tab.
  await page.keyboard.press("Home");
  await expect(explorerTab).toBeFocused();
  await expect(explorerTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-explorer")).toBeVisible();

  // End jumps to the last tab.
  await page.keyboard.press("End");
  await expect(semanticTab).toBeFocused();
  await expect(semanticTab).toHaveAttribute("aria-selected", "true");
});

test("arrow keys drive the theme radiogroup as one tab stop", async ({ page }) => {
  await page.goto("file://" + OUT);

  const autoRadio = page.getByRole("radio", { name: "Auto" });
  const lightRadio = page.getByRole("radio", { name: "Light" });
  const darkRadio = page.getByRole("radio", { name: "Dark" });

  // Auto is checked by default and is the group's single tab stop.
  await autoRadio.focus();
  await expect(autoRadio).toHaveAttribute("aria-checked", "true");

  // ArrowRight moves focus + checks the next radio (keyboard-focused → ring shown).
  await page.keyboard.press("ArrowRight");
  await expect(lightRadio).toBeFocused();
  await expect(lightRadio).toHaveAttribute("aria-checked", "true");
  await expect(lightRadio).toHaveCSS("outline-style", "solid");

  // ArrowLeft wraps from Auto back to Dark.
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowLeft");
  await expect(darkRadio).toBeFocused();
  await expect(darkRadio).toHaveAttribute("aria-checked", "true");
});

test("a polite live region is present for async surface changes", async ({ page }) => {
  await page.goto("file://" + OUT);
  // The region ships in the DOM (empty at load — content present at load is
  // never announced) so graph switches and live refreshes have somewhere to
  // announce politely.
  const liveRegion = page.locator("div.sr-only[aria-live='polite']");
  await expect(liveRegion).toHaveCount(1);
});
