import { expect, test, type Locator } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * End-to-end smoke coverage for the built, self-contained Tapestry page —
 * the same artifact `loom visualize` writes. Rather than running against the
 * dev server, this injects the committed dev fixture into the committed
 * template (`theloom/viz/static/tapestry.html`) using the exact substitution
 * `theloom/viz/html.py`'s `render_html` performs, then opens the result via
 * `file://`. That keeps the test honest about what ships, not just what
 * `npm run dev` serves.
 */
const OUT = join(tmpdir(), "tapestry-e2e.html");

test.beforeAll(() => {
  const template = readFileSync(new URL("../../theloom/viz/static/tapestry.html", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../fixtures/dev-bundle.json", import.meta.url), "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(OUT, html);
});

/**
 * Playwright's `.fill()` throws on `<input type="range">` — and a plain
 * `input.value = ...` doesn't register with React either: React wraps the
 * DOM node's `value` property with its own tracker so a direct JS write is
 * invisible to the controlled-input diffing that decides whether to fire
 * `onChange`. Going through the native `HTMLInputElement.prototype` setter
 * (bypassing React's wrapper) then dispatching "input" is the standard
 * workaround, and the one that actually reaches the Scrubber's `onChange`.
 */
async function setSlider(slider: Locator, value: number): Promise<void> {
  await slider.evaluate((el, v) => {
    const input = el as HTMLInputElement;
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    nativeSetter.call(input, String(v));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, value);
}

test("explorer renders nodes and the overview shows dashboard sections", async ({ page }) => {
  await page.goto("file://" + OUT);

  // Sigma mounts its WebGL canvases inside .explorer__canvas once the bundle
  // is parsed and the graph built — the core "did the app boot" check.
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();

  await page.getByRole("tab", { name: "Overview" }).click();

  const panel = page.locator("#panel-overview");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Composition" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Graph health" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Confidence" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Most central" })).toBeVisible();
  await expect(panel.getByText("Entities", { exact: true })).toBeVisible();
});

test("pressing p enters path mode and shows the path hint bar", async ({ page }) => {
  await page.goto("file://" + OUT);

  // Path mode is off by default: no status region yet.
  await expect(page.getByRole("status")).toHaveCount(0);

  await page.keyboard.press("p");

  // Explorer.tsx's PathBar renders with role="status" only while pathMode is
  // on, prompting for the first click — the real, literal hint text.
  const pathbar = page.getByRole("status");
  await expect(pathbar).toBeVisible();
  await expect(pathbar).toContainText("Path mode — click a node to start.");
});

test("systems tab shows the causal loop, isolates it, and animates flow with a leverage badge", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Systems" }).click();

  const panel = page.locator("#panel-systems");
  await expect(panel).toBeVisible();
  await expect(panel.locator("canvas").first()).toBeVisible(); // sigma mounted

  // Let the causal-layout force run settle so the polarity glyph / leverage
  // badge overlays (positioned from Sigma's afterRender) sit on real
  // coordinates before we assert against them.
  await page.waitForTimeout(2700);

  // The enriched fixture ships exactly one feedback loop: a 3-variable
  // balancing loop over the causal subgraph's three edges.
  const loopRow = panel.getByRole("button", { name: /balancing/i });
  await expect(loopRow).toBeVisible();
  await expect(loopRow).toContainText("3 variables");

  // Every causal edge (causes/causes/inhibits) carries a +/− polarity glyph —
  // colour is never the only cue.
  await expect(panel.locator(".systems__glyph")).toHaveCount(3);

  // The fixture's one leverage point ("Feedback transparency", Meadows level
  // 6) marks the variable it targets.
  const leverageBadge = panel.locator(".systems__leverage");
  await expect(leverageBadge).toHaveCount(1);
  await expect(leverageBadge).toHaveText("6");

  // Flow animation is locked until a loop is isolated.
  const flowToggle = panel.locator(".systems__flow");
  await expect(flowToggle).toHaveText(/animate flow/i);
  await expect(flowToggle).toBeDisabled();

  // Selecting the loop isolates it and unlocks the flow toggle.
  await loopRow.click();
  await expect(loopRow).toHaveAttribute("aria-pressed", "true");
  await expect(flowToggle).toBeEnabled();

  await flowToggle.click();
  await expect(flowToggle).toHaveAttribute("aria-pressed", "true");
  await expect(flowToggle).toHaveText(/stop flow/i);

  // Clearing isolation drops the selection and relocks the flow toggle.
  await panel.getByRole("button", { name: "Clear isolation" }).click();
  await expect(loopRow).toHaveAttribute("aria-pressed", "false");
  await expect(flowToggle).toBeDisabled();
});

