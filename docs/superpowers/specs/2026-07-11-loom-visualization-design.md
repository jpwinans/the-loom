# Tapestry — Visualization for The Loom

**Date:** 2026-07-11
**Status:** Approved design, pending implementation plan

## Purpose

Give The Loom a first-class visualization surface: visually stunning, clean,
professional, and strictly more useful and fully featured than graphify
(`~/Development/AI/graphify`). The Loom has no visualization code today, yet its
data is unusually visualizable — typed entities and relations with polarity and
strength, epistemic metadata (confidence, provenance, status), bi-temporal
history, feedback loops, leverage points, semantic embeddings, and composite
analytics. Tapestry renders all of it in one unified application.

## Decisions (settled during brainstorming)

1. **Delivery: phased static-then-server.** First, `loom visualize` generates
   a self-contained interactive HTML file (offline, shareable). Later (phase 4),
   `loom serve` serves the identical app in live mode against the store. Both
   share one frontend codebase.
2. **Toolchain: Vite + TypeScript SPA.** A `tapestry/` frontend workspace. The
   built bundle is committed into the Python package so end users never need
   Node; only frontend contributors do.
3. **Shape: unified workbench (Approach A).** One SPA with five linked views
   sharing selection, filter, and theme state — rejecting graphify's
   fragmented per-artifact model (Approach B) and the FalkorDB-browser-only
   minimal option (Approach C).

## Architecture

```
theloom/viz/                      new Python subpackage
  __init__.py
  bundle.py                         assembles TapestryBundle from existing operations
  html.py                           injects bundle JSON into the built SPA template
  static/tapestry.html              built single-file SPA (committed artifact)
tapestry/                         new frontend workspace (Vite + TypeScript)
  src/views/explorer/               Graph Explorer (sigma.js v3 WebGL + graphology)
  src/views/systems/                causal-loop diagram view
  src/views/chronicle/              bi-temporal time travel
  src/views/semantic/               embedding projection map
  src/views/overview/               dashboard
  src/state/                        shared app state (selection, filters, theme, time)
  src/design/                       design tokens, themes, typography
  src/lib/                          data-source interface, bundle types, replay engine
```

Data flow is one-way: existing operations (`extract-subgraph`,
`analyze-centrality`, `detect-loops`, `list-leverage-points`,
`detect-components`, `find-clusters`, the event log, embedding vectors) →
`theloom/viz/bundle.py` assembles a versioned **TapestryBundle** → either
injected into a self-contained HTML file (static mode) or served over REST
(live mode). The SPA reads from an inlined
`<script type="application/json" id="tapestry-data">` block or from `/api/*`,
behind a single data-source interface, so views are mode-agnostic.

The frontend builds via `vite-plugin-singlefile` into **one HTML template with
zero CDN dependencies** (graphify's HTML requires internet for vis-network/D3/
Mermaid; Tapestry works fully offline). The built template (~1 MB before data)
is committed at `theloom/viz/static/tapestry.html` and shipped as package data
via hatchling. CI rebuilds the frontend and fails if the committed artifact
drifts from source.

### Architecture-invariant compliance

- **One transactional store.** Tapestry adds no store. `bundle.py` reads only
  through existing operations/composites. The later server is read-only.
- **Event-sourced and bi-temporal.** The Chronicle view is powered by the
  existing `tx_from`/`tx_to` intervals and event log — replayed client-side.
- **Domain model is the source of truth.** Bundle entities/relations are the
  model's wire docs (`model_dump(by_alias=True)`); no hand-serialization.
- **Typed error codes.** See Error handling.
- **One config path.** `serve` resolves configuration through
  `theloom/config.py` like every other command.
- **Registry is the source of the CLI.** New commands are `CommandDescriptor`s
  in `theloom/cli/registry.py` under a new **Visualization** category;
  `COMMANDS.md` is regenerated, never hand-edited.
- **JSON-in/JSON-out preserved.** HTML is written to a file; the command
  returns a JSON envelope (`{path, ...}`) on stdout.

## Data contract — TapestryBundle

Versioned schema (`schemaVersion: 1`). Pydantic-modeled in
`theloom/viz/bundle.py`; TypeScript-typed in `tapestry/src/lib/bundle.ts`. A
generated JSON Schema plus a drift test keeps the two in sync.

