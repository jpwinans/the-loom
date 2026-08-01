# Tapestry Phase 5 Implementation Plan — Polish (final phase)

> **For agentic workers:** Execute this plan task-by-task, in order. Each task is
> a self-contained unit with a failing test → verify-fail → implement →
> verify-pass → gates → commit cycle. Do not start a task until the previous
> one's gates are green and committed. Steps use checkbox (`- [ ]`) syntax for
> tracking. This plan is written for a generic executing agent — it depends on no
> "superpowers" skills and no external orchestration; every command it needs is
> spelled out inline. UI tasks call for the `dataviz` and `frontend-design`
> skills where noted; if those skills are unavailable, apply their principles by
> hand (job-based colour, one-system consistency, WCAG-AA contrast, deliberate
> typography, both-theme styling) — do not block on them.

**This is the final phase.** Phase 1's roadmap defined the five phases; Phase 5
is the last one, and the Tapestry roadmap **closes with this document**. There is
no Phase 6. This plan finishes the four remaining polish areas from the spec's
`## Phasing` line 5 and `## Cross-cutting` section — **saved-view management UI**,
**full accessibility/keyboard pass**, **export refinements**, and **50k-node
performance hardening** — plus a **final docs sweep** and a recorded scale
benchmark. When it lands, every item in
`docs/superpowers/specs/2026-07-11-loom-visualization-design.md` that phases 1–4
did not ship is either shipped or explicitly, defensibly deferred (see
**Plan self-review notes → Spec sweep**).

**Goal:** Turn the feature-complete workbench into a finished product. Ship
saved-view *management* (rename, export/import as JSON, apply-on-load via URL);
pass a real accessibility bar (composite-widget keyboard patterns, visible focus,
`aria-live` for async updates, a `?` help overlay, keyboard alternatives for
canvas-only actions) verified by an `@axe-core/playwright` audit with **zero
serious/critical violations** across all five tabs in both themes; refine exports
so **every** view (not just the Explorer) emits a WYSIWYG PNG/SVG with a legend,
theme-honoring background, and a `<graph>-<view>-<date>` filename, and the DOM
Overview prints cleanly; and harden the whole stack for **50k nodes / 100k edges**
— Python assembly guardrails (an explicit `maxEntities` cap with `truncated`
metadata instead of silent slowness; analytics limits so the O(V·E) betweenness
and the exponential loop enumeration cannot dominate a huge graph) and frontend
rendering (ForceAtlas2 Barnes-Hut at scale, sigma label level-of-detail,
hover-during-layout suppression, and a virtualized Chronicle event list that
survives 100k rows). Per the approved spec's `## Cross-cutting` (Design system,
Exports, Shareable state, Performance) and the Phase 1 roadmap's Phase 5 line.

**Architecture:** Phase 5 adds **no store, no command, no runtime dependency,
and no new architectural surface** — it deepens what phases 1–4 shipped. The one
data-contract change is **additive and optional**: `TapestryMeta` gains an
optional `truncated` object (populated only when the `maxEntities` cap actually
fires), so `SCHEMA_VERSION` stays `1` (an additive optional field keeps every
existing bundle valid) and the committed `tapestry/schema/bundle.schema.json` is
regenerated from the model, never hand-edited. `maxEntities` is a new optional
field on the *input* model `ExportBundleInput` (not on the wire bundle), so it
touches `COMMANDS.md` but not the bundle schema. Every analytics guardrail is
gated behind a node/edge **threshold high enough that the `tapestry-dev` fixture
(10 entities) and all existing tests behave byte-for-byte as before** — the
guardrails are dead code until a graph is genuinely large, which is exactly how
the existing tests stay green. On the frontend, the saved-view management,
accessibility, export, and rendering changes are all **layered onto existing
modules** behind the same data-source-agnostic view contracts; the static
`file://` single-file path, the dev-fixture path, and live mode all stay intact.
Only one new dev dependency is added (`@axe-core/playwright`, a test-only npm
devDependency); no runtime bundle dependency, so the committed
`theloom/viz/static/tapestry.html` gains only the polish code, and the
exactly-once `__TAPESTRY_BUNDLE__` sentinel rule is preserved.

**Tech Stack:** Python 3.11+/Pydantic v2/Typer · FastAPI + uvicorn (optional
`viz-serve` extra, unchanged) · React 18 · TypeScript · zustand · sigma.js v3 ·
graphology · graphology-layout-forceatlas2 · Vitest · Playwright ·
**`@axe-core/playwright` (new, test-only npm devDependency)**. **No new runtime
dependency, Python or JavaScript.**

## Prerequisites (fresh environment)

- `uv sync` (add `--extra viz-serve` only if you touch or run live mode);
  `docker compose up -d falkordb` (Python tests connect to the live store —
  nothing is mocked; `uv run loom init` if the default graph is new).
- Node.js 22+ and npm for the `tapestry/` workspace; `cd tapestry && npm ci`.
  Playwright chromium: `cd tapestry && npx playwright install chromium`.
- Phases 1–4 are fully implemented and committed on `main`: the `theloom/viz`
  subpackage; `visualize` / `export-bundle` / `serve`; the SPA with Explorer,
  Overview, Systems, Chronicle, Semantic Map; saved views (minimal panel), deep
  links, PNG/SVG export (Explorer only), and live mode. Read the approved spec
  and `docs/superpowers/plans/2026-07-11-tapestry-phase-1.md` through
  `…-phase-4.md` before starting.
- The live store already holds a `tapestry-dev` fixture graph;
  `tapestry/fixtures/dev-bundle.json` is exported from it. Phase 5 does **not**
  re-export the fixture and does **not** change the bundle for the fixture (the
  `truncated` field is absent unless the cap fires, which it never does at
  fixture scale).
- Task 6's benchmark generator and Task 8's benchmark run need a large synthetic
  graph the generator builds on demand (`tapestry-bench`) — it is **built by a
  script, never committed** (a 50k/100k bundle is tens of MB). Nothing in CI
  builds or asserts against it; the numbers are recorded in the plan, not gated.

## Global Constraints

These are load-bearing — every one was learned the hard way in Phases 1–4. The
Phase 3/4 constraints are carried forward verbatim where still binding; Phase 5
additions follow.

### Carried forward (still binding)

- **Gates every commit.** `uv run mypy --strict theloom && uv run ruff check . &&
  uv run ruff format . && uv run pytest` must pass, plus — whenever `tapestry/`
  is touched — `cd tapestry && npm test && npm run build` and
  `uv run pytest tests/test_cli_viz_commands.py`. Keep `main` green. **No
  wall-clock performance assertion gates CI** (per CLAUDE.md) — Task 8 records
  measured numbers in this plan instead.
- **No pydantic mypy plugin.** Aliased Pydantic fields must be constructed with
  **alias (camelCase) kwargs** or via `model_validate({...camelCase...})`;
  snake_case kwargs on an aliased field fail `uv run mypy --strict theloom`.
  Mirror the existing `bundle.py` / `schema.py` pattern exactly
  (`generatedAt=…`, `entityCount=…`, `leveragePoints=…`, `asOf=…`); non-aliased
  fields use their plain name. Wire names are camelCase; serialize with
  `model_dump(by_alias=True, exclude_none=True)`.
- **The viz test suite is model-free by design.** No viz test downloads the
  fastembed model — vectors are seeded through `store.set_entity_vector(id, vec)`,
  and any test that reaches `_search_similar` / `find_clusters` /
  `semantic_search` MUST monkeypatch `theloom.operations.semantic.get_embedder`
  with a deterministic stub. `get_embedder` is `@lru_cache`d, so patch the
  **module attribute** `theloom.operations.semantic.get_embedder`, not the cache.
  Phase 5's benchmark generator (Task 6) seeds **no vectors** for the same reason.
- **`EntityCreate.model_validate(...)` requires `"observations": []`** in test
  fixtures and seeds. **`create-relation` requires an explicit `"polarity": null`**
  for a non-causal relation type; causal relations use polarity `"+"` / `"-"`
  (verify the accepted literals against `theloom/model.py` before seeding — the
  frontend reads `polarity === "-"`).
- **Never introduce a literal `__TAPESTRY_BUNDLE__` string in tapestry app
  source.** esbuild constant-folds it; the sentinel must appear exactly once in
  built output. `tapestry/src/lib/data.ts` detects mode by a `JSON.parse` result
  (dev-mode = parse failure; live-mode = a parsed marker with `live === true`),
  never by comparing against the literal — keep it that way. After every
  frontend-touching task, `cd tapestry && npm run build` and confirm
  `grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html` → `1`.
- **`tsc -b` strict project references.** Test-fixture bundles are typed
  `as unknown as TapestryBundleRaw` (never `as never`).
- **vitest 3.x + happy-dom (no WebGL).** Never instantiate Sigma in unit tests —
  test **pure modules only**. E2E via Playwright chromium. `vite.config.ts`'s
  vitest `exclude` already covers `e2e/**` AND `e2e-live/**`; keep any new spec
  files under those dirs so vitest never picks them up.
- **Canvas-side colours resolve CSS vars at runtime and MUST re-resolve on theme
  change** (the rAF + `readVar` pattern each Sigma view already uses). Any new
  canvas colour (export background, label LOD) reads a token with a light-theme
  fallback constant and both-theme values in `tokens.css`.
- **DOM overlays pair `sig.framedGraphToViewport(sig.getNodeDisplayData(id))`,
  never `graphToViewport`** (the Systems glyphs, Chronicle badges, and Semantic
  hulls all do this; any export that reads overlay positions must too).
- **Playwright `.fill()` throws on `<input type="range">`, and React swallows a
  plain `input.value = …`.** Use the `setSlider` helper already in
  `e2e/smoke.spec.ts` (native prototype setter + `input` event) for any range
  control. Drive a `<select>` with `selectOption` by **exact value** (there are
  64 local graphs; a label match is ambiguous).