test("chronicle tab scrubs the graph's construction and flags the deprecated claim at the end", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Chronicle" }).click();

  const panel = page.locator("#panel-chronicle");
  await expect(panel).toBeVisible();
  await expect(panel.locator("canvas").first()).toBeVisible(); // sigma mounted

  const slider = panel.getByRole("slider"); // the time scrubber
  await expect(slider).toBeVisible();

  // Let FA2 settle so the status-badge overlay (positioned from afterRender)
  // sits on real coordinates.
  await page.waitForTimeout(2200);

  const min = Number(await slider.getAttribute("min"));
  const max = Number(await slider.getAttribute("max"));
  expect(max).toBeGreaterThan(min);

  // Default scrubber position (time: null) parks at the end — current state.
  await expect(slider).toHaveValue(String(max));

  // The fixture's one status-change event (entity_status_changed, mid-timeline)
  // deprecates a claim and nothing later reverts it, so at the default
  // parked-at-end instant (current state) its status badge is already showing.
  const deprecatedBadge = panel.locator('.chronicle__badge[data-status="deprecated"]');
  await expect(deprecatedBadge).toBeVisible();
  await expect(panel.locator(".events__row")).toHaveCount(35);

  // Scrub to the very start: nothing has happened yet, so the badge hides and
  // the first event row becomes "current".
  await setSlider(slider, min);
  await expect(deprecatedBadge).toBeHidden();
  await expect(panel.locator(".events__row--current")).toHaveCount(1);

  // Scrubbing back to the end restores the deprecated badge.
  await setSlider(slider, max);
  await expect(deprecatedBadge).toBeVisible();
});

test("chronicle play button advances the scrubber and can be paused", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Chronicle" }).click();

  const panel = page.locator("#panel-chronicle");
  const slider = panel.getByRole("slider");
  await expect(slider).toBeVisible();
  await page.waitForTimeout(2200);

  const min = Number(await slider.getAttribute("min"));
  await setSlider(slider, min); // park at the start so "play" has somewhere to go
  await expect(slider).toHaveValue(String(min));

  const playButton = panel.getByRole("button", { name: /play replay|pause replay/i });
  await expect(playButton).toHaveAttribute("aria-pressed", "false");

  await playButton.click();
  await expect(playButton).toHaveAttribute("aria-pressed", "true");
  await expect(playButton).toHaveAccessibleName("Pause replay");

  // Give the rAF playback loop a moment to advance the scrubber, then pause.
  await page.waitForTimeout(600);
  const midValue = Number(await slider.inputValue());
  expect(midValue).toBeGreaterThan(min);

  await playButton.click();
  await expect(playButton).toHaveAttribute("aria-pressed", "false");
  await expect(playButton).toHaveAccessibleName("Play replay");
});

test("chronicle diff mode classifies added, changed, and invalidated nodes across a window", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Chronicle" }).click();

  const panel = page.locator("#panel-chronicle");
  const slider = panel.getByRole("slider");
  await expect(slider).toBeVisible();
  await page.waitForTimeout(2200);

  const min = Number(await slider.getAttribute("min"));
  const max = Number(await slider.getAttribute("max"));

  // Anchor A ~43s after the first event: past the six original entity
  // creations, so the (A, end] window catches the leverage point plus the
  // enriched fixture's gradient-descent trio (added — three earlier attempts
  // were created and later deleted in the same window, so they're in the raw
  // diff but wear no on-canvas badge since they no longer exist), the
  // mid-timeline batch update (changed), and the deprecation (invalidated) —
  // all three diff categories in one window.
  const anchor = min + 43000;
  await setSlider(slider, anchor);

  const diffToggle = panel.locator(".scrubber__diff");
  await diffToggle.click();
  await expect(diffToggle).toHaveAttribute("aria-pressed", "true");

  await setSlider(slider, max); // B: the end of the timeline

  const diffBar = panel.locator(".chronicle__diffbar");
  await expect(diffBar).toBeVisible();

  // Seven entities are created in the window, but three of them (an earlier,
  // superseded wording of the gradient-descent trio) are later deleted in the
  // same window, so only four still exist to wear an on-canvas added badge.
  await expect(panel.locator(".chronicle__diffbadge--added")).toHaveCount(4);
  // One entity is deprecated in the window (a node both updated and later
  // deprecated wears only the invalidated badge — never double-counted).
  await expect(panel.locator(".chronicle__diffbadge--invalidated")).toHaveCount(1);
  // Six entities are updated in the window; five wear the changed badge (the
  // sixth is the one that also gets deprecated). The gradient-descent trio's
  // own updates land in "added", not "changed" — diffStates excludes ids
  // created in the same window from the changed set.
  await expect(panel.locator(".chronicle__diffbadge--changed")).toHaveCount(5);

  // The summary chips count the raw diff sets (not filtered to nodes that
  // still exist), so "added" reads seven even though only four are badged.
  await expect(panel.locator(".chronicle__diffchip--added")).toContainText("7 added");
  await expect(panel.locator(".chronicle__diffchip--changed")).toContainText("5 changed");
  await expect(panel.locator(".chronicle__diffchip--invalidated")).toContainText("1 invalidated");

  // Turning diff off restores the plain time-replay legend.
  await diffToggle.click();
  await expect(diffToggle).toHaveAttribute("aria-pressed", "false");
  await expect(diffBar).toBeHidden();
});

