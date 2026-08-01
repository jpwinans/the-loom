import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Export coverage for every view (Task 5): each Sigma view (Explorer, Systems,
 * Chronicle, Semantic) triggers a real browser download with the
 * `<graph>-<view>-<date>` filename convention for both PNG and SVG; the DOM
 * Overview exposes its Print/Save-as-PDF path instead (asserted as wiring only
 * — `window.print()` opens a native dialog Playwright cannot drive headlessly).
 * Mirrors smoke.spec.ts's fixture-injection `beforeAll` so this exercises the
 * built, self-contained page — the same artifact `loom visualize` writes.
 */
const OUT = join(tmpdir(), "tapestry-e2e-export.html");

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

// Exercise the resting state, not a mid-layout frame — matches a11y.spec.ts's
// rationale (tokens.css zeroes transitions under reduced motion; the physics
// loop itself is a JS/rAF timer this does not touch, so it is not what this
// setting is stabilizing here — it keeps any CSS-driven chrome interpolation
// out of the way while a download event is awaited).
test.use({ reducedMotion: "reduce" });

const FILENAME = (view: string, ext: string) =>
  new RegExp(`^tapestry-dev-${view}-\\d{4}-\\d{2}-\\d{2}\\.${ext}$`);

test("Explorer exports a dated PNG and SVG", async ({ page }) => {
  await page.goto("file://" + OUT);
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();
  await page.getByRole("button", { name: "Views" }).click();
  await expect(page.locator("#views-panel")).toBeVisible();

  const [png] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "PNG", exact: true }).click(),
  ]);
  expect(png.suggestedFilename()).toMatch(FILENAME("explorer", "png"));

  const [svg] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "SVG", exact: true }).click(),
  ]);
  expect(svg.suggestedFilename()).toMatch(FILENAME("explorer", "svg"));
});

test("Systems exports a dated PNG and SVG", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Systems" }).click();
  const panel = page.locator("#panel-systems");
  await expect(panel.locator("canvas").first()).toBeVisible();

  const [png] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("button", { name: "PNG", exact: true }).click(),
  ]);
  expect(png.suggestedFilename()).toMatch(FILENAME("systems", "png"));

  const [svg] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("button", { name: "SVG", exact: true }).click(),
  ]);
  expect(svg.suggestedFilename()).toMatch(FILENAME("systems", "svg"));
});

test("Chronicle exports a dated PNG and SVG", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Chronicle" }).click();
  const panel = page.locator("#panel-chronicle");
  await expect(panel.locator("canvas").first()).toBeVisible();

  const [png] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("button", { name: "PNG", exact: true }).click(),
  ]);
  expect(png.suggestedFilename()).toMatch(FILENAME("chronicle", "png"));

  const [svg] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("button", { name: "SVG", exact: true }).click(),
  ]);
  expect(svg.suggestedFilename()).toMatch(FILENAME("chronicle", "svg"));
});

test("Semantic exports a dated PNG and SVG", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Semantic" }).click();
  const panel = page.locator("#panel-semantic");
  await expect(panel.locator(".semantic__canvas canvas").first()).toBeVisible();

  const [png] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("button", { name: "PNG", exact: true }).click(),
  ]);
  expect(png.suggestedFilename()).toMatch(FILENAME("semantic", "png"));

  const [svg] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("button", { name: "SVG", exact: true }).click(),
  ]);
  expect(svg.suggestedFilename()).toMatch(FILENAME("semantic", "svg"));
});

test("Overview exposes a wired Print button (window.print is not invoked in CI)", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Overview" }).click();
  const panel = page.locator("#panel-overview");
  await expect(panel).toBeVisible();

  const printButton = panel.getByRole("button", { name: "Print" });
  await expect(printButton).toBeVisible();
  await expect(printButton).toHaveAttribute("title", /print|pdf/i);
});
