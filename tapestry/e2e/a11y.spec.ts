import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Automated accessibility audit of the built, self-contained Tapestry page —
 * the same artifact `loom visualize` writes. Mirrors smoke.spec.ts's fixture
 * injection (inject the committed dev fixture into the committed template, open
 * via `file://`), then runs axe-core over each of the five view panels in both
 * themes plus the header/help-overlay and the Explorer's saved-views panel.
 *
 * The bar is zero `serious`/`critical` violations. Moderate/minor findings
 * (e.g. best-practice landmark advice) are out of scope for this gate; the
 * filter below is deliberate. The WebGL `<canvas>` sigma mounts inside each
 * labelled `role="tabpanel"` carries no text alternative — that is expected for
 * a canvas whose enclosing panel already names it, and axe's default WCAG rules
 * do not flag a bare canvas, so no exclusion is needed.
 */
const OUT = join(tmpdir(), "tapestry-e2e-a11y.html");

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

// Audit the settled resting state, not mid-animation frames. tokens.css already
// zeroes every transition under `prefers-reduced-motion: reduce` — but the
// `reducedMotion: "reduce"` *context* option (below) is unreliable for a
// `file://` document's very first paint on some Chromium builds: the media
// feature can still read `no-preference` for the initial navigation, so
// `openThemed` also calls `page.emulateMedia()` explicitly after `goto`,
// which reliably flips it before any control is touched. Belt-and-suspenders
// keeps the context option too — it costs nothing and covers other engines.
test.use({ reducedMotion: "reduce" });

const TABS = ["explorer", "overview", "systems", "chronicle", "semantic"] as const;

/** Only `serious`/`critical` impacts gate this suite. */
function seriousOrCritical(results: Awaited<ReturnType<AxeBuilder["analyze"]>>) {
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

/**
 * Click a theme radio and wait for `<html data-theme>` to actually carry the
 * new value before scanning. `applyTheme` (design/theme.ts) stamps that
 * attribute from a `useEffect`, one tick after the click's state update, and
 * every themed colour token cascades from it — scanning immediately after
 * `.click()` can catch axe mid-cascade (a still-transitioning `color`, sampled
 * partway between the old and new theme), producing a flaky, borderline
 * color-contrast violation on whichever control happens to be mid-transition.
 * Waiting for the attribute itself — not a fixed delay — makes the scan
 * deterministic once transitions are actually disabled (see `openThemed`).
 */
async function selectTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.getByRole("radio", { name: new RegExp(`^${theme}$`, "i") }).click();
  await page.waitForFunction(
    (expected) => document.documentElement.dataset.theme === expected,
    theme,
  );
}

/**
 * Open the built page and select `theme`, with `prefers-reduced-motion`
 * force-emulated *after* navigation (see the note on `test.use` above) so
 * every subsequent colour transition — including the one triggered by the
 * theme click itself — resolves instantly instead of animating over
 * `--dur-fast` (90ms), which is what actually made this suite flaky.
 */
async function openThemed(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.goto("file://" + OUT);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await selectTheme(page, theme);
}

for (const theme of ["light", "dark"] as const) {
  for (const tab of TABS) {
    test(`no serious/critical a11y violations: ${tab} (${theme})`, async ({ page }) => {
      await openThemed(page, theme);
      await page.getByRole("tab", { name: new RegExp(`^${tab}$`, "i") }).click();
      await expect(page.locator(`#panel-${tab}`)).toBeVisible();
      const results = await new AxeBuilder({ page }).include(`#panel-${tab}`).analyze();
      const bad = seriousOrCritical(results);
      expect(bad, JSON.stringify(bad, null, 2)).toEqual([]);
    });
  }
}

for (const theme of ["light", "dark"] as const) {
  test(`no serious/critical a11y violations: header chrome (${theme})`, async ({ page }) => {
    await openThemed(page, theme);
    // The header at rest, on its own solid surfaces: tabs, theme radiogroup,
    // brand counts, help trigger. This is where header colour-contrast surfaces.
    const results = await new AxeBuilder({ page }).include(".app__header").analyze();
    const bad = seriousOrCritical(results);
    expect(bad, JSON.stringify(bad, null, 2)).toEqual([]);
  });

  test(`no serious/critical a11y violations: help overlay (${theme})`, async ({ page }) => {
    await openThemed(page, theme);
    await page.getByRole("button", { name: "Keyboard shortcuts" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    // Scope to the dialog's own surface. A whole-document scan while the modal is
    // open would flag the header *through* the translucent backdrop — axe
    // composites the two layers and reports the reduced ratio — which is a
    // scan artifact, not a real solid-surface failure; the header-chrome scan
    // above already audits that chrome on its true background.
    const results = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    const bad = seriousOrCritical(results);
    expect(bad, JSON.stringify(bad, null, 2)).toEqual([]);
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`no serious/critical a11y violations: Explorer saved-views panel (${theme})`, async ({
    page,
  }) => {
    await openThemed(page, theme);
    await expect(page.locator("#panel-explorer")).toBeVisible();
    // Open the saved-views/exports panel (Task 1's management surface) so its
    // inline inputs, export/import controls, and saved rows are audited.
    await page.getByRole("button", { name: /views/i }).click();
    await expect(page.locator("#views-panel")).toBeVisible();
    const results = await new AxeBuilder({ page }).include("#views-panel").analyze();
    const bad = seriousOrCritical(results);
    expect(bad, JSON.stringify(bad, null, 2)).toEqual([]);
  });
}