test("semantic tab scatters embeddings, toggles hulls, and lassoes into the explorer", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Semantic" }).click();

  const panel = page.locator("#panel-semantic");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".semantic__canvas canvas").first()).toBeVisible(); // sigma
  // mounted — the projection is the layout, so no FA2 wait is needed.

  // The enriched fixture ships one 3-member "concept" cluster, so exactly one
  // labeled hull renders; the toggle removes it from the DOM entirely (it is
  // not merely hidden) and restores it on the next render.
  const hullToggle = panel.getByRole("button", { name: /hulls/i });
  const hull = panel.locator(".semantic__hull").first();
  await expect(hull).toBeVisible();
  await expect(panel.locator(".semantic__hulllabel")).toHaveText("concept");
  await hullToggle.click();
  await expect(panel.locator(".semantic__hull")).toHaveCount(0);
  await hullToggle.click();
  await expect(panel.locator(".semantic__hull")).toHaveCount(1);

  // Lasso a loop around just the cluster's hull (plus a small margin so its
  // vertices sit inside the loop, not on its edge) → brushes exactly its 3
  // members, not the other 6 projected points.
  await panel.getByRole("button", { name: /lasso/i }).click();
  const hullBox = (await hull.boundingBox())!;
  const margin = 26;
  const hx0 = hullBox.x - margin;
  const hy0 = hullBox.y - margin;
  const hx1 = hullBox.x + hullBox.width + margin;
  const hy1 = hullBox.y + hullBox.height + margin;
  await page.mouse.move(hx0, hy0);
  await page.mouse.down();
  await page.mouse.move(hx1, hy0);
  await page.mouse.move(hx1, hy1);
  await page.mouse.move(hx0, hy1);
  await page.mouse.move(hx0, hy0);
  await page.mouse.up();
  await expect(panel.locator(".semantic__brushcount")).toHaveText("3 brushed");

  // Clearing the brush removes the chip entirely.
  await panel.getByRole("button", { name: "Clear brush" }).click();
  await expect(panel.locator(".semantic__brush")).toHaveCount(0);

  // Lassoing the whole canvas brushes every projected point (9 in the fixture
  // — one of the 10 entities carries no embedding, so it has no projection).
  const box = (await panel.locator(".semantic__canvas").boundingBox())!;
  await page.mouse.move(box.x + 5, box.y + 5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 5, box.y + 5);
  await page.mouse.move(box.x + box.width - 5, box.y + box.height - 5);
  await page.mouse.move(box.x + 5, box.y + box.height - 5);
  await page.mouse.move(box.x + 5, box.y + 5);
  await page.mouse.up();
  await expect(panel.locator(".semantic__brushcount")).toHaveText("9 brushed");

  // "View in Explorer" carries the brush across tabs as a count chip.
  await panel.getByRole("button", { name: /view in explorer/i }).click();
  const explorerPanel = page.locator("#panel-explorer");
  await expect(explorerPanel).toBeVisible();
  await expect(explorerPanel.locator(".explorer__brushcount")).toHaveText("9 brushed");

  // Clearing from the Explorer side removes its chip too.
  await explorerPanel.getByRole("button", { name: "Clear brush" }).click();
  await expect(explorerPanel.locator(".explorer__brush")).toHaveCount(0);
});