- **macOS case-insensitive FS: a component file must not differ from a module
  file only by case** (the `SystemsView.tsx`/`systems.ts`,
  `SemanticView.tsx`/`semanticMap.ts` precedents). New files in this plan
  (`HelpOverlay.tsx`, `lib/roving.ts`, `views/chronicle/eventWindow.ts`) must not
  collide by case with an existing file.
- **One commit per task**, including the rebuilt `theloom/viz/static/tapestry.html`
  whenever the frontend changed. Commit messages are plain imperative — **never
  mention AI/Claude, never add co-author trailers.**
- **Python tests hit live FalkorDB** via the `db` / `redis_client` / `namespace`
  fixtures in `tests/conftest.py`; CLI-level tests go through
  `run_handler(name, input, multi)`. Your local default graph may be large and
  hold real data — **never enumerate or assemble it** in tests, seeds, or
  manual runs; always pass `{"graph": "tapestry-dev"}` (or `tapestry-bench` for
  the Task 6/8 benchmark). Live mode's serve.py must keep using
  `app.add_api_route` / `app.add_exception_handler` (never decorators) and
  `output_success` must `sys.stdout.flush()` before the blocking `run_uvicorn`;
  Phase 5 does not touch serve.py, but if a docs example changes it, preserve
  those. Run `mypy --strict theloom` in BOTH extra states if you touch serve.py.
- **Commit each task with an explicit pathspec.** A concurrent session may share
  this working tree and hold uncommitted Python WIP in files including
  `docker-compose.yml`, `tests/conftest.py`, `theloom/store/falkor.py`,
  `theloom/operations/bulk.py`, and others. **Never `git add -A`, never
  `git add` a directory blindly, and never stage files a task did not change.**
  Commit with an explicit list: `git add <exact paths> && git commit -m "…" --
  <exact paths>`.

### New in Phase 5

- **The bundle schema changes for the first time — additively.** `TapestryMeta`
  gains **one optional field**, `truncated` (a `Truncated` submodel or `None`).
  Because it is optional and additive, **`SCHEMA_VERSION` stays `1`**: every
  existing bundle (no `truncated`) still validates, and the SPA reads the field
  only when present. After adding it, regenerate the committed schema with
  `uv run python -m theloom.viz.schema` (writes
  `tapestry/schema/bundle.schema.json`), mirror the field into
  `tapestry/src/lib/data.ts`'s `TapestryBundleRaw['meta']`, and confirm
  `tests/test_viz_schema_drift.py` (exact equality of committed vs. model schema)
  and `tapestry/src/lib/schema.test.ts` (ajv-validates the dev fixture) both stay
  green. **Do not bump `SCHEMA_VERSION`** — an additive optional field does not
  warrant a major version, and bumping it would force the loader to gate on
  version for no gain.
- **`maxEntities` is an input field, not a wire field.** It lives on
  `ExportBundleInput` (`theloom/viz/bundle.py`), so it changes the generated
  `COMMANDS.md` (regenerate with `uv run loom --generate-docs > COMMANDS.md`)
  but **not** `bundle.schema.json`. The command count is unchanged (Phase 5 adds
  no command), so `CLAUDE.md`'s "153 commands" line stays as-is — **verify** it
  reads 153 (Phase 4 bumped it) and leave it.
- **Guardrails must be no-ops at fixture scale.** Every analytics/assembly
  guardrail is gated behind a module-level threshold constant chosen so the
  10-entity `tapestry-dev` fixture is unaffected and every existing Python test
  stays green **without editing the test**. Make the thresholds **injectable**
  (module constants that tests can monkeypatch, or explicit params) so a guardrail
  can be exercised against a *small* graph with a *low* threshold — never build
  a 50k graph inside a `pytest` run.
- **The benchmark generator and benchmark run are local-only.** The synthetic
  50k/100k graph (`tapestry-bench`) is produced by `scripts/gen_bench_graph.py`
  and is **never committed** and **never built in CI**. Task 8 runs the benchmark
  on the dev machine and records the numbers in this plan's final task — **no
  wall-clock assertion enters CI or the test suite** (CLAUDE.md).
- **Accessibility is verified, not asserted by eye.** Task 4 adds an
  `@axe-core/playwright` scan over all five tabs **in both light and dark themes**
  and fails on any `serious`/`critical` violation. It runs inside the existing
  `tapestry` CI job (which already runs `npm run e2e`); `@axe-core/playwright` is
  a **devDependency**, so `npm ci` installs it — no new CI job, no FalkorDB, no
  Python. Regenerate `tapestry/package-lock.json` with `npm install
  --save-dev @axe-core/playwright` and commit it alongside `package.json`.
- **The `?` help overlay is a real modal.** It is `role="dialog"
  aria-modal="true"`, opens on the `?` key (which `useKeyboard` already passes —
  it is not a modifier and `event.key === "?"`), **traps focus** (Tab cycles
  within; `Escape` closes and restores focus to the trigger), and is reachable by
  mouse via a header `?` button carrying `aria-keyshortcuts="?"`. Because
  `useKeyboard` ignores keydowns whose target is a form control, the overlay's own
  close button and any controls inside it still work (they are `<button>`s, and
  `Escape` on them is handled locally).
- **Canvas actions need a keyboard path only where one is sensible.** The
  Explorer already has `/`, `p`, `f`, `Esc`, and arrow-walk. The Systems view's
  core actions (isolate a loop, animate flow, pause layout) are already `<button>`s
  and `<LoopPanel>` rows — keyboard-operable; **do not rebuild them**. The
  Chronicle's scrubber is a range input and its event rows/play/diff are buttons —
  keyboard-operable. **Only the Semantic Map's lasso is pointer-only**; Task 3
  adds a keyboard alternative there (a cluster-brush affordance), not a synthetic
  pointer emulation.
- **Exports must not regress the Explorer's WYSIWYG contract.** The Explorer's
  PNG (layered-canvas flatten) and SVG (`graphToSvg`, theme background via
  `--color-canvas`) already work; Task 5 *extends* the shared `exportSvg.ts`
  (adds a theme-background fill to PNG, an optional legend to `graphToSvg`, a
  `<graph>-<view>-<date>` filename helper) and wires the other Sigma views to the
  same functions. Known and accepted limitation: DOM-overlay decorations
  (Systems polarity glyphs/leverage badges, Chronicle status/diff badges, Semantic
  hull outlines) are **not** part of the flattened PNG (they are HTML/SVG overlays,
  not sigma canvases); the SVG legend documents polarity/status so the exported
  image is still legible. State this in the export UI's title text, do not silently
  drop it.

## File Structure (Phase 5 touch map)

```
# Task 1 — saved-view management
tapestry/src/lib/savedViews.ts        + renameView / serializeViews / importViews / resolveViewHash
tapestry/src/lib/savedViews.test.ts   + rename/export/import/resolve cases
tapestry/src/views/explorer/Explorer.tsx   ViewsMenu: rename, export, import controls
tapestry/src/views/explorer/Explorer.css   styles for the new controls
tapestry/src/App.tsx                  apply-on-load: `#view=<name>` resolves a saved view
tapestry/e2e/savedviews.spec.ts       save → rename → export → import round-trip
theloom/viz/static/tapestry.html      rebuilt

# Task 2 — keyboard operability + ARIA patterns + focus + aria-live
tapestry/src/lib/roving.ts            + roving-focus keydown helper (pure)
tapestry/src/lib/roving.test.ts
tapestry/src/App.tsx                  tablist + theme radiogroup roving/arrows; polite live region
tapestry/src/App.css                  focus-visible rings; live-region visually-hidden
tapestry/src/design/tokens.css        + --focus-ring token (both themes)
tapestry/e2e/keyboard.spec.ts         arrow-key tab/theme navigation, focus visibility
theloom/viz/static/tapestry.html      rebuilt

# Task 3 — help overlay + canvas keyboard alternatives
tapestry/src/views/HelpOverlay.tsx    the `?` dialog (focus-trapped)
tapestry/src/views/HelpOverlay.css
tapestry/src/App.tsx                  `?` trigger button + overlay wiring
tapestry/src/views/semantic/SemanticView.tsx   keyboard cluster-brush affordance
tapestry/src/views/semantic/SemanticMap.css
tapestry/e2e/help.spec.ts             open/trap/close; keyboard cluster-brush
theloom/viz/static/tapestry.html      rebuilt

# Task 4 — axe-core accessibility audit
tapestry/package.json                 + @axe-core/playwright devDependency
tapestry/package-lock.json            regenerated
tapestry/e2e/a11y.spec.ts             axe scan of all five tabs, light + dark
(plus any markup/CSS fixes the scan surfaces, in the offending files)
theloom/viz/static/tapestry.html      rebuilt (only if a fix changed source)