| Section | Contents | Optional |
|---|---|---|
| `meta` | graph name, scope description, generatedAt, asOf, counts, title, schemaVersion | no |
| `entities[]` | full wire entity docs (id, name, entityType, observations, confidence, status, provenance, memory fields, embedding status) | no |
| `relations[]` | full wire relation docs (from, to, relationType, polarity, strength, confidence, provenance) | no |
| `analytics` | centrality (degree/betweenness/pagerank), components, bridges, loops (reinforcing/balancing classification, members, edges), leverage points (Meadows level). Communities are computed client-side (graphology Louvain) where they also drive coloring — not shipped in the bundle. | yes |
| `temporal` | per-record `txFrom`/`txTo` intervals + scoped event log — enables client-side time replay with no server | yes |
| `semantic` | 2D projection coordinates per entity (UMAP; PCA fallback), cluster assignments with labels | yes |
| `documents` | chunk-provenance links for source entities (detail-panel use) | yes |

Optional sections are governed by the `include` input flags so a minimal bundle
stays small. Non-finite floats follow the existing `_jsonify` convention
(coerced to null).

## CLI surface

New registry category **Visualization**, two commands in Phase 1:

### `visualize`

Input model (wire names camelCase):

```json
{
  "graph": "default",
  "scope": { "mode": "full | ego | causal | typed | search",
             "center": "<entity id, for ego>", "depth": 2,
             "entityTypes": [], "relationTypes": [], "query": "<for search>" },
  "asOf": "2026-07-01T00:00:00Z",
  "include": { "analytics": true, "temporal": true, "semantic": true, "documents": false },
  "output": "loom-viz/default.html",
  "title": "My graph",
  "theme": "auto | dark | light"
}
```

All fields optional; defaults: full scope of the default graph, analytics on,
temporal on, semantic on when embeddings exist, output under `./loom-viz/`.

Output: `{ "path": "...", "entityCount": n, "relationCount": n, "bytes": n,
"sections": ["analytics", "temporal", "semantic"] }`.

### `export-bundle`

Same input minus `output`/`title`/`theme`; emits the TapestryBundle JSON to
stdout. Pipelines, tests, and the phase-4 server all share this assembler.

### `serve` (phase 4)

Optional dependency group `viz-serve` (FastAPI + uvicorn). Read-only REST:
`/api/bundle`, `/api/graphs`, `/api/neighbors` (expand-on-demand),
`/api/search` (semantic + keyword), `/api/as-of`, `/api/entity/{id}`. Serves
the same SPA with the live data source active. Input: `{graph?, host?, port?}`.

## The five views

### Graph Explorer (core)

