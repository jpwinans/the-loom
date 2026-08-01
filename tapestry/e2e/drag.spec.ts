import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * End-to-end coverage for click-hold-drag node repositioning, against the built,
 * self-contained page (mirroring smoke.spec.ts's fixture injection). Two things
 * are proven without ever pixel-hunting for a node: (1) a node the search box
 * centres actually MOVES when dragged — clicking its old spot no longer finds it
 * and clicking the drop spot does — and (2) the click Sigma emits after a real
 * drag is SUPPRESSED, so a drag in path mode never picks a path endpoint.
 */
const OUT = join(tmpdir(), "tapestry-e2e-drag.html");

// Reduced motion keeps the run deterministic (no open-ended CSS animations);
// the Sigma camera fly and the FA2 settling burst are JS, unaffected by it.
test.use({ reducedMotion: "reduce" });

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

/** The Explorer's FA2 burst runs for LAYOUT_MS (3s) then fits the camera; wait
 * past both so a searched node, once centred, stays put under the cursor. */
const SETTLE_MS = 3700;
/** Slack for the ~500ms camera fly the search's `navigate()` kicks off. */
const FLY_MS = 800;
/** Sigma treats two canvas clicks within its doubleClickTimeout (300ms) as a
 * double-click (a zoom, no `click`); keep single clicks comfortably apart. */
const CLICK_GAP_MS = 500;

/** Search for a fixture node by label and select it — camera flies to centre it. */
async function selectNode(panel: Locator, name: string): Promise<void> {
  const input = panel.locator("#explorer-search-input");
  await input.click();
  await input.fill(name);
  await panel.locator(".search__result", { hasText: name }).first().click();
}

/** Centre of the Explorer canvas in page coordinates. */
async function canvasCentre(panel: Locator): Promise<{ x: number; y: number }> {
  const box = (await panel.locator(".explorer__canvas").boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Press on (from), travel to (to) in steps, and release — a real drag. */
async function drag(
  page: Page,
  from: { x: number; y: number },
  to: { x: number; y: number },
): Promise<void> {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 12 });
  await page.mouse.up();
}

test("dragging a searched node moves it: its old spot is emptied, the drop spot holds it", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  const panel = page.locator("#panel-explorer");
  await expect(panel.locator(".explorer__canvas canvas").first()).toBeVisible();
  await page.waitForTimeout(SETTLE_MS);

  const detailFor = (name: string): Locator => panel.locator(`aside[aria-label="Details for ${name}"]`);

  // Search-select "Population": the camera centres it, so the canvas centre now
  // sits over the node — no pixel-hunting.
  await selectNode(panel, "Population");
  await page.waitForTimeout(FLY_MS);
  await expect(detailFor("Population")).toBeVisible();

  const centre = await canvasCentre(panel);
  const dropped = { x: centre.x - 150, y: centre.y }; // left of centre — clear of the right-side detail panel

  await drag(page, centre, dropped);

  // The drop's trailing click is swallowed, so the node stays selected across the
  // drag (never deselected, never errored).
  await expect(detailFor("Population")).toBeVisible();

  // Proof of movement #1: the node's ORIGINAL centre no longer holds it — a fresh
  // click there hits empty stage, so selection clears and the panel disappears.
  await page.waitForTimeout(CLICK_GAP_MS);
  await page.mouse.click(centre.x, centre.y);
  await expect(detailFor("Population")).toHaveCount(0);

  // Proof of movement #2: the DROP point now holds it — clicking there re-selects
  // Population, which is only possible if the node actually moved there.
  await page.waitForTimeout(CLICK_GAP_MS);
  await page.mouse.click(dropped.x, dropped.y);
  await expect(detailFor("Population")).toBeVisible();
});

test("a drag in path mode does not pick an endpoint (the post-drag click is suppressed)", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  const panel = page.locator("#panel-explorer");
  await expect(panel.locator(".explorer__canvas canvas").first()).toBeVisible();
  await page.waitForTimeout(SETTLE_MS);

  // Enter path mode: the hint bar prompts for the first endpoint.
  await page.keyboard.press("p");
  const hint = panel.locator(".pathbar__hint");
  await expect(hint).toContainText("Path mode — click a node to start.");

  // Centre a node; selecting it does not pick a path endpoint (selection and path
  // endpoints are independent), so the prompt is unchanged.
  await selectNode(panel, "Population");
  await page.waitForTimeout(FLY_MS);
  await expect(hint).toContainText("Path mode — click a node to start.");

  const centre = await canvasCentre(panel);
  const dropped = { x: centre.x - 150, y: centre.y };

  // Dragging the node must NOT count as clicking it: were the post-drag click not
  // suppressed, it would set Population as the first endpoint.
  await drag(page, centre, dropped);
  await expect(hint).toContainText("Path mode — click a node to start.");

  // A genuine (non-drag) click still registers — and it lands on the node at its
  // NEW position, so the endpoint prompt advances, proving both the suppression
  // above and that the node moved to the drop point.
  await page.waitForTimeout(CLICK_GAP_MS);
  await page.mouse.click(dropped.x, dropped.y);
  await expect(hint).toContainText("From");
  await expect(hint).toContainText("Population");
});