# Task 5 — export refinements
tapestry/src/lib/exportSvg.ts         PNG bg fill; graphToSvg legend; exportFilename helper
tapestry/src/lib/exportSvg.test.ts    filename + legend cases
tapestry/src/views/systems/SystemsView.tsx     export control (SVG/PNG)
tapestry/src/views/chronicle/Chronicle.tsx      export control (SVG/PNG)
tapestry/src/views/semantic/SemanticView.tsx    export control (SVG/PNG)
tapestry/src/views/*/*.css            export button styles
tapestry/src/views/overview/Overview.tsx        print button + @media print block
tapestry/src/views/overview/Overview.css        print stylesheet
tapestry/e2e/export.spec.ts           each view triggers a download
theloom/viz/static/tapestry.html      rebuilt

# Task 6 — Python assembly guardrails + benchmark generator
scripts/gen_bench_graph.py            synthetic 50k/100k graph builder (uncommitted output)
theloom/viz/schema.py                 + Truncated submodel; TapestryMeta.truncated
theloom/viz/bundle.py                 + maxEntities on ExportBundleInput; truncation + metadata
theloom/viz/analytics.py              analytics guardrails (thresholds, betweenness/loop gating)
tapestry/schema/bundle.schema.json    regenerated
tapestry/src/lib/data.ts              + meta.truncated on TapestryBundleRaw
tests/test_viz_bundle.py              maxEntities cap + truncated metadata (low threshold)
tests/test_viz_analytics.py (or existing) betweenness/loop gating at low threshold
COMMANDS.md                           regenerated (maxEntities on export-bundle/visualize)

# Task 7 — frontend 50k rendering hardening
tapestry/src/views/explorer/layout.ts           barnesHutOptimize + scale settings
tapestry/src/views/explorer/Explorer.tsx         label LOD; hover suppressed during layout
tapestry/src/views/systems/SystemsView.tsx       label LOD
tapestry/src/views/chronicle/Chronicle.tsx       label LOD
tapestry/src/views/semantic/SemanticView.tsx     label LOD
tapestry/src/views/chronicle/eventWindow.ts      + visible-range helper (pure)
tapestry/src/views/chronicle/eventWindow.test.ts
tapestry/src/views/chronicle/EventList.tsx        virtualized rows
tapestry/src/views/chronicle/Chronicle.css        virtual-list spacer styles
theloom/viz/static/tapestry.html      rebuilt

# Task 8 — final docs + recorded benchmark
README.md                             saved-view mgmt, a11y/shortcuts, exports, scale notes
CLAUDE.md                             verify accuracy (layout, command count) — likely no change
COMMANDS.md                           confirm current (regenerated in T6 if inputs changed)
docs/superpowers/plans/2026-07-11-tapestry-phase-5.md   (this file, benchmark table filled in)
```

## Phase roadmap (closing note)

Phase 1 defined five phases: (1) Foundation + Explorer + Overview, (2) Systems +
Chronicle, (3) Semantic Map, (4) Live mode, (5) Polish. Phases 1–4 are shipped.
**This plan is Phase 5, and the roadmap ends here** — there is no further Tapestry
phase planned. Anything intentionally left out is enumerated in the spec's
`## Out of scope` (collaboration, write-back, a dedicated document-corpus view, 3D)
or in this plan's **Spec sweep** self-review, which reconciles every remaining
spec line against what ships in Tasks 1–8.

---

### Task 1: Saved-view management UI — rename, export/import, apply-on-load

Phase 1 shipped save/list/delete + apply of named views (per-graph, in
`localStorage`). Phase 5 completes the *management* surface the spec's
`## Cross-cutting → Shareable state` implies: **rename** a saved view,
**export/import** the graph's saved views as a JSON file (download/upload, so
views are portable between machines or shareable alongside a static export), and
**apply-on-load via URL** — open `…#view=<name>` and the named saved view (if it
exists in this browser for the current graph) applies immediately.

**Load the `dataviz` and `frontend-design` skills before writing UI/styles.** The
Views panel is an existing surface; new controls (rename inline-edit, Export,
Import) must sit in its rhythm, read in both themes, and never look like raw
browser defaults (a bare `<input type="file">` is not acceptable — wrap it).

**Files:**
- Modify: `tapestry/src/lib/savedViews.ts`, `tapestry/src/lib/savedViews.test.ts`
- Modify: `tapestry/src/views/explorer/Explorer.tsx` (the `ViewsMenu` component),
  `tapestry/src/views/explorer/Explorer.css`
- Modify: `tapestry/src/App.tsx` (apply-on-load branch)
- Create: `tapestry/e2e/savedviews.spec.ts`
- Rebuild: `theloom/viz/static/tapestry.html`

**Interfaces (verified against the shipped code):**
- `savedViews.ts` exports `SavedView { name; hash; savedAt }`, `listViews(graph)`,
  `saveView(graph, name, hash)` (overwrites by name — last-write-wins),
  `deleteView(graph, name)`, keyed `tapestry:views:${graph}` in `localStorage`.
- `downloadBlob(blob, filename)` is already exported from
  `tapestry/src/lib/exportSvg.ts` — reuse it for the JSON download.
- `applyHash(hash)` and `serializeState(state)` live in
  `tapestry/src/state/urlHash.ts`; a saved view's stored `hash` is itself a
  `#s=<json>` string. `App.tsx`'s mount effect runs
  `if (window.location.hash) applyHash(window.location.hash)` once.
- `ViewsMenu` in `Explorer.tsx` already has `graphKey`, `listViews`, `saveView`,
  `deleteView`, `applyHash`, and a `refresh()` that re-reads the list.
- Produces (add to `savedViews.ts`, all pure/`localStorage`-only so vitest covers
  them under happy-dom):
  - `renameView(graph, from, to): boolean` — renames unless `to` already exists
    or is blank (then returns `false`, no mutation); preserves `hash`/`savedAt`.
  - `serializeViews(graph): string` — a portable envelope
    `{ schema: "tapestry-views@1", graph, views: SavedView[] }`, JSON string.
  - `importViews(graph, json): { added: number; error?: string }` — parse +
    validate the envelope shape; merge each `{name,hash,savedAt}` via the same
    last-write-wins rule as `saveView`; a malformed payload returns
    `{ added: 0, error }` (never throws).
  - `resolveViewHash(graph, name): string | null` — the stored `hash` for a name,
    or `null` — used by apply-on-load.

- [ ] **Step 1: Write the failing test**

`tapestry/src/lib/savedViews.test.ts` — add cases (the file already tests
save/list/delete):

```typescript
import { beforeEach, describe, expect, it } from "vitest";
import { deleteView, importViews, listViews, renameView, resolveViewHash, saveView, serializeViews } from "./savedViews";

beforeEach(() => localStorage.clear());

describe("saved-view management", () => {
  it("renames a view, preserving its hash, and refuses a name collision", () => {
    saveView("g", "a", "#s=1");
    saveView("g", "b", "#s=2");
    expect(renameView("g", "a", "c")).toBe(true);
    expect(resolveViewHash("g", "c")).toBe("#s=1");
    expect(resolveViewHash("g", "a")).toBeNull();
    expect(renameView("g", "c", "b")).toBe(false); // b exists
    expect(resolveViewHash("g", "c")).toBe("#s=1"); // unchanged
  });

  it("round-trips export → import across graphs", () => {
    saveView("g", "a", "#s=1");
    const json = serializeViews("g");
    expect(importViews("h", json).added).toBe(1);
    expect(resolveViewHash("h", "a")).toBe("#s=1");
  });

  it("reports an error for a malformed import without throwing", () => {
    expect(importViews("g", "not json").added).toBe(0);
    expect(importViews("g", "not json").error).toBeTruthy();
    expect(listViews("g")).toEqual([]);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd tapestry && npm test` → FAIL
  (the new exports do not exist).

- [ ] **Step 3: Implement** — add the four functions to `savedViews.ts` (pure,
  mirroring the existing `storageKey`/`listViews` idiom). Then extend `ViewsMenu`
  in `Explorer.tsx`: each saved row gains a rename affordance (a rename button
  that swaps the row into an inline `<input>` + confirm; on submit call
  `renameView`, then `refresh()`; a duplicate name shows a small inline notice);
  add an **Export views** button (`downloadBlob(new Blob([serializeViews(graphKey)],
  {type:"application/json"}), exportFilename…)` — reuse the dated-filename helper
  once Task 5 lands, or just `${graphKey}-views-${date}.json` here) and an
  **Import views** control (a styled `<label>` wrapping a hidden
  `<input type="file" accept="application/json">`; on change, `FileReader`
  → `importViews(graphKey, text)` → `refresh()`, surfacing the `added`/`error`).
  In `App.tsx`'s mount effect, before `applyHash`, detect a `#view=<name>` hash:
  read `bundle.meta.graph`, `resolveViewHash(graph, name)`, and if non-null
  `applyHash(thatHash)` (which immediately rewrites the URL to the `#s=` form the
  subscriber keeps current); if null, fall through to the existing `applyHash`.

- [ ] **Step 4: Verify** — `cd tapestry && npm test` → new cases pass, existing
  green.

- [ ] **Step 5: e2e + build + gates + commit**

`tapestry/e2e/savedviews.spec.ts` — against the static `file://` build (mirror
`smoke.spec.ts`'s `beforeAll` fixture injection): open the Views menu, save a view,
rename it, and assert an Import round-trip repopulates it. For the download, use
Playwright's `page.waitForEvent("download")`; for upload, `setInputFiles` on the
hidden file input with a temp JSON written in the spec.

```bash
cd tapestry && npm run build && cd ..
test "$(grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html)" = "1" && echo OK
```

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build && npm run e2e`

```bash
git add tapestry/src/lib/savedViews.ts tapestry/src/lib/savedViews.test.ts tapestry/src/views/explorer/Explorer.tsx tapestry/src/views/explorer/Explorer.css tapestry/src/App.tsx tapestry/e2e/savedviews.spec.ts theloom/viz/static/tapestry.html
git commit -m "Add saved-view management: rename, export/import, and apply-on-load" -- tapestry/src/lib/savedViews.ts tapestry/src/lib/savedViews.test.ts tapestry/src/views/explorer/Explorer.tsx tapestry/src/views/explorer/Explorer.css tapestry/src/App.tsx tapestry/e2e/savedviews.spec.ts theloom/viz/static/tapestry.html
```

---

### Task 2: Keyboard operability — composite-widget ARIA + focus + aria-live

Make the header's composite widgets follow their WAI-ARIA keyboard patterns, give
every interactive element a visible focus ring, and announce async updates
politely. Today the tablist (`role="tablist"`/`role="tab"`) and the theme group
(`role="radiogroup"`/`role="radio"`) render the right roles but rely on default
button tabbing — arrow keys do nothing and every tab/radio is in the tab order,
which the ARIA patterns forbid (a tablist/radiogroup is **one** tab stop, arrows
move within). Live-mode refresh and graph-switch complete silently.

**Load the `dataviz` and `frontend-design` skills before writing styles** — the
focus ring is a design-system token (one ring, both themes, WCAG-AA against every
surface it lands on), not an ad-hoc outline.

**Files:**
- Create: `tapestry/src/lib/roving.ts`, `tapestry/src/lib/roving.test.ts`
- Modify: `tapestry/src/App.tsx`, `tapestry/src/App.css`,
  `tapestry/src/design/tokens.css`
- Create: `tapestry/e2e/keyboard.spec.ts`
- Rebuild: `theloom/viz/static/tapestry.html`

**Interfaces (verified):**
- `App.tsx` renders the tabs from `VIEWS` (5 items) as `<button role="tab"
  aria-selected>` inside `<nav role="tablist">`, and the themes from `THEMES`
  (3 items) as `<button role="radio" aria-checked>` inside `<div
  role="radiogroup">`. Selection state is `useTapestry`'s `view`/`theme`.
- `useLiveControls()` gives `{ live, graphs, currentGraph, setGraph, refresh }`;
  the `brand__live` span is `role="status"` (already an `aria-live=polite`
  region by role), the switcher is a `<select>`, refresh is a `<button>`.
- Produces: `nextRovingIndex(current: number, count: number, key: string): number
  | null` — maps `ArrowRight`/`ArrowDown`→next (wrap), `ArrowLeft`/`ArrowUp`→prev
  (wrap), `Home`→0, `End`→count-1, else `null`. Pure — vitest-covered.

- [ ] **Step 1: Write the failing test** — `tapestry/src/lib/roving.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { nextRovingIndex } from "./roving";

describe("nextRovingIndex", () => {
  it("wraps forward and backward", () => {
    expect(nextRovingIndex(4, 5, "ArrowRight")).toBe(0);
    expect(nextRovingIndex(0, 5, "ArrowLeft")).toBe(4);
  });
  it("honors Home/End and ignores other keys", () => {
    expect(nextRovingIndex(3, 5, "Home")).toBe(0);
    expect(nextRovingIndex(3, 5, "End")).toBe(4);
    expect(nextRovingIndex(3, 5, "a")).toBeNull();
  });
});
```

- [ ] **Step 2: Verify failure** — `cd tapestry && npm test` → FAIL.

- [ ] **Step 3: Implement**
  - `roving.ts`: the pure helper above.
  - `App.tsx` tablist: give the active tab `tabIndex={0}` and the rest
    `tabIndex={-1}`; add `onKeyDown` on the `<nav role="tablist">` that computes
    `nextRovingIndex(currentTabIndex, VIEWS.length, e.key)`, and on a non-null
    result `e.preventDefault()`, `setView(VIEWS[next].id)`, and focus that tab
    button (a ref array). Automatic activation (move = select) is the common,
    accessible choice here and matches the click behaviour.
  - `App.tsx` theme radiogroup: same roving pattern over `THEMES` (active =
    checked radio `tabIndex={0}`, rest `-1`; arrows move+`setTheme`).
  - `App.tsx` a polite live region: add a visually-hidden
    `<div aria-live="polite" className="sr-only">` whose text updates on
    live refresh ("Refreshed <graph>") and graph switch ("Loaded <graph>"),
    driven by an effect on `currentGraph`/a refresh nonce. (The brush-count and
    path-bar `role="status"` regions already announce; leave them.)
  - `App.css` + `tokens.css`: a `--focus-ring` token (both `:root` and
    `[data-theme="dark"]`), and a shared `:focus-visible { outline: 2px solid
    var(--focus-ring); outline-offset: 2px; }` rule (or per-component classes)
    covering tabs, theme buttons, the graph `<select>`, refresh, and the canvas
    `role="tabpanel"` sections (which already carry `tabIndex={0}`). Add a
    `.sr-only` utility (clip-rect visually-hidden) for the live region.

- [ ] **Step 4: Verify** — `cd tapestry && npm test` (roving cases pass).

- [ ] **Step 5: e2e + build + gates + commit**

`tapestry/e2e/keyboard.spec.ts` (static build): focus the first tab, press
`ArrowRight`, assert focus and `aria-selected` moved to the next tab and the
panel changed; press `Home`, assert it returns; repeat for the theme radiogroup;
assert a focused tab shows a visible outline (`toHaveCSS("outline-style",
"solid")` after `.focus()`).

```bash
cd tapestry && npm run build && cd ..
test "$(grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html)" = "1" && echo OK
```

Run the full gate set (mypy/ruff/pytest + `cd tapestry && npm test && npm run build && npm run e2e`), then:

```bash
git add tapestry/src/lib/roving.ts tapestry/src/lib/roving.test.ts tapestry/src/App.tsx tapestry/src/App.css tapestry/src/design/tokens.css tapestry/e2e/keyboard.spec.ts theloom/viz/static/tapestry.html
git commit -m "Add keyboard operability and ARIA patterns to the header and views" -- tapestry/src/lib/roving.ts tapestry/src/lib/roving.test.ts tapestry/src/App.tsx tapestry/src/App.css tapestry/src/design/tokens.css tapestry/e2e/keyboard.spec.ts theloom/viz/static/tapestry.html
```

---

### Task 3: Help overlay (`?`) + canvas keyboard alternatives

Make the shortcuts discoverable and give the one pointer-only canvas action a
keyboard path. Add a focus-trapped `?` help dialog listing every shortcut (global
+ per view), reachable by the `?` key and a header `?` button, and add a
keyboard **cluster-brush** to the Semantic Map so a keyboard user can perform its
core cross-view action (brush a set → view in Explorer) without a lasso.

**Load the `dataviz` and `frontend-design` skills before writing the overlay.**
The dialog is a deliberate surface — a legible shortcut table, both themes, clear
hierarchy — not a raw list.

**Files:**
- Create: `tapestry/src/views/HelpOverlay.tsx`, `tapestry/src/views/HelpOverlay.css`
- Modify: `tapestry/src/App.tsx` (trigger + wiring)
- Modify: `tapestry/src/views/semantic/SemanticView.tsx`,
  `tapestry/src/views/semantic/SemanticMap.css`
- Create: `tapestry/e2e/help.spec.ts`
- Rebuild: `theloom/viz/static/tapestry.html`

**Interfaces (verified):**
- `useKeyboard(bindings)` (`lib/keyboard.ts`) attaches a `window` keydown
  listener, ignores `meta/ctrl/alt` and typing targets, and dispatches by
  `event.key`. `?` is `event.key === "?"` and is not a modifier — bind it in
  `App.tsx` to open the overlay. (Do not bind it inside a view; App owns global
  shortcuts.)
- Explorer's shortcuts (to document): `/` focus search, `p` path mode, `f` fit,
  `Esc` clear, arrows walk neighbours. Header: tab/theme arrows (Task 2).
  Chronicle: scrubber arrows, space/play button. Systems: loop rows, animate-flow.
  Semantic: hulls toggle, lasso, and the new cluster-brush.
- Semantic clusters come from `bundle.semantic?.clusters` (`{ id, label,
  entityIds, size }[]`); `setBrushed(ids)` and `setView("explorer")` are already
  used by the pointer path.

- [ ] **Step 1: Write the failing test** — `tapestry/e2e/help.spec.ts` (static
  build): press `?`, assert `getByRole("dialog")` is visible and focus is inside
  it; press `Tab` a few times and assert focus stays within the dialog; press
  `Escape` and assert it closes and focus returns to the `?` trigger button. Then
  on the Semantic tab, focus the cluster-brush control, activate a cluster, and
  assert the `… brushed` status appears. (Run to verify it fails — no dialog, no
  cluster control.)

- [ ] **Step 2: Verify failure** — `cd tapestry && npm run build && npm run e2e`
  → FAIL.

- [ ] **Step 3: Implement**
  - `HelpOverlay.tsx`: a controlled `{ open, onClose }` modal. When open, render
    a backdrop + `<div role="dialog" aria-modal="true" aria-labelledby="help-title">`
    containing a shortcut table grouped by scope. On open, move focus to the close
    button; implement a focus trap (keydown handler: `Tab`/`Shift+Tab` wrap across
    the dialog's focusable elements; `Escape` calls `onClose`). Restore focus to
    the opener on close (App passes a ref or re-focuses the trigger in its
    `onClose`).
  - `App.tsx`: `const [help, setHelp] = useState(false)`; `useKeyboard({ "?": ()
    => setHelp(true) })` (App does not currently call `useKeyboard` — add it);
    render `<HelpOverlay open={help} onClose={() => setHelp(false)} />`; add a
    header `<button aria-keyshortcuts="?" aria-label="Keyboard shortcuts"
    onClick={() => setHelp(true)}>` with a `?`/keyboard glyph, in the header
    idiom next to the theme group.
  - `SemanticView.tsx`: add a small keyboard-operable **cluster-brush**: when
    `hasClusters`, render a compact list/menu of clusters (buttons, each labelled
    with the cluster label + size); activating one calls
    `setBrushed(cluster.entityIds)` — the exact effect the lasso produces — so a
    keyboard user reaches the same `… brushed → View in Explorer` flow. Keep the
    lasso as the pointer path; the cluster-brush is the keyboard equivalent.
    Guard it behind `hasClusters` (its empty state already exists).

- [ ] **Step 4: Verify** — `cd tapestry && npm test` still green (no pure-logic
  change); `npm run build` then `npm run e2e` passes the new help spec.

- [ ] **Step 5: build + gates + commit**

```bash
cd tapestry && npm run build && cd ..
test "$(grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html)" = "1" && echo OK
```

Full gates, then:

```bash
git add tapestry/src/views/HelpOverlay.tsx tapestry/src/views/HelpOverlay.css tapestry/src/App.tsx tapestry/src/views/semantic/SemanticView.tsx tapestry/src/views/semantic/SemanticMap.css tapestry/e2e/help.spec.ts theloom/viz/static/tapestry.html
git commit -m "Add the keyboard shortcut help overlay and canvas keyboard alternatives" -- tapestry/src/views/HelpOverlay.tsx tapestry/src/views/HelpOverlay.css tapestry/src/App.tsx tapestry/src/views/semantic/SemanticView.tsx tapestry/src/views/semantic/SemanticMap.css tapestry/e2e/help.spec.ts theloom/viz/static/tapestry.html
```

---

### Task 4: `@axe-core/playwright` accessibility audit across all five tabs

Verify the accessibility work with an automated scan and fix whatever it finds.
Add an `AxeBuilder` scan of each of the five tabs in **both** themes and drive
`serious`/`critical` violations to **zero**. This task runs *after* the keyboard
and help work so it audits the finished surface.

**Files:**
- Modify: `tapestry/package.json` (+`@axe-core/playwright`),
  `tapestry/package-lock.json`
- Create: `tapestry/e2e/a11y.spec.ts`
- Modify: whatever files the scan flags (markup/CSS in the offending view)
- Rebuild: `theloom/viz/static/tapestry.html` (only if a fix changed source)

**Interfaces (verified):**
- The `tapestry` CI job already runs `npm run e2e` (all of `e2e/**`) after
  `npm ci` and `npm run build`, with no FalkorDB and no Python — so a new
  `e2e/a11y.spec.ts` and a devDependency need no CI change; `npm ci` installs the
  devDependency and the built `file://` page is what axe scans.
- Each view renders a `<section role="tabpanel" id="panel-<id>">`; switching tabs
  is a click on `role="tab"`; theme is the `role="radio"` group. `AxeBuilder({
  page })` works on a `file://` page.

- [ ] **Step 1: Write the failing (or flagging) test** — `tapestry/e2e/a11y.spec.ts`:

```typescript
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
// reuse smoke.spec's fixture-injection beforeAll (copy the OUT + beforeAll block)

const TABS = ["explorer", "overview", "systems", "chronicle", "semantic"] as const;

for (const theme of ["light", "dark"] as const) {
  for (const tab of TABS) {
    test(`no serious/critical a11y violations: ${tab} (${theme})`, async ({ page }) => {
      await page.goto(`file://${OUT}`);
      // set theme via the radiogroup, then open the tab
      await page.getByRole("radio", { name: new RegExp(theme, "i") }).click();
      await page.getByRole("tab", { name: new RegExp(tab, "i") }).click();
      await expect(page.locator(`#panel-${tab}`)).toBeVisible();
      const results = await new AxeBuilder({ page })
        .include(`#panel-${tab}`)
        .analyze();
      const bad = results.violations.filter(
        (v) => v.impact === "serious" || v.impact === "critical",
      );
      expect(bad, JSON.stringify(bad, null, 2)).toEqual([]);
    });
  }
}
```

Also add one scan of the header + help overlay open.

- [ ] **Step 2: Run to see findings** — `npm install --save-dev
  @axe-core/playwright` (regenerates `package-lock.json`), then `cd tapestry &&
  npm run build && npm run e2e -- a11y`. Expect some findings (likely
  colour-contrast on a token, an unlabelled control, or a canvas without an
  accessible name).

- [ ] **Step 3: Fix findings** — address each `serious`/`critical` in the
  offending source file (e.g. bump a token's lightness to reach AA, add an
  `aria-label` to a control, give a canvas `role="img"` + label). Do **not**
  suppress a rule to pass; fix the underlying issue. Re-run until the filtered
  list is empty in both themes. If a rule is a documented false positive for a
  canvas visualization (e.g. `canvas` with no text alternative where the panel
  already carries a labelled description), narrow the `.exclude()` to that exact
  node **with a comment** rather than dropping the rule globally.

- [ ] **Step 4: Verify** — `cd tapestry && npm run build && npm run e2e` all
  green (a11y + existing specs).

- [ ] **Step 5: gates + commit**

If a fix changed source, rebuild the template and re-check the sentinel. Full
gates, then:

```bash
git add tapestry/package.json tapestry/package-lock.json tapestry/e2e/a11y.spec.ts <any fixed files> theloom/viz/static/tapestry.html
git commit -m "Add an axe-core accessibility audit across all five views" -- tapestry/package.json tapestry/package-lock.json tapestry/e2e/a11y.spec.ts <any fixed files> theloom/viz/static/tapestry.html
```

---

### Task 5: Export refinements — WYSIWYG for every view, legends, dated filenames

The spec's `## Cross-cutting → Exports` promises **every** view exports a PNG and
SVG of what's on screen. Phase 1 shipped that for the Explorer only. Extend the
shared `exportSvg.ts` and wire the other four views: the three Sigma views
(Systems, Chronicle, Semantic) reuse the same PNG/SVG functions; the DOM Overview
gets a print stylesheet + "Print" button (pragmatic — arbitrary DOM cannot be
rasterized offline without a heavy dependency, and Save-as-PDF is the right tool
for a dashboard). Add a theme-honoring PNG background, an optional legend to the
SVG, and a `<graph>-<view>-<date>` filename convention.

**Load the `dataviz` and `frontend-design` skills before adding controls/print
CSS.** The legend and print layout are design artifacts.

**Files:**
- Modify: `tapestry/src/lib/exportSvg.ts`, `tapestry/src/lib/exportSvg.test.ts`
- Modify: `tapestry/src/views/systems/SystemsView.tsx` + `Systems.css`,
  `tapestry/src/views/chronicle/Chronicle.tsx` + `Chronicle.css`,
  `tapestry/src/views/semantic/SemanticView.tsx` + `SemanticMap.css`
- Modify: `tapestry/src/views/overview/Overview.tsx` + `Overview.css`
- Modify: `tapestry/src/views/explorer/Explorer.tsx` (adopt the dated filename)
- Create: `tapestry/e2e/export.spec.ts`
- Rebuild: `theloom/viz/static/tapestry.html`

**Interfaces (verified):**
- `exportSvg.ts` exports `graphToSvg(graph, visible, viewport, options)`
  (pure; `SvgExportOptions { textColor?, background?, padding? }`),
  `exportSvgFile(sigma, graph, visible, options, filename)`,
  `exportPngFile(sigma, filename): Promise<void>` (flattens sigma's layered
  canvases in paint order — but does **not** fill a background, so PNGs are
  currently transparent), `downloadBlob(blob, filename)`, and the `Visibility`
  type (`{ visibleNodes: Set, visibleEdges: Set }`).
- The Explorer builds `Visibility` via `applyFilters(graph, filters)`; the
  Systems/Chronicle/Semantic graphs have no filter layer — build an **all-visible**
  `Visibility` (`{ visibleNodes: new Set(graph.nodes()), visibleEdges: new
  Set(graph.edges()) }`) for their SVG export.
- Produces (add to `exportSvg.ts`):
  - `exportFilename(graph: string, view: string, ext: string): string` →
    `${graph}-${view}-${YYYY-MM-DD}.${ext}` (pure — vitest).
  - `exportPngFile(sigma, filename, background?)` — gains an optional background:
    fill the output canvas with it before drawing the layers (theme-honoring PNG).
  - `graphToSvg(..., options)` — `SvgExportOptions` gains an optional
    `legend?: { label: string; color: string }[]`; when present, draw a small
    legend block in a corner (swatch + label rows) inside the SVG.

- [ ] **Step 1: Write the failing test** — `tapestry/src/lib/exportSvg.test.ts`
  add: `exportFilename("g","systems","svg")` matches `/^g-systems-\d{4}-\d{2}-\d{2}\.svg$/`;
  and a `graphToSvg` call with a `legend` produces an SVG string containing the
  legend labels. Run → FAIL.

- [ ] **Step 2: Verify failure** — `cd tapestry && npm test` → FAIL.

- [ ] **Step 3: Implement**
  - `exportSvg.ts`: add `exportFilename`; add the background fill to
    `exportPngFile` (fill first, then `drawImage` the layers — the layer order is
    unchanged); add the optional `legend` rendering to `graphToSvg`.
  - Explorer: switch its filenames to `exportFilename(graphKey, "explorer", ext)`,
    pass the theme `--color-canvas` to `exportPngFile` as the background, and pass
    an entity-type `legend` (the types actually present) to the SVG.
  - Systems: add a small export control (SVG/PNG) to `systems__controls`; SVG via
    `graphToSvg` with an all-visible `Visibility`, the causal graph, the current
    camera state, `--color-canvas` background, and a **polarity legend**
    (`+ amplifies` / `− inhibits` from the `--polarity-*` tokens); PNG via
    `exportPngFile(sigma, exportFilename(graph,"systems","png"), canvasBg)`. Title
    text notes the polarity glyphs/leverage badges are on-screen overlays not in
    the PNG.
  - Chronicle: same pattern, view `"chronicle"`, legend = the live/invalidated
    swatches it already shows.
  - Semantic: same pattern, view `"semantic"`; the projection *is* the layout, so
    node positions export directly; legend = the projection method + cluster
    swatch. (Hull outlines are the SVG overlay, not exported — note in the title.)
  - Overview: add a **Print** button and an `@media print` block in `Overview.css`
    that lays the tiles/cards out cleanly (drop the app chrome, expand widths,
    avoid page breaks inside a card); the button calls `window.print()`. This is
    the DOM view's WYSIWYG path (Save-as-PDF), stated plainly in the button title.

- [ ] **Step 4: Verify** — `cd tapestry && npm test` (filename/legend cases pass).

- [ ] **Step 5: e2e + build + gates + commit**

`tapestry/e2e/export.spec.ts`: for each Sigma view, open its export control and
assert a `download` event fires with the `<graph>-<view>-<date>` name pattern; for
Overview, assert the Print button exists (do not invoke `window.print()` in CI —
just assert wiring). Full gates + rebuild + sentinel check, then:

```bash
git add tapestry/src/lib/exportSvg.ts tapestry/src/lib/exportSvg.test.ts tapestry/src/views/systems/SystemsView.tsx tapestry/src/views/systems/Systems.css tapestry/src/views/chronicle/Chronicle.tsx tapestry/src/views/chronicle/Chronicle.css tapestry/src/views/semantic/SemanticView.tsx tapestry/src/views/semantic/SemanticMap.css tapestry/src/views/overview/Overview.tsx tapestry/src/views/overview/Overview.css tapestry/src/views/explorer/Explorer.tsx tapestry/e2e/export.spec.ts theloom/viz/static/tapestry.html
git commit -m "Add WYSIWYG export to every view with legends and dated filenames" -- tapestry/src/lib/exportSvg.ts tapestry/src/lib/exportSvg.test.ts tapestry/src/views/systems/SystemsView.tsx tapestry/src/views/systems/Systems.css tapestry/src/views/chronicle/Chronicle.tsx tapestry/src/views/chronicle/Chronicle.css tapestry/src/views/semantic/SemanticView.tsx tapestry/src/views/semantic/SemanticMap.css tapestry/src/views/overview/Overview.tsx tapestry/src/views/overview/Overview.css tapestry/src/views/explorer/Explorer.tsx tapestry/e2e/export.spec.ts theloom/viz/static/tapestry.html
```

---

### Task 6: Python assembly guardrails at scale + benchmark generator

Make bundle assembly *safe* at 50k nodes: an explicit `maxEntities` cap that
truncates to the top-degree core and records `truncated` metadata (instead of
silently shipping a giant, slow bundle), and analytics guardrails so the two
super-linear analyses — rustworkx betweenness (O(V·E)) and Johnson loop
enumeration (worst-case exponential) — cannot dominate a huge graph. Also add the
synthetic-graph generator Tasks 7/8 measure against.

**Files:**
- Create: `scripts/gen_bench_graph.py` (output uncommitted)
- Modify: `theloom/viz/schema.py` (add `Truncated` + `TapestryMeta.truncated`)
- Modify: `theloom/viz/bundle.py` (`maxEntities` input + truncation)
- Modify: `theloom/viz/analytics.py` (guardrails)
- Regenerate: `tapestry/schema/bundle.schema.json`
- Modify: `tapestry/src/lib/data.ts` (mirror `meta.truncated`)
- Modify: `tests/test_viz_bundle.py`, and the analytics test file
- Regenerate: `COMMANDS.md`

**Interfaces (verified):**
- `assemble_bundle(params, multi)` (`bundle.py`) calls `resolve_scope` → `(entities,
  relations, label)`, then `assemble_analytics(target, multi)`, and builds
  `TapestryMeta(...)`. Serializes `model_dump(by_alias=True, exclude_none=True)`.
- `ExportBundleInput` fields: `graph`, `scope`, `include`, `title`, `as_of`
  (alias `asOf`). Add `max_entities: int | None = Field(default=None,
  alias="maxEntities")`.
- `TapestryMeta` (`schema.py`) is a `LoomModel` with aliased camelCase fields;
  `SCHEMA_VERSION = 1`. The committed schema drift test
  (`tests/test_viz_schema_drift.py`) asserts `committed == bundle_json_schema()`
  **exactly** — regenerate the JSON after the model change.
- `assemble_analytics(graph, multi)` (`analytics.py`) runs three centralities
  (incl. `analyze_centrality(algorithm="betweenness")`), `detect_components`,
  `detect_loops(persist=False)` (no `maxSize`), `list_leverage_points`, and
  `multi.bridges.list_bridges()`. `AnalyzeCentralityInput` has `limit`;
  `DetectLoopsInput` has `max_size` (alias `maxSize`).

- [ ] **Step 1: Write the failing test** — in `tests/test_viz_bundle.py`, drive
  the cap with a **tiny** graph and a **low** cap (never build 50k in a test):

```python
def test_max_entities_truncates_and_records_metadata(multi: MultiGraph) -> None:
    store = multi.get_store()
    for name in ("a", "b", "c"):
        store.create_entity(EntityCreate.model_validate(
            {"name": name, "entityType": "concept", "observations": []}))
    bundle = assemble_bundle(
        ExportBundleInput.model_validate({"maxEntities": 2}), multi)
    assert bundle["meta"]["entityCount"] == 2
    assert bundle["meta"]["truncated"]["total"] == 3
    assert bundle["meta"]["truncated"]["kept"] == 2

def test_no_truncated_key_when_under_cap(multi: MultiGraph) -> None:
    multi.get_store().create_entity(EntityCreate.model_validate(
        {"name": "a", "entityType": "concept", "observations": []}))
    bundle = assemble_bundle(ExportBundleInput.model_validate({}), multi)
    assert "truncated" not in bundle["meta"]  # exclude_none drops it
```

For the analytics guardrails, make the thresholds **module constants** and either
monkeypatch them low or pass an explicit override, then assert (with a small
graph) that betweenness is omitted / loops are skipped above the threshold and
present below it. Keep it model-free (no vectors).

- [ ] **Step 2: Verify failure** — `uv run pytest tests/test_viz_bundle.py -v` →
  FAIL.

- [ ] **Step 3: Implement**
  - `schema.py`: add `class Truncated(LoomModel): total: int; kept: int; by: str`
    (aliases as needed — these names have no camel/snake gap) and
    `truncated: Truncated | None = None` on `TapestryMeta`. Regenerate:
    `uv run python -m theloom.viz.schema`. Keep `SCHEMA_VERSION = 1`.
  - `bundle.py`: add `max_entities` to `ExportBundleInput`. After `resolve_scope`,
    if `max_entities is not None and len(entities) > max_entities`, compute a
    cheap degree from the relation list (O(E), no centrality call), keep the
    top-`max_entities` entities by degree (stable tiebreak on id for reproducible
    output), filter relations to the induced set, and set
    `truncated=Truncated(total=<pre-cap count>, kept=len(entities), by="degree")`.
    Pass `truncated` into `TapestryMeta`. `exclude_none=True` drops it when unset.
  - `analytics.py`: introduce module constants (choose and **state real numbers**
    in the plan — recommended starting points, tune during Task 8):
    `BETWEENNESS_MAX_NODES = 5_000`, `LOOP_MAX_NODES = 10_000`,
    `LOOP_MAX_SIZE = 12`, `CENTRALITY_SHIP_LIMIT = 1_000`. Gate: compute node
    count once (from the hydrated graph or the entity list); when it exceeds
    `BETWEENNESS_MAX_NODES`, **omit** the `betweenness` key from the centrality
    dict (ship `degree` + `pagerank` only) — the Overview/Explorer already tolerate
    a missing algorithm. When it exceeds `LOOP_MAX_NODES`, skip loop enumeration
    (ship `loops=[]`); otherwise pass `max_size=LOOP_MAX_SIZE` to bound cycle
    length. Ship at most `CENTRALITY_SHIP_LIMIT` scores per algorithm via
    `AnalyzeCentralityInput(limit=…)`. **All thresholds are far above fixture
    scale**, so every existing test is unaffected. Keep the guardrail decisions in
    a short docstring so the "why" is legible.
  - `data.ts`: add `truncated?: { total: number; kept: number; by: string }` to
    `TapestryBundleRaw['meta']`. (Optionally surface it in the header — a small
    "showing top N of M" note — but that can be a one-line addition; the data
    contract is the requirement.)
  - `scripts/gen_bench_graph.py`: build a `tapestry-bench` graph with `--entities
    50000 --relations 100000` (argparse; defaults those), **no vectors**, mostly
    `related_to` relations (explicit `"polarity": null`) with a fraction of causal
    edges (`"+"`/`"-"`) for realism, via the committed bulk path
    (`theloom.operations.bulk.bulk_import`, batched under its `MAX_ENTITIES_LIMIT`)
    or direct `store.create_entity`/`create_relation` loops. It targets
    `tapestry-bench` only — **never** the default graph. Print a summary line. Verify
    the exact create signatures against `theloom/store/falkor.py` before
    finalizing.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_viz_bundle.py <analytics test>
  -v` pass; `uv run pytest tests/test_viz_schema_drift.py` green (schema
  regenerated); `cd tapestry && npm test` green (ajv still validates the fixture).

- [ ] **Step 5: gates + regenerate docs + commit**

```bash
uv run loom --generate-docs > COMMANDS.md   # maxEntities now documented on export-bundle/visualize
```

Full gates (mypy/ruff/pytest + `cd tapestry && npm test && npm run build`; the
build check confirms the sentinel and that `data.ts` still compiles). Confirm
`tests/test_generate_docs.py` passes (it regenerates from the registry). Then:

```bash
git add scripts/gen_bench_graph.py theloom/viz/schema.py theloom/viz/bundle.py theloom/viz/analytics.py tapestry/schema/bundle.schema.json tapestry/src/lib/data.ts tests/test_viz_bundle.py <analytics test file> COMMANDS.md theloom/viz/static/tapestry.html
git commit -m "Add large-graph assembly guardrails and a benchmark graph generator" -- scripts/gen_bench_graph.py theloom/viz/schema.py theloom/viz/bundle.py theloom/viz/analytics.py tapestry/schema/bundle.schema.json tapestry/src/lib/data.ts tests/test_viz_bundle.py <analytics test file> COMMANDS.md theloom/viz/static/tapestry.html
```

(Rebuild `tapestry/viz/static/tapestry.html` only if `data.ts`'s change altered
built output — it does, since `data.ts` is in the bundle; include it in the
pathspec and rerun the sentinel check.)

---

### Task 7: Frontend rendering hardening for large graphs

Make the SPA usable at 50k nodes / 100k edges: Barnes-Hut ForceAtlas2, sigma
label level-of-detail, hover-effect suppression while the layout runs, and a
**virtualized** Chronicle event list (100k rows in a `<ul>` is fatal today).
Every change is invisible at fixture scale.

**Load the `dataviz` and `frontend-design` skills before changing render/label
settings** — label LOD is a legibility decision.

**Files:**
- Modify: `tapestry/src/views/explorer/layout.ts`
- Modify: `tapestry/src/views/explorer/Explorer.tsx` (label LOD, hover guard)
- Modify: `tapestry/src/views/systems/SystemsView.tsx`,
  `tapestry/src/views/chronicle/Chronicle.tsx`,
  `tapestry/src/views/semantic/SemanticView.tsx` (label LOD)
- Create: `tapestry/src/views/chronicle/eventWindow.ts`,
  `tapestry/src/views/chronicle/eventWindow.test.ts`
- Modify: `tapestry/src/views/chronicle/EventList.tsx`,
  `tapestry/src/views/chronicle/Chronicle.css`
- Rebuild: `theloom/viz/static/tapestry.html`

**Interfaces (verified):**
- `layout.ts` builds settings via `forceAtlas2.inferSettings(graph)` and runs the
  worker `FA2Layout` (with a sync rAF fallback). `inferSettings` enables
  `barnesHutOptimize` for large graphs, but make it explicit and add
  `barnesHutTheta` for the big case.
- Each view constructs `new Sigma(graph, container, { … labelDensity, labelFont,
  labelColor … })`. **None set `labelRenderedSizeThreshold`** — add it so only
  larger (higher-degree) nodes get labels once a graph is big.
- Explorer's `enterNode` handler builds a neighbour set and calls
  `sigma.refresh()`; it has a `running` state (layout active) already in scope.
- `EventList.tsx` maps **every** `timeline.events` row into a `<ul>`, keeps a
  `currentRowRef` scrolled into view, and jumps the scrubber on click. This is the
  100k-row problem.
- Produces: `visibleRange(scrollTop, rowHeight, viewportHeight, count, overscan):
  { start: number; end: number }` — pure, vitest-covered.

- [ ] **Step 1: Write the failing test** — `eventWindow.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { visibleRange } from "./eventWindow";

describe("visibleRange", () => {
  it("windows a large list with overscan and clamps to bounds", () => {
    expect(visibleRange(0, 20, 200, 100000, 5)).toEqual({ start: 0, end: 15 });
    const mid = visibleRange(2000, 20, 200, 100000, 5);
    expect(mid.start).toBe(95);   // floor(2000/20) - 5
    expect(mid.end).toBe(115);    // +10 rows +5 overscan
  });
});
```

- [ ] **Step 2: Verify failure** — `cd tapestry && npm test` → FAIL.

- [ ] **Step 3: Implement**
  - `eventWindow.ts`: the pure `visibleRange` (clamp `start>=0`, `end<=count`).
  - `EventList.tsx`: virtualize. Measure/assume a fixed row height; on the list's
    scroll, compute `visibleRange(scrollTop, rowH, clientH, events.length,
    overscan)` and render only that slice, wrapped in a top spacer
    (`start*rowH`) and bottom spacer (`(count-end)*rowH`) so the scrollbar length
    is correct. Preserve click-to-jump and the "current row" behaviour: when the
    current index is outside the window, scroll it into range (compute its offset
    directly rather than relying on a DOM ref that may be unmounted). Keep the
    small-list path identical in output (at fixture scale the whole list is within
    one window).
  - `layout.ts`: for `graph.order` above a threshold (e.g. 3000), pass explicit
    `{ barnesHutOptimize: true, barnesHutTheta: 0.6 }` merged over
    `inferSettings`, and consider fewer sync-fallback iterations so the fallback
    stays responsive.
  - Each Sigma view: add `labelRenderedSizeThreshold` (e.g. 6) to the settings so
    labels thin out at scale; keep `labelDensity` as-is. Tune the constant so the
    fixture still shows labels (its nodes are small; verify the fixture e2e still
    finds its labels/canvas — if the threshold hides fixture labels, gate it on
    `graph.order > N`).
  - Explorer: in `enterNode`/`leaveNode`, early-return when the layout is
    `running` (skip the neighbour-set build + refresh) so hover does not fight the
    physics loop on a large graph.

- [ ] **Step 4: Verify** — `cd tapestry && npm test` (window cases pass); build;
  `npm run e2e` — the existing Chronicle spec asserts `.events__row` counts
  (e.g. 35 rows) and a `--current` row; **confirm the virtualized list still
  satisfies those assertions** at fixture scale (all rows within the window) or
  adjust the spec's expectations deliberately if the DOM shape changed (prefer
  keeping all fixture rows rendered so the spec is untouched).

- [ ] **Step 5: gates + commit**

Rebuild + sentinel check, full gates, then:

```bash
git add tapestry/src/views/explorer/layout.ts tapestry/src/views/explorer/Explorer.tsx tapestry/src/views/systems/SystemsView.tsx tapestry/src/views/chronicle/Chronicle.tsx tapestry/src/views/semantic/SemanticView.tsx tapestry/src/views/chronicle/eventWindow.ts tapestry/src/views/chronicle/eventWindow.test.ts tapestry/src/views/chronicle/EventList.tsx tapestry/src/views/chronicle/Chronicle.css theloom/viz/static/tapestry.html
git commit -m "Harden the SPA for large graphs: layout, label LOD, and event virtualization" -- tapestry/src/views/explorer/layout.ts tapestry/src/views/explorer/Explorer.tsx tapestry/src/views/systems/SystemsView.tsx tapestry/src/views/chronicle/Chronicle.tsx tapestry/src/views/semantic/SemanticView.tsx tapestry/src/views/chronicle/eventWindow.ts tapestry/src/views/chronicle/eventWindow.test.ts tapestry/src/views/chronicle/EventList.tsx tapestry/src/views/chronicle/Chronicle.css theloom/viz/static/tapestry.html
```

---

### Task 8: Final docs sweep + recorded scale benchmark

Close the phase: polish the docs to describe the finished feature, verify the
`CLAUDE.md` layout and command count are accurate, and **run the 50k benchmark
once on the dev machine and record the numbers in this plan** (no CI wall-clock
assertion — CLAUDE.md forbids it).

**Files:**
- Modify: `README.md`
- Verify (edit only if inaccurate): `CLAUDE.md`, `COMMANDS.md`
- Modify: `docs/superpowers/plans/2026-07-11-tapestry-phase-5.md` (fill the
  benchmark table below)

- [ ] **Step 1: Docs**
  - `README.md` `## Visualization`: add a short **Saved views** note (rename,
    export/import JSON, `#view=<name>` deep link), an **Accessibility & keyboard**
    note (the `?` help overlay, tab/theme arrow navigation, full keyboard
    operation, per-view shortcuts), an **Exports** note (every view → PNG/SVG with
    legend and `<graph>-<view>-<date>` filename; Overview → Print/Save-as-PDF),
    and a **Scale** note (50k-node target; the `maxEntities` cap with `truncated`
    metadata; analytics guardrails). There is **no `CHANGELOG.md`** in the repo —
    do **not** invent one; a concise feature summary belongs in this README
    section (the standalone-project README is the changelog surface here).
  - `CLAUDE.md`: verify the `## Layout` tree already lists `viz/` and `tapestry/`
    (it does) and the "**153 commands**" count is correct (Phase 5 adds none) —
    edit only if either drifted.
  - `COMMANDS.md`: confirm it is current (Task 6 regenerated it when
    `maxEntities` was added); if untouched since, re-run `uv run loom
    --generate-docs > COMMANDS.md` and confirm no diff.

- [ ] **Step 2: Run the benchmark (local, once)**

```bash
docker compose up -d falkordb
uv run python scripts/gen_bench_graph.py --entities 50000 --relations 100000   # builds tapestry-bench
time uv run loom export-bundle '{"graph":"tapestry-bench"}' > /tmp/bench.json   # assembly time
ls -la /tmp/bench.json                                                          # bundle size
# SPA: render /tmp/bench.json into the template (as e2e does), open it, and time
# initial render + sample interaction fps via Playwright tracing or a rAF fps
# meter in the devtools console. Record the numbers — do NOT add an assertion.
```

**Methodology.** Measured on the dev machine against a `tapestry-bench` graph
built by `scripts/gen_bench_graph.py --entities 50000 --relations 100000`
(never committed, never built in CI):

- **Generator**: 50,000 entities in 92.3 s + 100,000 relations in 645.4 s =
  737.7 s total — dominated by relation writes (each relation write is its
  own graph mutation plus an event-log append; entity creation is
  comparatively cheap).
- **Bundle assembly**: `time uv run loom export-bundle '{"graph":
  "tapestry-bench"}'` for the analytics-on row; the same call with
  `{"include": {"analytics": false}}` for the analytics-off row. The
  analytics-on number reflects the Task 6 guardrails firing as designed —
  betweenness omitted above `BETWEENNESS_MAX_NODES` (5,000 nodes) and loop
  enumeration skipped above `LOOP_MAX_NODES` (10,000 nodes), so only
  `degree` + `pagerank` ship in `centrality` and `loops` ships `[]`.
- **`visualize` HTML**: the same graph through `loom visualize` — build took
  23.3 s, producing a ~39.4 MB self-contained HTML file (the ~46.8 MB bundle
  JSON inlined into the template).
- **SPA initial render**: parse → `buildGraph` → first paint, timed via
  Playwright against the built 50k HTML file.
- **Interaction FPS**: sampled over 240 frames / 2002 ms with the layout
  frozen (post-settle) — zero stalls, zero console errors during the run.
- **Chronicle virtualization**: `ROW_HEIGHT` 46px, `OVERSCAN` 8, threshold
  `> 200` events (`VIRTUALIZE_THRESHOLD`); a 1,400-event synthetic timeline
  mounted 23–31 DOM rows at any scroll position — never the full list.
- **Frontend scale thresholds exercised**: Barnes-Hut + reduced
  sync-fallback iteration at `graph.order > 3000` (`barnesHutTheta` 0.6);
  label level-of-detail at `graph.order > 2000`
  (`labelRenderedSizeThreshold` 14).

Fill this table (replace the dashes with measured values; targets are guidance,
not gates):

| Metric | Target | Measured |
|---|---|---|
| Generator: 50k entities + 100k relations | — | 92.3 s + 645.4 s = 737.7 s total |
| Bundle assembly, 50k/100k, analytics on (guardrails active) | < ~15 s | 23.7 s |
| Bundle assembly, 50k/100k, `{"include":{"analytics":false}}` | < ~5 s | 6.6 s |
| Bundle size (JSON, analytics on) | — | ~46.8 MB |
| `visualize` HTML build / size | — | 23.3 s / ~39.4 MB |
| `betweenness` present in centrality at 50k | omitted (> 5k nodes) | omitted — `degree` + `pagerank` only |
| `loops` count at 50k | skipped (> 10k nodes) → `[]` | skipped → `[]` |
| SPA initial render (parse → buildGraph → first paint) | < ~5 s | 31.75 s |
| Interaction FPS, layout frozen | > 30 fps | 120 fps (240 frames / 2002 ms, zero stalls), zero console errors |
| Chronicle event list, 1,400 rows: scroll stays responsive | yes (virtualized) | yes — 23–31 DOM rows mounted (never all 1,400) |

**Honest caveats.** Two numbers miss their aspirational targets, and the
guardrails still did their job:

- **Assembly-on, 23.7 s vs. < ~15 s target** — dominated by centrality at
  50k nodes. Betweenness is correctly gated off above 5k nodes, but
  `degree` and `pagerank` still run in full, and pagerank's iterative solve
  over 100k edges is the largest remaining cost once betweenness is out of
  the picture.
- **SPA first paint, 31.75 s vs. < ~5 s target** — dominated by parsing a
  ~39 MB inline JSON bundle on the main thread, plus a Louvain clustering
  pass inside `buildGraph`; both scale with node/edge count in a
  single-file, no-server artifact that inlines its entire dataset.

Neither is a regression to fix before shipping — the frontend renders and
stays interactive at 120 fps once loaded, and the honest, already-shipped
fast-load path at this scale is the `maxEntities` cap from Task 6: a
top-degree core with `meta.truncated` metadata trades completeness for a
bundle that assembles and parses quickly and deterministically, at any
graph size. Per CLAUDE.md, no wall-clock assertion enters CI or the test
suite — these are recorded numbers, not gates.

If a measured number badly misses its target, tune the Task 6/7 thresholds
(betweenness/loop node caps, label LOD, Barnes-Hut theta) and re-record — but do
**not** add a timing assertion to CI or the test suite.

- [ ] **Step 3: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass (docs-only + this plan). Then:

```bash
git add README.md docs/superpowers/plans/2026-07-11-tapestry-phase-5.md
# add CLAUDE.md and/or COMMANDS.md ONLY if you actually edited them
git commit -m "Finalize Tapestry docs and record the scale benchmark" -- README.md docs/superpowers/plans/2026-07-11-tapestry-phase-5.md
```

---

## Plan self-review notes

- **This closes the roadmap.** Phase 5 is the last of the five phases Phase 1
  defined. After it, the spec's `## Phasing` list is fully delivered and only its
  `## Out of scope` items (collaboration, write-back, a dedicated document-corpus
  view, 3D) remain unbuilt by design.

- **Spec sweep (every remaining spec line reconciled).**
  - *Cross-cutting → Shareable state* ("named saved views persist in
    localStorage") — Phase 1 shipped save/list/delete/apply; **T1** completes the
    management surface (rename, export/import JSON, `#view=` apply-on-load).
  - *Cross-cutting → Design system* ("WCAG-AA contrast", "deliberate typography")
    and *Graph Explorer* ("full keyboard navigation") — **T2** (composite-widget
    keyboard + focus + aria-live), **T3** (help overlay + canvas keyboard
    alternative), **T4** (axe-core audit, zero serious/critical, both themes)
    close the accessibility bar the spec implies but never fully specified.
  - *Cross-cutting → Exports* ("every view exports PNG and SVG of exactly what is
    on screen (WYSIWYG)") — **T5** extends export from Explorer-only to all five
    views, adds legends, theme background, and dated filenames. **Deliberately
    scoped down:** the spec's "current selection exports as JSON, GraphML, and
    DOT" is **deferred** — it is a *selection*-serialization feature, not a view
    snapshot, with limited demand for a read-only visualizer, and the bundle
    itself is already JSON (`export-bundle`); building three graph-interchange
    serializers is disproportionate for the final polish pass. Flagged here as the
    one intentional export gap. DOM-overlay decorations are a stated PNG
    limitation (documented in the export title text).
  - *Cross-cutting → Performance* / *Graph Explorer* ("Target: 50k nodes
    interactive", "above 50k nodes, progressive loading seeded by top-centrality
    nodes") — **T6** (Python `maxEntities` cap seeded by top-degree core +
    `truncated` metadata; betweenness/loop guardrails) and **T7** (Barnes-Hut,
    label LOD, hover suppression, event virtualization) plus **T8** (measured
    numbers). **Scoped honestly:** the *live* expand-on-demand through
    `/api/neighbors` (Phase 4 shipped the endpoint) is wired minimally via the cap
    + `truncated` signal rather than a full progressive-load UI; a top-degree
    truncation with an explicit "showing top N of M" contract is the pragmatic,
    testable form of "seeded by top-centrality nodes" for a read-only surface.
  - *Systems → influence propagation* and *Chronicle → event-lane chart* were
    listed as optional in the spec and **deferred by the Phase 2 self-review**;
    they are **not** reintroduced here. Rationale: both are net-new *view features*,
    not polish, and this final phase is scoped to polish (management, a11y, export,
    scale) per the Phase 1 roadmap. They remain candidates for a future,
    separately-specced enhancement — noted, not silently dropped.

- **The one data-contract change is additive and safe.** `TapestryMeta.truncated`
  is optional; `SCHEMA_VERSION` stays `1`; `exclude_none=True` keeps it out of
  every bundle where the cap did not fire (so the fixture, the dev bundle, and
  every existing test are byte-identical). The drift test and the ajv fixture test
  both stay green after regenerating `bundle.schema.json` and mirroring the field
  into `data.ts`. `maxEntities` is an *input* field, so it touches `COMMANDS.md`
  but not the wire schema.

- **Guardrails are inert at fixture scale.** Every threshold
  (`BETWEENNESS_MAX_NODES=5_000`, `LOOP_MAX_NODES=10_000`, `LOOP_MAX_SIZE=12`,
  `CENTRALITY_SHIP_LIMIT=1_000`, the layout/label `> 3000` gate) is far above the
  10-entity fixture, so no existing test changes behaviour. Tests exercise the
  guardrails with a *small* graph and a *low* threshold/cap (injectable
  constants), never by building 50k inside `pytest` — that stays in the local-only
  `scripts/gen_bench_graph.py` and Task 8's manual run.

- **Reuse, not reinvention.** T1 reuses `applyHash`/`serializeState`/`downloadBlob`;
  T2 extracts one pure roving helper and otherwise edits existing header markup;
  T5 extends the shared `exportSvg.ts` and reuses `graphToSvg`/`exportPngFile`
  across the Sigma views; T6 reuses `analyze_centrality`'s `limit` and
  `detect_loops`'s `maxSize` rather than new algorithms; T7 uses graphology's own
  Barnes-Hut and sigma's own `labelRenderedSizeThreshold`. No new store, command,
  runtime dependency, or architectural surface.

- **CI shape unchanged.** No new job. The `tapestry` job already runs
  `npm run e2e` over all of `e2e/**`, so the new saved-view, keyboard, help,
  a11y, and export specs run there; `@axe-core/playwright` is a devDependency
  `npm ci` installs. The base `ci` job (with `--extra viz-serve`) is untouched.
  The benchmark is local-only and enters no gate.

- **Risks flagged for implementers.**
  1. **Schema drift is exact-equality.** After adding `truncated`, you *must*
     `uv run python -m theloom.viz.schema` to rewrite `tapestry/schema/bundle.schema.json`
     and mirror the field into `data.ts`, or `test_viz_schema_drift.py` and/or
     `schema.test.ts` fail. Regenerate; do not hand-edit the JSON.
  2. **Label LOD can hide fixture labels.** `labelRenderedSizeThreshold` set too
     high blanks the small fixture's labels and breaks the existing e2e (which
     asserts on rendered content). Gate the threshold on `graph.order > N` or pick
     a value the fixture's node sizes clear; verify against `npm run e2e`.
  3. **Event-list virtualization vs. the existing Chronicle spec.** `smoke.spec.ts`
     asserts exact `.events__row` counts and a single `--current` row. Keep the
     fixture's full list within one window (all rows rendered at fixture scale) so
     the spec is untouched; only touch the spec if you deliberately change the DOM
     shape, and then update it consciously.
  4. **axe findings may be real design debt.** Some `serious` findings (token
     contrast, an unlabelled canvas) require a genuine fix (adjust a token to reach
     AA, add an accessible name), not a rule suppression. Fix the source; only
     `.exclude()` a specific node with a written justification when a rule is a
     documented false positive for a WebGL canvas that the panel already
     describes.
  5. **`maxEntities` truncation must stay reproducible.** Use a stable tiebreak
     (id) when selecting the top-degree core so two runs of the same graph produce
     the same bundle (deterministic screenshots, stable deep links) — mirror the
     `initialPosition`/`hashSeed` determinism the frontend already relies on.
  6. **The `?` key and typing targets.** `useKeyboard` ignores keydowns whose
     target is an `INPUT`/`TEXTAREA`/`SELECT`/`contenteditable`, so `?` typed into
     the search box will *not* open help — that is correct. But confirm the help
     overlay's own focus trap does not swallow `?`/`Escape` from a control inside
     it; the trap handles `Tab`/`Escape` explicitly and leaves other keys alone.
  7. **Benchmark generator signatures.** Verify `create_entity`/`create_relation`
     (or `bulk_import`) argument shapes against `theloom/store/falkor.py` /
     `theloom/operations/bulk.py` before running the 50k build; construct models
     via `model_validate` with camelCase keys, `"observations": []`, and explicit
     `polarity` (`null` for `related_to`), per the Global Constraints. Batch under
     `bulk_import`'s `MAX_ENTITIES_LIMIT`. Target `tapestry-bench` only — never
     the default graph.
  8. **Concurrent-session hygiene.** Every commit uses an explicit pathspec; never
     `git add -A` or stage a file a task did not change (a concurrent session may
     hold uncommitted Python WIP in `tests/conftest.py`, `theloom/store/falkor.py`,
     `theloom/operations/bulk.py`, and others — the benchmark generator *reads*
     those signatures but commits only `scripts/gen_bench_graph.py` and the files
     its task owns).