- **Rendering:** sigma.js v3 (WebGL) + graphology. Target: 50k nodes
  interactive (10x graphify's 5k canvas ceiling).
- **Layouts:** ForceAtlas2 in a Web Worker with live physics controls
  (gravity, repulsion, re-heat); DAG/layered for causal scopes; radial;
  circular.
- **Encoding from Loom semantics:** node color by entity type (curated
  19-type palette), switchable to confidence, community, domain, or recency;
  node size by chosen centrality; edge hue by relation family (epistemic
  `supports`/`contradicts` visually distinct), causal arrows with +/− polarity
  glyphs, width by strength, dashed for low confidence; status rendered
  (superseded/deprecated/retracted dimmed or badged).
- **Interactions:** hover neighbor-highlight; click → detail panel
  (observations, confidence gauge, provenance chain, version history, chunk
  sources); **path mode** — pick two nodes, shortest/all paths highlighted
  with the rest dimmed; filters for entity type, relation type, confidence
  range, status, domain; fuzzy search; minimap; full keyboard navigation
  (`/` search, arrows walk neighbors, `p` path mode, `f` fit, `esc` clear).
- **Expand:** double-click ego-expand in live mode; focus-filter in static
  mode.

### Systems View

Causal subgraph rendered as a causal-loop diagram: feedback-loop list with
R/B (reinforcing/balancing) badges — click a loop to isolate it and animate
signed flow around it; leverage points ranked and marked by Meadows level;
influence propagation — select a node and watch signed influence ripple
downstream (semiring distances). No competitor renders any of this.

### Chronicle

Bi-temporal time travel: a scrubber over transaction time re-renders graph
state as-of-T entirely client-side by replaying `txFrom`/`txTo` intervals;
**diff mode** compares two instants (added / invalidated / changed
color-coded); an event-stream lane chart shows mutation activity by type over
time; per-entity version timelines appear in the detail panel.

### Semantic Map

2D UMAP projection of entity embeddings (computed in `bundle.py`; PCA fallback
when `umap-learn` is not installed) drawn as a canvas scatter with density
contours and labeled cluster hulls (from `find-clusters`); hover shows nearest
semantic neighbors; **lasso-select carries the selection into Graph Explorer**
(cross-view brushing). In live mode, search-by-similarity.

### Overview

`graph-reconnaissance` visualized: stat tiles, entity/relation type
distribution charts, confidence histogram, top-centrality tables with inline
bars, component treemap, health indicators (dangling relations, contradiction
count, stale embeddings), recent-activity sparkline.

## Cross-cutting

- **Design system:** design tokens, dark and light themes, WCAG-AA contrast,
  deliberate typography. Implementation follows the `dataviz` and
  `frontend-design` skills' rules.
- **Exports:** every view exports PNG and SVG of exactly what is on screen
  (WYSIWYG); current selection exports as JSON, GraphML, and DOT.
- **Shareable state:** filters, layout, selection, and time position
  serialize into the URL hash — bookmarkable deep links that work in a static
  file; named saved views persist in localStorage.
- **Performance:** WebGL rendering, label level-of-detail by zoom, layout in
  workers; above 50k nodes, progressive loading seeded by top-centrality nodes
  with expand-on-demand.

## Error handling

| Condition | Code |
|---|---|
| Bad scope/params (e.g. ego without center) | `VALIDATION_ERROR` |
| Missing graph, entity, or ego center | `NOT_FOUND` |
| Store read or file write failure | `OPERATION_ERROR` |
| Built SPA template missing (frontend not built) | `CONFIG_ERROR` with instruction to build |

Never classify errors by substring-matching prose.

## Testing

- **Python:** `run_handler`-level tests against live FalkorDB fixtures
  (existing convention in `tests/test_cli_commands.py` / `tests/conftest.py`):
  bundle schema validity, scope correctness (ego/causal/typed/search), as-of
  snapshots agree with `read_entity_as_of`, HTML emission and byte count,
  every error code. JSON-Schema drift test pins the bundle contract between
  Python and TypeScript.
- **Frontend:** Vitest for state/filter logic and the temporal replay engine
  (property: replay-to-end equals current state); Playwright smoke tests —
  load a fixture bundle, each view renders, path mode and time scrubber work.
- **CI:** builds the frontend, verifies the committed artifact matches, runs
  both suites. `main` stays green: mypy --strict, ruff, pytest.

## Phasing

1. **Foundation + Explorer** — `theloom/viz/` bundle assembler, `visualize` +
   `export-bundle` commands, `tapestry/` workspace with design system, Graph
   Explorer, Overview. Ships a product already better than graphify.
2. **Systems + Chronicle** — the Loom-unique causal and temporal views.
3. **Semantic Map** — projection pipeline + cross-view brushing.
4. **Live mode** — `serve` command (optional `viz-serve` extra), live
   expand/search/as-of.
5. **Polish** — saved views, full accessibility/keyboard pass, export
   refinements, performance hardening at 50k nodes.

## Competitive scorecard vs graphify

| Capability | graphify | Tapestry |
|---|---|---|
| Interactive node ceiling | 5k (canvas), then detail lost | 50k target (WebGL) + progressive load |
| Path highlighting in UI | none (CLI text only) | path mode with dimming |
| Saved/shareable views | none | URL-hash deep links + localStorage |
| Time travel / diffing | none | bi-temporal scrubber + diff mode |
| WYSIWYG export | no (separate matplotlib SVG) | PNG/SVG of live view |
| Keyboard navigation | explicitly disabled | full |
| Physics/layout controls | none (auto-freeze) | live controls, 4 layouts |
| Filtering | communities only | type/relation/confidence/status/domain |
| Minimap | none | yes |
| Embedding/similarity view | none by design | Semantic Map + brushing |
| Detail panel | type/community/degree | full doc, provenance, versions |
| Artifact unity | 3 disconnected HTML files | one workbench |
| Offline | needs CDN internet | fully self-contained |
| Causal semantics (polarity, loops, leverage) | n/a | first-class Systems View |

## Out of scope

- Collaboration/multi-user annotation.
- Writing back to the graph from the UI (read-only surface).
- A dedicated document-corpus view (chunk provenance appears in the detail
  panel only; a full corpus view can be a future spec).
- 3D rendering.
