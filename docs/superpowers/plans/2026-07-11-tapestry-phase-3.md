# Tapestry Phase 3 Implementation Plan — Semantic Map

> **For agentic workers:** Execute this plan task-by-task, in order. Each task is
> a self-contained unit with a failing test → verify-fail → implement →
> verify-pass → gates → commit cycle. Do not start a task until the previous
> one's gates are green and committed. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Ship the **Semantic Map** — the embedding view the spec promises — plus
the Python pieces that feed it: an optional **UMAP** projection upgrade (PCA stays
the fallback), **cluster assignments** in the bundle's semantic section, a new
**Semantic Map** SPA tab that scatters the projection, draws labeled **cluster
hulls**, and **lasso-brushes** a selection that lands in the Explorer, and a new
**`scope.mode: "search"`** that scopes a bundle to a semantic query — per the
approved spec at `docs/superpowers/specs/2026-07-11-loom-visualization-design.md`
(Semantic Map section and the Data-contract / CLI-surface sections).

**Architecture:** Phase 3 adds no store and no *required* Python runtime
dependency. The projection upgrade is an **optional extra** (`viz-umap` →
`umap-learn`); `assemble_semantic` tries UMAP when it is installed and the vector
count is high enough, else falls back to the existing numpy PCA — `method`
reports which. Cluster assignments reuse the existing **`find-clusters`**
operation (`theloom/operations/semantic.py::find_clusters`) and ride in the
**same `SemanticSection`** Phase 1 already ships (a new optional `clusters`
field). Search scope reuses the existing semantic-search internal
(`_search_similar`) — the exact path `semantic-search`/`find-clusters` use — so
`resolve_scope` gains a `"search"` mode that embeds the query, keeps the matched
entities plus their induced relations, and labels the scope `search:<query>`. On
the frontend, the Semantic Map is a new tab: a Sigma instance with a **fixed
layout** (node positions come straight from `semantic.projection`, so there is no
ForceAtlas2 pass), colored by entity type through the existing token layer,
with an SVG hull overlay and a freehand lasso that writes a `brushedIds` set into
the shared store; the Explorer reads that set as a new highlight layer with a
count chip. Views stay mode-agnostic behind the Phase 1 data-source interface.
CLI stays JSON-out.

**Tech Stack:** Python 3.11+/Pydantic v2/Typer · numpy (PCA, already a dep) ·
**umap-learn (optional, new extra)** · React 18 · TypeScript · zustand ·
sigma.js v3 · graphology · Vitest · Playwright. **The only new dependency is the
optional `viz-umap` extra; the core install and CI are unchanged.**

## Prerequisites (fresh environment)

- `uv sync`, `docker compose up -d falkordb` (tests connect to the live store —
  nothing is mocked; `uv run loom init` if the default graph is new).
- Node.js 22+ and npm for the `tapestry/` workspace; `cd tapestry && npm ci`.
  Playwright chromium: `cd tapestry && npx playwright install chromium`.
- Phases 1 and 2 are fully implemented and committed (viz subpackage, `visualize`
  / `export-bundle`, the SPA with Explorer + Overview + Systems + Chronicle, the
  `asOf` bundle param). Read
  `docs/superpowers/plans/2026-07-11-tapestry-phase-1.md`,
  `docs/superpowers/plans/2026-07-11-tapestry-phase-2.md`, and the approved spec
  before starting.
- The live store already holds a `tapestry-dev` fixture graph (7 entities, of
  which **6 carry embeddings**, 1 balancing loop, 1 leverage point, 1 deprecated
  claim); `tapestry/fixtures/dev-bundle.json` is exported from it and ships
  `semantic.method === "pca"` with a 6-entity `projection`. Task 4 enriches it so
  at least one semantic cluster forms, then re-exports.
- Optional, for the UMAP path only: `uv sync --extra viz-umap` (installs
  `umap-learn`; heavy — numba/llvmlite/scikit-learn). CI does **not** install it,
  so the UMAP-path test is skipped there and the PCA fallback is the CI default.

## Global Constraints

These are load-bearing — every one was learned the hard way in Phases 1–2.

- **Gates every commit.** `uv run mypy --strict theloom && uv run ruff check . &&
  uv run ruff format . && uv run pytest` must pass, plus — whenever `tapestry/`
  is touched — `cd tapestry && npm test && npm run build` and
  `uv run pytest tests/test_cli_viz_commands.py`. Keep `main` green.
- **No pydantic mypy plugin.** Aliased Pydantic fields must be constructed with
  **alias (camelCase) kwargs** or via `model_validate({...camelCase...})`;
  snake_case kwargs on an aliased field fail `uv run mypy --strict theloom`.
  Mirror the existing `bundle.py` pattern exactly (`generatedAt=…`,
  `entityCount=…`, `leveragePoints=…`, `asOf=…`); non-aliased fields (`graph`,
  `centrality`, `method`, `projection`) use their plain name. Wire names are
  camelCase; serialize with `model_dump(by_alias=True, exclude_none=True)`.
- **The viz test suite is model-free by design.** No existing viz test downloads
  the fastembed model — vectors are seeded directly through
  `store.set_entity_vector(id, vector)`, and search/cluster code paths are never
  exercised with a live embedder. Preserve this: any Phase 3 test that reaches
  `find_clusters` or `_search_similar` MUST monkeypatch
  `theloom.operations.semantic.get_embedder` with a deterministic stub (a helper
  is defined in Task 1) so CI stays offline and fast. `get_embedder` is
  `@lru_cache`d, so patch the **module attribute** `theloom.operations.semantic
  .get_embedder`, not the cache.
- **`EntityCreate.model_validate(...)` requires `"observations": []`** in test
  fixtures.
- **`list-entities` filters are top-level aliased fields, not nested.** It is
  `{"graph":"g","entityType":"variable"}` — there is **no** `filter` wrapper
  (`ListEntitiesInput` in `theloom/operations/entity.py:114` carries `entityType`,
  `name`, `includeDeprecated`, … at the top level). Phase 2's fixture task used a
  nested `filter` by mistake; do not copy it.
- **`create-relation` requires an explicit `"polarity": null`** for a non-causal
  relation type; omitting it where the model expects the key fails validation.
- **Leverage-point observations use snake_case `depth_category:`** with values
  `parameters | feedbacks | design | intent`
  (`theloom/graph/metadata.py::parse_leverage_point_observations` matches
  `^depth_category:\s*(parameters|feedbacks|design|intent)$`, case-insensitive).
  `depthCategory:` does not parse — Phase 2's fixture only worked because
  `level: 6` back-derives the category. Phase 3 adds no leverage points, but keep
  this correct if you touch one.
- **Never introduce a literal `__TAPESTRY_BUNDLE__` string in tapestry app
  source.** esbuild constant-folds it; the sentinel must appear exactly once in
  built output. `tapestry/src/lib/data.ts` detects dev-mode by a `JSON.parse`
  failure, not by comparing against the literal — keep it that way. After every
  frontend-touching task, `cd tapestry && npm run build` and confirm
  `grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html` → `1`.
- **`tsc -b` strict project references.** Test-fixture bundles are typed
  `as unknown as TapestryBundleRaw` (never `as never`); casts that get spread use
  `as unknown as T`.
- **vitest 3.x + happy-dom (no WebGL).** Never instantiate Sigma in unit tests —
  test **pure modules only** (`semanticMap.ts`). E2E via Playwright chromium
  (config `testDir: "e2e"`; vitest excludes `e2e/**`).
- **Canvas-side colors resolve CSS vars at runtime and MUST re-resolve on theme
  change.** Reuse the Explorer/Systems rAF + `readVar` pattern and a
  `resolve…Colors` pass in any new Sigma view. Any new hull/lasso tokens live in
  `tokens.css` with both-theme values and concrete fallbacks in the resolving
  module (mirror `buildGraph.ts`'s `TYPE_FALLBACK` and Systems' `readVar`).
- **DOM overlays pair `framedGraphToViewport(getNodeDisplayData(id))`, never
  `graphToViewport`.** This is the correct projection for badges/glyphs/hulls
  laid over the Sigma canvas (verified in Systems' polarity-glyph and leverage
  overlays). `graphToViewport` is only correct for the *camera-origin* projection
  used in the Explorer's arrow-key cone math — do not use it for overlays.
- **Sigma views run FA2 ~2–2.5 s on mount (Systems, Chronicle); the Semantic Map
  does NOT** — it uses a fixed layout from `semantic.projection`, so it mounts
  ready. E2E for FA2 views must `waitForTimeout` before asserting on overlay
  positions; the Semantic Map only needs to wait for the canvas to appear.
- **Playwright `.fill()` throws on `<input type="range">`, and React swallows a
  plain `input.value = …`.** Set range inputs through the native
  `HTMLInputElement.prototype` value setter then dispatch an `input` event — the
  `setSlider` helper already in `e2e/smoke.spec.ts`. Reuse it for any range
  control (the lasso/hull controls are buttons, so this only matters if you add a
  slider).
- **One commit per task**, including the rebuilt `theloom/viz/static/tapestry.html`
  whenever the frontend changed. Commit messages are plain imperative — never
  mention AI/Claude, never add co-author trailers.
- **Python tests hit live FalkorDB** via the `db` / `redis_client` / `namespace`
  fixtures in `tests/conftest.py` (`MultiGraph(db, redis_client,
  default_graph="default", key_prefix=namespace)`); CLI-level tests go through
  `run_handler(name, input, multi)`.
- **The bundle schema is pinned.** If a Pydantic model in `theloom/viz/schema.py`
  changes, regenerate the committed JSON Schema with
  `uv run python -m theloom.viz.schema`; `tests/test_viz_schema_drift.py` pins
  `tapestry/schema/bundle.schema.json` against the model, and
  `tapestry/src/lib/schema.test.ts` validates the fixture against that schema.
  `SemanticSection` sets `additionalProperties: false`, so a new field is invalid
  in the fixture until the schema is regenerated.
- **`SCHEMA_VERSION` stays `1`.** Additive, optional fields (`clusters`, like
  Phase 2's `asOf`) do not bump the version; the frontend `TapestryBundleRaw`
  treats them as optional.
- **UI tasks (Tasks 6, 7): load the `dataviz` and `frontend-design` skills BEFORE
  writing any styles or chart/encoding code.** Cluster hulls, the lasso stroke,
  and the brush chip are new visual channels — hull fills must not collide with
  the 19 entity-type hues or the status/polarity channels, must read in both
  themes, and are never color-alone (each hull carries a label; the brush chip
  states its count).

## File Structure (Phase 3 additions)

```
pyproject.toml                        + [project.optional-dependencies] viz-umap  (Task 2)
                                      + umap.* mypy override                      (Task 2)
theloom/viz/schema.py                 + SemanticCluster, SemanticSection.clusters (Task 1)
theloom/viz/semantic.py               + cluster assembly (Task 1), UMAP path (Task 2)
theloom/viz/scope.py                  + "search" mode, ScopeInput.query           (Task 3)
tapestry/schema/bundle.schema.json    regenerated (clusters added)                (Task 1)
tests/test_viz_semantic.py            + stub embedder, cluster + UMAP tests    (Tasks 1,2)
tests/test_viz_scope.py               + search-mode tests                         (Task 3)
tests/test_cli_viz_commands.py        + search-scope CLI test                     (Task 3)
tapestry/fixtures/dev-bundle.json     re-exported (clusters + more projection)    (Task 4)

tapestry/src/lib/data.ts              + semantic.clusters on the raw type         (Task 5)
tapestry/src/views/semantic/semanticMap.ts        buildScatter/hull/lasso (pure)  (Task 5)
tapestry/src/views/semantic/semanticMap.test.ts                                   (Task 5)
tapestry/src/views/semantic/SemanticMap.tsx        Sigma scatter + hull + lasso   (Task 6)
tapestry/src/views/semantic/SemanticMap.css                                       (Task 6)
tapestry/src/state/store.ts           + "semantic" view, brushedIds/setBrushed  (Tasks 6,7)
tapestry/src/App.tsx                  + Semantic tab + routing                     (Task 6)
tapestry/src/design/tokens.css        + --hull-* / --brush tokens (both themes)    (Task 6)
tapestry/src/views/explorer/Explorer.tsx   + brush highlight layer + count chip   (Task 7)
tapestry/src/views/explorer/Explorer.css   + brush chip styles                    (Task 7)
tapestry/e2e/smoke.spec.ts            + Semantic Map coverage; Chronicle recount   (Task 8)
README.md                             Visualization section: Semantic Map + search (Task 8)
```

## Phase roadmap (remaining plans, one document each)

- **Phase 4 — Live mode:** `serve` command (optional `viz-serve` group: FastAPI +
  uvicorn), REST endpoints `/api/bundle|graphs|neighbors|search|as-of|entity/{id}`,
  live data source in the SPA, live ego-expand and search-by-similarity.
- **Phase 5 — Polish:** saved-view management UI, full a11y/keyboard audit, export
  refinements, 50k-node performance hardening (label LOD, progressive load seeded
  by top-centrality).

---

### Task 1: Cluster assignments in the semantic section

Add cluster membership to the bundle's `SemanticSection`, sourced from the
existing `find-clusters` operation, and introduce the deterministic stub embedder
the model-free tests use. `assemble_semantic` already fetches every stored vector
to project; it now also runs `find_clusters` over the same graph and maps its
output onto a small wire shape the SPA draws hulls from.

**Files:**
- Modify: `theloom/viz/schema.py` (`SemanticCluster`, `SemanticSection.clusters`)
- Modify: `theloom/viz/semantic.py` (`_assemble_clusters`, wire into `assemble_semantic`)
- Regenerate: `tapestry/schema/bundle.schema.json`
- Modify: `tests/test_viz_semantic.py` (stub embedder + cluster tests)

**Interfaces (verified in the Phase 1/2 code):**
- `find_clusters(params: FindClustersInput, multi: MultiGraph) -> dict[str, Any]`
  (`theloom/operations/semantic.py:781`). `FindClustersInput` fields (all
  optional): `similarityThreshold`, `minClusterSize`, `entityType`, `maxEntities`,
  `graph`. Returns `{"clusters": [{"id": int, "entities": [{"id","name",
  "entityType"}], "size": int, "avgSimilarity": float}], "sampled", ...}`. It
  clusters by union-find over `_search_similar` similarity (default threshold
  0.7 on the `1/(1+L2)` score, `minClusterSize` 2) — so it embeds each entity's
  text as a query through `get_embedder()`.
- `assemble_semantic(graph: str | None, multi: MultiGraph) -> SemanticSection |
  None` (`theloom/viz/semantic.py:15`) — returns `None` below `_MIN_VECTORS` (3).
- Produces:
  - `class SemanticCluster(LoomModel)` — `id: int`, `label: str`, `entity_ids:
    list[str]` (alias `entityIds`), `size: int`.
  - `SemanticSection.clusters: list[SemanticCluster] | None = None`.
  - `_assemble_clusters(graph, multi) -> list[SemanticCluster] | None` — `None`
    when no cluster meets `minClusterSize`; label is the dominant `entityType`
    among a cluster's members (deterministic tie-break by first appearance).

- [ ] **Step 1: Write the failing test**

Replace `tests/test_viz_semantic.py` with the version below (keeps the two Phase 1
tests, adds a stub-embedder helper and cluster coverage). The stub maps each
`find_clusters` query text back to that entity's seeded vector, so clustering is a
pure, deterministic function of the seeded geometry — no model, no network.

```python
"""Semantic projection + cluster tests using synthetic vectors (no fastembed).

find_clusters embeds each entity's text as a query; a deterministic stub maps
that query text back to the entity's seeded vector, so cluster membership is a
pure function of the seeded geometry — CI never downloads the model."""

from __future__ import annotations

from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.semantic import assemble_semantic


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


class _StubEmbedder:
    """Returns the seeded vector for a query, keyed on the entity's name (the
    first token of `_query_text` = f"{name} {observations}")."""

    def __init__(self, by_name: dict[str, list[float]]) -> None:
        self._by_name = by_name

    def embed_query(self, text: str) -> list[float]:
        return self._by_name[text.split()[0]]


def _seed(multi: MultiGraph, vectors: dict[str, list[float]]) -> dict[str, str]:
    store = multi.get_store()
    ids: dict[str, str] = {}
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate(
                {"name": name, "entityType": "concept", "observations": []}
            )
        )
        store.set_entity_vector(entity.id, vector)
        ids[name] = entity.id
    return ids


def _install_stub(monkeypatch: pytest.MonkeyPatch, vectors: dict[str, list[float]]) -> None:
    stub = _StubEmbedder(vectors)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: stub)


def test_none_when_too_few_vectors(multi: MultiGraph) -> None:
    assert assemble_semantic(None, multi) is None


def test_pca_projection_shape_and_no_clusters_when_dissimilar(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    vectors = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": [0.0, 1.0, 0.0, 0.0],
        "c": [0.0, 0.0, 1.0, 0.0],
        "d": [1.0, 1.0, 0.0, 0.0],
    }
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "pca"  # 4 vectors < UMAP threshold
    assert len(section.projection) == 4
    assert all(len(point) == 2 for point in section.projection.values())
    assert section.clusters is None  # nothing is similar enough to group


def test_clusters_from_similar_vectors(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two tight pairs, mutually orthogonal: {a,b} and {c,d}.
    vectors = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": [1.0, 0.0, 0.0, 0.0],
        "c": [0.0, 1.0, 0.0, 0.0],
        "d": [0.0, 1.0, 0.0, 0.0],
    }
    _install_stub(monkeypatch, vectors)
    ids = _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.clusters is not None
    grouped = {frozenset(cluster.entity_ids) for cluster in section.clusters}
    assert grouped == {
        frozenset({ids["a"], ids["b"]}),
        frozenset({ids["c"], ids["d"]}),
    }
    for cluster in section.clusters:
        assert cluster.size == 2
        assert cluster.label == "concept"  # dominant entity type
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_semantic.py -v`
Expected: FAIL — `SemanticSection` has no `clusters`; `_StubEmbedder` path not
exercised because `assemble_semantic` does not call `find_clusters` yet.

- [ ] **Step 3: Write the implementation**

`theloom/viz/schema.py` — add the cluster model and field:

```python
class SemanticCluster(LoomModel):
    id: int
    label: str
    entity_ids: list[str] = Field(alias="entityIds")
    size: int


class SemanticSection(LoomModel):
    method: str
    projection: dict[str, list[float]]
    clusters: list[SemanticCluster] | None = None
```

`theloom/viz/semantic.py` — add cluster assembly and wire it in (keep the existing
PCA projection intact for now; Task 2 adds the UMAP branch):

```python
"""Semantic section — 2D projection of entity embedding vectors plus semantic
clusters.

The projection is numpy PCA (UMAP is an optional upgrade, Task 2). Clusters reuse
the existing find-clusters operation over the same graph, so the map's hulls match
what `loom find-clusters` reports."""

from __future__ import annotations

from collections import Counter

import numpy as np

from theloom.operations.semantic import FindClustersInput, find_clusters
from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import SemanticCluster, SemanticSection

_MIN_VECTORS = 3


def _assemble_clusters(graph: str | None, multi: MultiGraph) -> list[SemanticCluster] | None:
    result = find_clusters(FindClustersInput(graph=graph), multi)
    clusters: list[SemanticCluster] = []
    for cluster in result["clusters"]:
        members = cluster["entities"]
        label = Counter(m["entityType"] for m in members).most_common(1)[0][0]
        clusters.append(
            SemanticCluster(
                id=int(cluster["id"]),
                label=label,
                entityIds=[m["id"] for m in members],
                size=int(cluster["size"]),
            )
        )
    return clusters or None


def assemble_semantic(graph: str | None, multi: MultiGraph) -> SemanticSection | None:
    vectors = multi.get_store(graph).get_entity_vectors()
    if len(vectors) < _MIN_VECTORS:
        return None
    ids = list(vectors.keys())
    matrix = np.array([vectors[entity_id] for entity_id in ids], dtype=np.float64)
    centered = matrix - matrix.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    coords = centered @ vt[:2].T
    projection = {
        entity_id: [round(float(x), 4), round(float(y), 4)]
        for entity_id, (x, y) in zip(ids, coords, strict=True)
    }
    clusters = _assemble_clusters(graph, multi)
    return SemanticSection(method="pca", projection=projection, clusters=clusters)
```

Note: `find_clusters` / `FindClustersInput` live in `theloom.operations.semantic`;
importing them here is a legitimate reuse of the canonical clustering op (per
CLAUDE.md invariant 6). Because `assemble_semantic` returns `None` below three
vectors, the existing bundle/CLI tests (which seed no vectors) never reach
`find_clusters`, so they stay model-free with no change.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_semantic.py -v`
Expected: 4 passed.

- [ ] **Step 5: Regenerate schema, gates, commit**

```bash
uv run python -m theloom.viz.schema        # rewrites tapestry/schema/bundle.schema.json
uv run pytest tests/test_viz_schema_drift.py -v
```

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass. (`tapestry/src/lib/schema.test.ts` still passes — the shipped
fixture has no `clusters`, and `clusters` is optional in the regenerated schema.)

```bash
git add theloom/viz/schema.py theloom/viz/semantic.py tapestry/schema/bundle.schema.json tests/test_viz_semantic.py
git commit -m "Add semantic clusters to the visualization bundle from find-clusters"
```

---

### Task 2: Optional UMAP projection (`viz-umap` extra)

Upgrade the projection to UMAP when the optional `umap-learn` package is installed
and the graph has enough vectors; otherwise keep PCA. `method` reports which was
used, and the UMAP path is deterministically seeded so screenshots and diffs are
stable.

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies] viz-umap`; `umap.*`
  mypy override)
- Modify: `theloom/viz/semantic.py` (method selection)
- Modify: `tests/test_viz_semantic.py` (UMAP-path tests, skipped when absent)

**Interfaces:**
- `umap.UMAP(n_components=2, n_neighbors=…, min_dist=…, metric="cosine",
  random_state=…)` with `.fit_transform(matrix) -> ndarray[n, 2]`. `random_state`
  forces single-threaded execution, which is what makes UMAP reproducible.
- Produces:
  - `_pca_project(matrix: np.ndarray) -> np.ndarray` — the existing SVD projection,
    factored out.
  - `_umap_project(matrix: np.ndarray) -> np.ndarray | None` — `None` when
    `umap-learn` is not importable; otherwise a seeded 2D embedding.
  - `assemble_semantic` picks UMAP when `len(vectors) >= _UMAP_MIN_VECTORS` **and**
    `_umap_project` returns coordinates, else PCA; `method` is `"umap"` / `"pca"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_viz_semantic.py`:

```python
def _seeded_vectors(count: int, dims: int = 16) -> dict[str, list[float]]:
    rng = np.random.default_rng(7)  # deterministic synthetic embeddings
    return {f"e{i}": rng.standard_normal(dims).tolist() for i in range(count)}


def test_pca_below_umap_threshold(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    vectors = _seeded_vectors(6)
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "pca"  # 6 < _UMAP_MIN_VECTORS, PCA even if umap installed


def test_umap_when_available(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("umap")  # skipped in CI (viz-umap not installed)
    vectors = _seeded_vectors(12)
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "umap"
    assert len(section.projection) == 12
    assert all(len(point) == 2 for point in section.projection.values())


def test_umap_is_deterministic(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("umap")
    vectors = _seeded_vectors(12)
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    first = assemble_semantic(None, multi)
    second = assemble_semantic(None, multi)
    assert first is not None and second is not None
    assert first.projection == second.projection
```

`import numpy as np` is already needed — add it to the test module's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_semantic.py -v`
Expected: `test_pca_below_umap_threshold` FAILS (no threshold yet — with 6
vectors PCA already returns, so this actually passes; the real driver is the
UMAP tests, which are **skipped** until `umap-learn` is installed). To see the
UMAP path fail meaningfully, run once inside `uv run --extra viz-umap pytest …`
after Step 3's pyproject change; before the implementation it errors because
`_umap_project` / method selection do not exist.

- [ ] **Step 3: Write the implementation**

`pyproject.toml` — add an optional extra (this is a user-facing feature toggle, so
it is a `[project.optional-dependencies]` extra, installable via
`uv sync --extra viz-umap` / `pip install 'theloom[viz-umap]'`, not a
`[dependency-groups]` dev group):

```toml
[project.optional-dependencies]
viz-umap = ["umap-learn>=0.5"]  # optional UMAP projection for the Semantic Map (PCA is the default)
```

and add `umap` to the mypy missing-imports override (umap-learn ships no type
stubs, and is absent in CI, so this suppresses both `import-untyped` and
`import-not-found`):

```toml
[[tool.mypy.overrides]]
module = ["falkordb.*", "sympy.*", "constraint.*", "z3.*", "tree_sitter_typescript.*", "umap.*"]
ignore_missing_imports = true
```

`theloom/viz/semantic.py` — factor PCA out, add the guarded UMAP branch, and
select:

```python
_MIN_VECTORS = 3
_UMAP_MIN_VECTORS = 10  # UMAP needs a non-trivial neighbourhood; below this PCA is more faithful
_UMAP_SEED = 42


def _pca_project(matrix: np.ndarray) -> np.ndarray:
    centered = matrix - matrix.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    return np.asarray(centered @ vt[:2].T, dtype=np.float64)


def _umap_project(matrix: np.ndarray) -> np.ndarray | None:
    """Seeded 2D UMAP, or None when umap-learn is not installed."""
    try:
        import umap
    except ImportError:
        return None
    n = matrix.shape[0]
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=min(15, n - 1),
        min_dist=0.1,
        metric="cosine",
        random_state=_UMAP_SEED,
    )
    return np.asarray(reducer.fit_transform(matrix), dtype=np.float64)


def assemble_semantic(graph: str | None, multi: MultiGraph) -> SemanticSection | None:
    vectors = multi.get_store(graph).get_entity_vectors()
    if len(vectors) < _MIN_VECTORS:
        return None
    ids = list(vectors.keys())
    matrix = np.array([vectors[entity_id] for entity_id in ids], dtype=np.float64)

    coords: np.ndarray | None = None
    method = "pca"
    if len(vectors) >= _UMAP_MIN_VECTORS:
        coords = _umap_project(matrix)
        if coords is not None:
            method = "umap"
    if coords is None:
        coords = _pca_project(matrix)

    projection = {
        entity_id: [round(float(x), 4), round(float(y), 4)]
        for entity_id, (x, y) in zip(ids, coords, strict=True)
    }
    clusters = _assemble_clusters(graph, multi)
    return SemanticSection(method=method, projection=projection, clusters=clusters)
```

Then `uv sync` (no extra) to refresh `uv.lock` with the new optional group
metadata. The lockfile records the extra without installing it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_semantic.py -v`
Expected: the PCA/cluster tests pass; the two UMAP tests **skip** (umap not
installed). To exercise the UMAP path locally:
`uv sync --extra viz-umap && uv run pytest tests/test_viz_semantic.py -v` →
UMAP tests pass; then `uv sync` to drop the heavy extra again before committing.

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass (mypy typechecks `theloom/viz/semantic.py` with umap absent —
the `umap.*` override keeps the lazy import clean).

```bash
git add pyproject.toml uv.lock theloom/viz/semantic.py tests/test_viz_semantic.py
git commit -m "Add optional UMAP projection for the Semantic Map"
```

---

### Task 3: `scope.mode: "search"`

Add a semantic-search scope: given a `query`, embed it, keep the top matching
entities and the relations induced among them, and label the scope
`search:<query>`. Reuses the exact search internal (`_search_similar`) that
`semantic-search` and `find-clusters` share, so search results agree with the
`loom semantic-search` command.

**Files:**
- Modify: `theloom/viz/scope.py` (`ScopeInput.query`, `"search"` in `_MODES`,
  search branch in `resolve_scope`)
- Modify: `tests/test_viz_scope.py` (search-mode tests)
- Modify: `tests/test_cli_viz_commands.py` (search-scope through `run_handler`)

**Interfaces (verified):**
- `_search_similar(store: FalkorGraphStore, query_text: str, limit: int,
  min_score: float | None = None, entity_types: list[str] | None = None) ->
  list[dict[str, Any]]` (`theloom/operations/semantic.py:128`). Embeds the query
  (`get_embedder().embed_query`), scores every stored vector by `1/(1+L2)`,
  filters, sorts desc, truncates to `limit`; each result is `{"id", "score",
  "metadata": {...}}`. Returns `[]` when the graph has no vectors.
- `resolve_scope(scope, store, as_of=None)` already computes `entities, relations
  = _docs(store, as_of)` up front — the search branch filters those in-memory
  doc sets, so it inherits `asOf` correctly (search ranks by *current*
  embeddings, then intersects with the entities that existed at `as_of`).
- Produces:
  - `ScopeInput` gains `query: str | None = None`.
  - `_MODES` gains `"search"`.
  - Search branch: empty/whitespace `query` ⇒ `VALIDATION_ERROR`; matched ids from
    `_search_similar(store, query, limit=_SEARCH_LIMIT, entity_types=…)`; keep
    matched entities and relations whose both endpoints are matched; label
    `f"search:{query}"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_viz_scope.py` (reuse its `multi` / `seeded` fixtures; add a
stub embedder — the same shape as Task 1's — so no model is needed):

```python
class _StubEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_query(self, text: str) -> list[float]:
        return self._vector


def test_search_scope_keeps_matches_and_induced_relations(
    multi: MultiGraph, seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    # Seed vectors: a,b close to the query direction; c orthogonal (excluded).
    store.set_entity_vector(seeded["a"], [1.0, 0.0])
    store.set_entity_vector(seeded["b"], [0.98, 0.20])
    store.set_entity_vector(seeded["c"], [0.0, 1.0])
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: _StubEmbedder([1.0, 0.0])
    )
    entities, relations, label = resolve_scope(
        ScopeInput.model_validate({"mode": "search", "query": "a and b"}),
        store,
    )
    names = {e["name"] for e in entities}
    assert names == {"a", "b"}  # c is orthogonal to the query
    assert [r["relationType"] for r in relations] == ["causes"]  # a->b, both matched
    assert label == "search:a and b"


def test_search_scope_requires_a_query(multi: MultiGraph, seeded: dict[str, str]) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(ScopeInput(mode="search"), multi.get_store())
    assert err.value.code == "VALIDATION_ERROR"
```

Append to `tests/test_cli_viz_commands.py`:

```python
def test_export_bundle_search_scope(multi: MultiGraph, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = multi.get_store()
    hit = store.create_entity(
        EntityCreate.model_validate(
            {"name": "vector search", "entityType": "concept", "observations": []}
        )
    )
    miss = store.create_entity(
        EntityCreate.model_validate(
            {"name": "unrelated", "entityType": "concept", "observations": []}
        )
    )
    store.set_entity_vector(hit.id, [1.0, 0.0])
    store.set_entity_vector(miss.id, [0.0, 1.0])

    class _Stub:
        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: _Stub())
    result = run_handler(
        "export-bundle", {"scope": {"mode": "search", "query": "vector"}}, multi
    )
    assert {e["name"] for e in result["entities"]} == {"vector search"}
    assert result["meta"]["scope"] == "search:vector"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_viz_scope.py tests/test_cli_viz_commands.py -v`
Expected: FAIL — `"search"` is rejected as an invalid mode / `ScopeInput` has no
`query`.

- [ ] **Step 3: Write the implementation**

`theloom/viz/scope.py`:

```python
from theloom.operations.semantic import _search_similar  # shared search internal

_MODES = ("full", "ego", "causal", "typed", "search")
_SEARCH_LIMIT = 25  # top-k entities the search scope keeps


class ScopeInput(CommandInput):
    mode: str = "full"
    center: str | None = None
    depth: int = Field(default=1, ge=1, le=5)
    entity_type: str | None = Field(default=None, alias="entityType")
    relation_type: str | None = Field(default=None, alias="relationType")
    query: str | None = None
```

In `resolve_scope`, after the existing `entities, relations = _docs(store, as_of)`
line and before the `mode == "full"` check (or as an added branch alongside the
others), handle search:

```python
    if scope.mode == "search":
        if scope.query is None or not scope.query.strip():
            raise ValidationError("Scope mode 'search' requires a non-empty 'query'.")
        entity_types = [scope.entity_type] if scope.entity_type else None
        matched = {
            result["id"]
            for result in _search_similar(
                store, scope.query, limit=_SEARCH_LIMIT, entity_types=entity_types
            )
        }
        search_entities = [e for e in entities if e["id"] in matched]
        matched_ids = {e["id"] for e in search_entities}
        search_relations = [
            r for r in relations if r["from"] in matched_ids and r["to"] in matched_ids
        ]
        return search_entities, search_relations, f"search:{scope.query}"
```

(Place it as the first mode branch after the `_MODES` guard so it short-circuits
before the `full` return; the existing branches are unchanged.)

Reusing `_search_similar` (the internal both `semantic_search` and `find_clusters`
call) keeps search results identical to the `semantic-search` command — the label
`search:<query>` records the query verbatim for the header. `COMMANDS.md` needs no
regeneration (no command changed; the input schema is not surfaced in the catalog)
and `bundle.schema.json` is unaffected (`ScopeInput` is an input model, not part
of the bundle).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_viz_scope.py tests/test_cli_viz_commands.py -v`
Expected: all pass (including the seven Phase 1 scope tests, unchanged).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`

```bash
git add theloom/viz/scope.py tests/test_viz_scope.py tests/test_cli_viz_commands.py
git commit -m "Add search scope mode to the visualization bundle"
```

---

### Task 4: Enrich the dev fixture (semantic cluster) + re-export

The Semantic Map's cluster hulls need at least one real cluster to render, and the
shipped `dev-bundle.json` currently has none. Add a small, semantically-coherent
trio of entities to the live `tapestry-dev` graph, embed them (dev machine — the
model is available locally), and re-export the committed fixture. The new
entities carry **no relations**, so the Systems view (causal-only) and its e2e
counts are untouched; only the Chronicle event counts shift (handled in Task 8).

**Files:**
- Modify (re-export): `tapestry/fixtures/dev-bundle.json`

**Interfaces:** `create-entity`, `embed-entity` (or `embed-entities`),
`export-bundle` — all existing CLI commands. `find_clusters` runs against the live
graph during `export-bundle` (real embedder locally), producing the clusters that
Task 1 now folds into `semantic.clusters`.

- [ ] **Step 1: Add a coherent cluster to the live `tapestry-dev` graph**

Add three tightly-related concepts (a domain trio that embeds close together), and
embed each so they carry vectors:

```bash
for NAME in \
  "Stochastic gradient descent" \
  "Mini-batch gradient descent" \
  "Backpropagation and the chain rule"; do
  ID=$(uv run loom create-entity '{"graph":"tapestry-dev","name":"'"$NAME"'",
    "entityType":"concept","observations":["A first-order optimization method for training neural networks by following the loss gradient."]}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
  uv run loom embed-entity '{"graph":"tapestry-dev","id":"'"$ID"'"}'
done
```

Use observations that describe the *same* idea so the three land above the
`find-clusters` default 0.7 threshold and group. (Adjust wording if a re-export
shows fewer than one cluster — the goal is exactly one demonstrable cluster.)

- [ ] **Step 2: Re-export and sanity-check the fixture**

```bash
uv run loom export-bundle '{"graph":"tapestry-dev"}' > tapestry/fixtures/dev-bundle.json
python3 -c "import json; b=json.load(open('tapestry/fixtures/dev-bundle.json')); \
s=b['semantic']; \
print('method', s['method']); \
print('projection points', len(s['projection'])); \
print('clusters', [(c['label'], c['size']) for c in s.get('clusters', [])]); \
print('event count', len(b['temporal']['events']))"
```

Expected: `method pca` (still below the UMAP threshold — the fixture stays PCA so
CI's PCA path is what ships); ~9 projection points; at least one cluster of size
≥ 2; a higher event count than before (record it — Task 8 re-derives the Chronicle
e2e assertions from this number).

- [ ] **Step 3: Verify the fixture still conforms and nothing regressed**

Run: `cd tapestry && npm test` (schema.test.ts validates the enriched fixture,
now with `clusters`, against the Task-1 schema) `&& cd .. && uv run pytest tests/test_cli_viz_commands.py`
Expected: all pass. If `schema.test.ts` fails, the committed schema is stale —
re-run `uv run python -m theloom.viz.schema` (it should already be current from
Task 1).

- [ ] **Step 4: Commit**

```bash
git add tapestry/fixtures/dev-bundle.json
git commit -m "Enrich the Tapestry dev fixture with a semantic cluster"
```

---

### Task 5: Semantic Map model helpers (pure)

The pure functions the Semantic Map renders from: a scatter of projected points, a
convex hull per cluster, and a point-in-polygon lasso test. No Sigma, no DOM —
unit-tested in isolation, so hull geometry and lasso hit-testing are provable
without WebGL.

**Files:**
- Modify: `tapestry/src/lib/data.ts` (`semantic.clusters` on the raw type)
- Create: `tapestry/src/views/semantic/semanticMap.ts`
- Test: `tapestry/src/views/semantic/semanticMap.test.ts`

**Interfaces:**
- Consumes: `TapestryBundleRaw` (`lib/data.ts`), `resolveTypeColor` /
  `initialPosition` (`views/explorer/buildGraph.ts`).
- Produces:
  - `interface ScatterPoint { id: string; x: number; y: number; entityType: string; label: string }`
  - `buildScatter(bundle): ScatterPoint[]` — join `semantic.projection` (id → [x,y])
    with `bundle.entities` (name, entityType); entities without a projection are
    omitted (not every entity is embedded — the fixture has 6–9 of them).
  - `buildSemanticGraph(bundle): Graph` — a nodes-only graphology graph placing
    each scatter point at its projection `x`/`y`, `color` from `resolveTypeColor`,
    a fixed `size`; **no edges** and **no layout** (the projection *is* the
    layout). Empty entities/projection ⇒ an empty graph.
  - `convexHull(points: Point[]): Point[]` — Andrew's monotone chain; returns the
    hull vertices CCW. Fewer than 3 unique points return the points themselves.
  - `pointInPolygon(p: Point, polygon: Point[]): boolean` — ray-casting.
  - `pointsInLasso(polygon: Point[], points: (Point & { id: string })[]): string[]`
    — ids whose point is inside the (closed) polygon; a polygon under 3 points
    selects nothing.
  - `clusterPolygons(clusters, positionById): { id: number; label: string; hull: Point[] }[]`
    — for each cluster with ≥ 2 members that have positions, the convex hull of
    their positions (space-agnostic; the view passes **viewport** positions).
  - `type Point = { x: number; y: number }`.

- [ ] **Step 1: Write the failing test**

`tapestry/src/views/semantic/semanticMap.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  buildScatter,
  clusterPolygons,
  convexHull,
  pointInPolygon,
  pointsInLasso,
  type Point,
} from "./semanticMap";
import type { TapestryBundleRaw } from "../../lib/data";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 3, relationCount: 0, sections: ["semantic"] },
  entities: [
    { id: "a", name: "A", entityType: "concept" },
    { id: "b", name: "B", entityType: "claim" },
    { id: "c", name: "C", entityType: "concept" },
    { id: "novec", name: "NoVec", entityType: "concept" },
  ],
  relations: [],
  semantic: {
    method: "pca",
    projection: { a: [0, 0], b: [10, 0], c: [5, 8] }, // novec has no projection
    clusters: [{ id: 0, label: "concept", entityIds: ["a", "c"], size: 2 }],
  },
} as unknown as TapestryBundleRaw;

describe("buildScatter", () => {
  it("keeps only entities that have a projection", () => {
    const points = buildScatter(bundle);
    expect(points.map((p) => p.id).sort()).toEqual(["a", "b", "c"]);
    expect(points.find((p) => p.id === "a")).toMatchObject({ x: 0, y: 0, entityType: "concept", label: "A" });
  });
});

describe("convexHull", () => {
  it("returns the outer vertices of a point set", () => {
    const square: Point[] = [
      { x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 4 }, { x: 0, y: 4 }, { x: 2, y: 2 },
    ];
    expect(convexHull(square)).toHaveLength(4); // the interior point is dropped
  });
});

describe("pointInPolygon", () => {
  const square: Point[] = [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 4 }, { x: 0, y: 4 }];
  it("is true inside and false outside", () => {
    expect(pointInPolygon({ x: 2, y: 2 }, square)).toBe(true);
    expect(pointInPolygon({ x: 9, y: 9 }, square)).toBe(false);
  });
});

describe("pointsInLasso", () => {
  it("returns the enclosed ids", () => {
    const polygon: Point[] = [{ x: -1, y: -1 }, { x: 6, y: -1 }, { x: 6, y: 3 }, { x: -1, y: 3 }];
    const points = [
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 10, y: 0 },
      { id: "c", x: 5, y: 8 },
    ];
    expect(pointsInLasso(polygon, points)).toEqual(["a"]);
  });
});

describe("clusterPolygons", () => {
  it("hulls each cluster's positioned members", () => {
    const positions = new Map<string, Point>([
      ["a", { x: 0, y: 0 }],
      ["c", { x: 5, y: 8 }],
    ]);
    const polys = clusterPolygons(bundle.semantic!.clusters!, positions);
    expect(polys).toHaveLength(1);
    expect(polys[0]).toMatchObject({ id: 0, label: "concept" });
    expect(polys[0].hull.length).toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd tapestry && npm test` → FAIL (module missing).

- [ ] **Step 3: Implement**

First extend the raw bundle type in `tapestry/src/lib/data.ts`:

```typescript
  semantic?: {
    method: string;
    projection: Record<string, number[]>;
    clusters?: { id: number; label: string; entityIds: string[]; size: number }[];
  };
```

Then `tapestry/src/views/semantic/semanticMap.ts` — a directed graphology graph
built with fixed positions (mirror `buildGraph`'s node attributes minus edges and
layout seeding, since positions come from the projection), plus the pure geometry:

```typescript
/**
 * semanticMap — pure helpers for the embedding scatter view.
 *
 * The projection is the layout: `buildSemanticGraph` places each entity at its
 * `semantic.projection` coordinate, so no ForceAtlas2 runs. Hull geometry
 * (convex hull per cluster) and the lasso hit-test (point-in-polygon) are
 * space-agnostic — the view feeds them VIEWPORT positions so hulls and lassoes
 * track the Sigma camera. All unit-tested without WebGL.
 */
import Graph from "graphology";
import type { TapestryBundleRaw } from "../../lib/data";
import { resolveTypeColor } from "../explorer/buildGraph";

export interface Point {
  x: number;
  y: number;
}

export interface ScatterPoint extends Point {
  id: string;
  entityType: string;
  label: string;
}

interface Cluster {
  id: number;
  label: string;
  entityIds: string[];
  size: number;
}

export function buildScatter(bundle: TapestryBundleRaw): ScatterPoint[] {
  const projection = bundle.semantic?.projection ?? {};
  const byId = new Map(bundle.entities.map((e) => [e.id as string, e]));
  const points: ScatterPoint[] = [];
  for (const [id, coord] of Object.entries(projection)) {
    const entity = byId.get(id);
    if (!entity || coord.length < 2) continue;
    points.push({
      id,
      x: coord[0],
      y: coord[1],
      entityType: (entity.entityType as string) ?? "concept",
      label: (entity.name as string) ?? id,
    });
  }
  return points;
}

export function buildSemanticGraph(bundle: TapestryBundleRaw): Graph {
  const graph = new Graph({ multi: true, type: "directed" });
  for (const point of buildScatter(bundle)) {
    if (graph.hasNode(point.id)) continue;
    graph.addNode(point.id, {
      label: point.label,
      entityType: point.entityType,
      x: point.x,
      y: point.y,
      size: 6,
      color: resolveTypeColor(point.entityType),
    });
  }
  return graph;
}

function cross(o: Point, a: Point, b: Point): number {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

export function convexHull(points: Point[]): Point[] {
  const unique = Array.from(
    new Map(points.map((p) => [`${p.x},${p.y}`, p])).values(),
  ).sort((p, q) => (p.x === q.x ? p.y - q.y : p.x - q.x));
  if (unique.length < 3) return unique;

  const half = (src: Point[]): Point[] => {
    const out: Point[] = [];
    for (const p of src) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) {
        out.pop();
      }
      out.push(p);
    }
    out.pop();
    return out;
  };
  const lower = half(unique);
  const upper = half([...unique].reverse());
  return [...lower, ...upper];
}

export function pointInPolygon(p: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const a = polygon[i];
    const b = polygon[j];
    const straddles = a.y > p.y !== b.y > p.y;
    if (straddles && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

export function pointsInLasso(
  polygon: Point[],
  points: (Point & { id: string })[],
): string[] {
  if (polygon.length < 3) return [];
  return points.filter((point) => pointInPolygon(point, polygon)).map((point) => point.id);
}

export function clusterPolygons(
  clusters: Cluster[],
  positionById: Map<string, Point>,
): { id: number; label: string; hull: Point[] }[] {
  const polygons: { id: number; label: string; hull: Point[] }[] = [];
  for (const cluster of clusters) {
    const positioned = cluster.entityIds
      .map((id) => positionById.get(id))
      .filter((p): p is Point => p != null);
    if (positioned.length < 2) continue;
    polygons.push({ id: cluster.id, label: cluster.label, hull: convexHull(positioned) });
  }
  return polygons;
}
```

- [ ] **Step 4: Run to verify pass** — `cd tapestry && npm test` → all pass.

- [ ] **Step 5: Gates + commit**

Run: `cd tapestry && npm test && cd .. && uv run pytest tests/test_cli_viz_commands.py`
(no build required — no app source imports these modules yet; Task 6 wires the view.)

```bash
git add tapestry/src/lib/data.ts tapestry/src/views/semantic/semanticMap.ts tapestry/src/views/semantic/semanticMap.test.ts
git commit -m "Add Semantic Map projection, hull, and lasso helpers"
```

---

### Task 6: Semantic Map view — scatter, cluster hulls, lasso, selection sync

The Semantic Map tab: a Sigma scatter of `semantic.projection` colored by entity
type, an SVG cluster-hull overlay with a toggle, hover/click that syncs the global
`selection`, and a freehand lasso that writes a `brushedIds` set into the store.

**Load the `dataviz` and `frontend-design` skills before writing styles/encoding.**

**Files:**
- Create: `tapestry/src/views/semantic/SemanticMap.tsx`, `SemanticMap.css`
- Modify: `tapestry/src/state/store.ts` (View union + `brushedIds` / `setBrushed`)
- Modify: `tapestry/src/App.tsx` (Semantic tab + routing)
- Modify: `tapestry/src/design/tokens.css` (`--hull-stroke` / `--hull-fill` /
  `--brush`, both themes)

**Interfaces:**
- Consumes: `useBundle` context, Task 5 `semanticMap.ts`, `resolveTypeColor`,
  the Systems/Explorer `readVar` + rAF re-resolve pattern, the
  `framedGraphToViewport(getNodeDisplayData(id))` overlay projection.
- Store additions: `View = "explorer" | "overview" | "systems" | "chronicle" |
  "semantic"`; `brushedIds: string[] | null` + `setBrushed(ids: string[] | null)`.

- [ ] **Step 1: Extend the store + tokens (with a test)**

`store.ts`:
```typescript
export type View = "explorer" | "overview" | "systems" | "chronicle" | "semantic";
// ...in TapestryState:
/** The Semantic Map lasso's brushed entity ids; null ⇒ no active brush. */
brushedIds: string[] | null;
setBrushed: (ids: string[] | null) => void;
// ...in the creator:
brushedIds: null,
setBrushed: (brushedIds) => set({ brushedIds }),
```

Add a `store.test.ts` case asserting `brushedIds` defaults to `null` and
`setBrushed(["x"])` sets it (and `setBrushed(null)` clears).

`tokens.css` — add a hull channel (a low-chroma neutral outline + faint fill,
distinct from every entity-type, status, and polarity hue; validated with the
dataviz validator against `--color-canvas` in both themes; hulls are never
color-alone — each carries its cluster label). Add to both `:root` and
`[data-theme="dark"]`:
```css
--hull-stroke: <validated neutral, ≥3:1 on canvas>;
--hull-fill:   <same hue, low-alpha translucent>;
--brush:       var(--color-accent);   /* the lasso stroke — the chrome iris */
```

Run `cd tapestry && npm test` to confirm the store test is green before wiring the
view.

- [ ] **Step 2: Implement the Semantic Map view**

`SemanticMap.tsx` — mirror the Systems lifecycle (instantiate Sigma over
`useMemo(() => buildSemanticGraph(bundle), [bundle])`, resolve colors, rAF
theme re-resolve, `readVar` fallbacks) but with **no layout controller** — the
projection is the layout, so after mount just `sigma.getCamera().animatedReset()`
to fit. Differences from Systems:

- **Selection sync (reuse the Explorer's discipline):** a `selectionRef` +
  reducer halo; `enterNode`/`leaveNode` neighbour-dim is optional (there are no
  edges — a simple hover highlight is enough); `clickNode` → `select(node)`,
  `clickStage` → `select(null)`. The reducer haloes `selection` and dims
  non-brushed nodes when a brush is active (so the map echoes the Explorer's
  brushed set too).
- **Cluster-hull overlay:** an SVG layer (like Systems' glyph layer) sized to the
  canvas. On `afterRender`, build `positionById` by
  `sigma.framedGraphToViewport(sigma.getNodeDisplayData(id))` for every node,
  call `clusterPolygons(bundle.semantic?.clusters ?? [], positionById)`, and draw
  each hull as an SVG `<polygon>` (stroke `--hull-stroke`, fill `--hull-fill`)
  with a `<text>` label at the hull centroid. A **"Hulls" toggle** (default on)
  hides/shows the layer. When there are no clusters, the toggle is disabled with a
  title ("No clusters in this scope").
- **Lasso:** a **"Lasso" toggle** (like path mode). When on, an overlay `<div>`
  with `pointer-events: auto` captures `pointerdown`/`pointermove`/`pointerup`
  (when off, `pointer-events: none` so Sigma pans/zooms normally). Collect the
  pointer path in viewport coords, draw it as an in-progress SVG `polyline`
  (stroke `--brush`); on `pointerup`, compute
  `pointsInLasso(path, points)` where `points` are the same
  `framedGraphToViewport(getNodeDisplayData(id))` viewport positions, then
  `setBrushed(ids.length ? ids : null)` and clear the path. A **brush chip**
  (`{n} brushed · Clear`) shows the current count and clears via
  `setBrushed(null)`; a "View in Explorer" affordance calls `setView("explorer")`.
- **Empty projection:** when `buildSemanticGraph` is empty, show a note ("No
  embeddings in this scope — re-export with the semantic section, or embed the
  graph's entities.").

`App.tsx` — add the Semantic tab to `VIEWS`
(`{ id: "semantic", label: "Semantic", color: "var(--type-source)" }`) and route
`view === "semantic"` to `<SemanticMap key="semantic" />` in the `app__main`
switch (keep the existing four). No hash change — the brush is a transient
cross-view selection, deliberately not serialized (large id arrays would bloat the
hash; `selection`/`filters`/`time` remain the deep-linked state).

- [ ] **Step 3: Verify** — `cd tapestry && npm test` → pass (store test). Manual:
  `npm run dev` → the Semantic tab scatters the fixture's points colored by type;
  the cluster hull wraps the enriched trio with its label; hover highlights,
  clicking a point selects it (and the Explorer detail panel shows it on tab
  switch); toggling Lasso and dragging a loop around some points fills the brush
  chip; "Clear" empties it; both themes read.

- [ ] **Step 4: Build + confirm the sentinel**

```bash
cd tapestry && npm run build && cd ..
test "$(grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html)" = "1" && echo OK
uv run pytest tests/test_cli_viz_commands.py
```

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add the Semantic Map view with cluster hulls and lasso brushing"
```

---

### Task 7: Lasso brushing into the Explorer

The Explorer consumes the store's `brushedIds`: a new reducer layer highlights the
brushed set and dims the rest, a toolbar chip states the brushed count with a
clear affordance, so a lasso in the Semantic Map lands as a focused set the reader
can inspect node-by-node.

**Load the `dataviz` and `frontend-design` skills before touching the chip/encoding.**

**Files:**
- Modify: `tapestry/src/views/explorer/Explorer.tsx` (brush reducer layer + chip)
- Modify: `tapestry/src/views/explorer/Explorer.css` (chip styles)

**Interfaces:**
- Consumes: `brushedIds` / `setBrushed` from the store (Task 6).
- The brush layer sits **between** the FILTER layer and the PATH layer in the
  existing reducer stack (Explorer.tsx lines ~118–163): a node not in `brushedIds`
  (when the set is non-null) is dimmed like the path-outside treatment; a brushed
  node keeps full strength and rises in `zIndex`. It never *hides* nodes (that
  stays the FILTER layer's job) — brushing is emphasis, reversible from the chip.

- [ ] **Step 1: Wire the brush into the Explorer reducers**

In `Explorer.tsx`, subscribe to the store's `brushedIds` and hold it in a
`brushSetRef` (a `Set<string> | null`, updated in an effect that also calls
`sigmaRef.current?.refresh()`, mirroring the `selection` effect). In the
`nodeReducer`, after the FILTER check and before/around the PATH block, apply:
```typescript
const brush = brushSetRef.current;
if (brush && !brush.has(node)) {
  res = { ...res, color: dimRef.current, label: "", zIndex: 0 };
} else if (brush) {
  res = { ...res, zIndex: 1 };
}
```
In the `edgeReducer`, dim any edge with an endpoint outside the brush when a brush
is active (mirror the hovered-edge dim). The existing selection/hover/path layers
continue to compose on top.

- [ ] **Step 2: Add the brush chip**

Render a chip in `explorer__toolbar` (next to `SearchBox`/`FilterPanel`) only when
`brushedIds` is non-null: `"{n} brushed"` with a clear button
(`onClick={() => setBrushed(null)}`), styled from the new CSS. The chip is the
Explorer-side surface of the Semantic Map lasso, and the single place the reader
learns a brush is active while on the Explorer tab.

- [ ] **Step 3: Verify** — `cd tapestry && npm test` → pass (no pure-logic change).
  Manual: `npm run dev` → lasso a set in the Semantic Map, switch to Explorer →
  the brushed nodes stay lit while the rest dim, and the chip shows the count;
  clearing the chip restores full strength; the FILTER panel still hides/reveals
  independently of the brush.

- [ ] **Step 4: Build + confirm the sentinel** (as Task 6 Step 4).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Highlight the Semantic Map lasso brush in the Explorer"
```

---

### Task 8: E2E smoke coverage + docs

Extend the Playwright smoke suite to boot the Semantic Map (scatter, hull toggle,
lasso → Explorer brush), **re-derive the Chronicle counts** the Task-4 fixture
enrichment shifted, and update the docs. CI needs no change — the `tapestry` job
already runs `npm test`, rebuilds, asserts template freshness
(`git diff --exit-code theloom/viz/static/tapestry.html`), and runs `npm run e2e`.

**Files:**
- Modify: `tapestry/e2e/smoke.spec.ts`
- Modify: `README.md` (Visualization section — Semantic Map + search scope)

- [ ] **Step 1: Re-derive the Chronicle e2e counts (fixture churn from Task 4)**

Task 4 added three embedded entities to `tapestry-dev`, so the Chronicle events
and diff-window counts in `smoke.spec.ts` are now stale. Recompute them from the
rebuilt fixture rather than guessing — for the event-list row count and the
diff-window breakdown, read the numbers off the fixture:

```bash
python3 -c "import json; b=json.load(open('tapestry/fixtures/dev-bundle.json')); \
print('events', len(b['temporal']['events'])); \
from collections import Counter; print(Counter(e['type'] for e in b['temporal']['events']))"
```

Update the affected assertions in the existing Chronicle tests
(`toHaveCount(20)` on `.events__row`, and the added/changed/invalidated counts in
the diff test) to the new numbers. The three new entities are non-causal and
unrelated, so the **Systems** tests (loop `3 variables`, `.systems__glyph`
count 3, one `.systems__leverage`) are unchanged — verify, do not edit them.

- [ ] **Step 2: Add Semantic Map e2e coverage**

Add to `smoke.spec.ts` (reuse the existing `beforeAll` template injection and the
`setSlider` helper if needed):

```typescript
test("semantic tab scatters embeddings, toggles hulls, and lassoes into the explorer", async ({
  page,
}) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Semantic" }).click();

  const panel = page.locator("#panel-semantic");
  await expect(panel).toBeVisible();
  await expect(panel.locator("canvas").first()).toBeVisible(); // sigma mounted (no FA2 wait)

  // The enriched fixture ships one cluster, so at least one hull renders and the
  // toggle hides it.
  const hullToggle = panel.getByRole("button", { name: /hulls/i });
  await expect(panel.locator(".semantic__hull").first()).toBeVisible();
  await hullToggle.click();
  await expect(panel.locator(".semantic__hull")).toHaveCount(0);
  await hullToggle.click();

  // Lasso the whole scatter → a brush lands and the chip appears.
  await panel.getByRole("button", { name: /lasso/i }).click();
  const box = (await panel.locator(".semantic__canvas").boundingBox())!;
  await page.mouse.move(box.x + 5, box.y + 5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 5, box.y + 5);
  await page.mouse.move(box.x + box.width - 5, box.y + box.height - 5);
  await page.mouse.move(box.x + 5, box.y + box.height - 5);
  await page.mouse.up();
  await expect(panel.getByText(/\d+ brushed/)).toBeVisible();

  // The brush carries into the Explorer as a count chip.
  await page.getByRole("tab", { name: "Explorer" }).click();
  await expect(page.locator("#panel-explorer").getByText(/\d+ brushed/)).toBeVisible();
});
```

(Adjust the `.semantic__hull` / `.semantic__canvas` / brush-chip locators to the
real markup you shipped in Tasks 6–7 — verify against the running dev build, not
from memory. The lasso drag encloses the whole canvas, so it brushes every
projected point regardless of exact positions.)

Run: `cd tapestry && npx playwright install chromium && npm run build && npm run e2e`
Expected: all smoke tests pass, including the recomputed Chronicle cases and the
unchanged Explorer/Overview/Systems cases.

- [ ] **Step 3: Docs**

README Visualization section: add a short paragraph for the **Semantic Map** (a
UMAP/PCA projection of entity embeddings, labeled cluster hulls from
`find-clusters`, lasso brushing that carries a selection into the Explorer), a
note that UMAP is an optional upgrade
(`uv sync --extra viz-umap`; PCA is the default and the fallback), and the new
**search scope** with a runnable example
(`uv run loom visualize '{"scope":{"mode":"search","query":"gradient descent"}}'`).
Capture a real screenshot of the Semantic Map against the dev fixture
(`docs/images/tapestry-semantic.png`) — no placeholder. Verify `COMMANDS.md` is
current (`uv run pytest tests/test_generate_docs.py`; no command changed, so this
is a no-op check).

- [ ] **Step 4: Full gates**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build && npm run e2e`
Expected: everything passes; the sentinel count in the committed template is 1.

- [ ] **Step 5: Commit**

```bash
git add tapestry/e2e/ README.md docs/images/
git commit -m "Cover the Semantic Map with smoke tests and docs"
```

---

## Plan self-review notes

- **Spec coverage (Phase 3 scope).** UMAP upgrade behind an optional `viz-umap`
  extra with PCA fallback and a `method` field ✓ (T2); deterministic seed ✓ (T2,
  `random_state=42`). Cluster hulls from the real `find-clusters` operation ✓
  (T1 `_assemble_clusters`, T6 SVG hull overlay). Semantic Map tab — projection
  scatter colored by entity type, hull toggle, hover/select synced to the global
  store, click-through to the Explorer ✓ (T6). Lasso brushing → `brushedIds` in
  the store → Explorer highlight + count chip + clear ✓ (T6 capture, T7 consume).
  `scope.mode: "search"` embedding the query, keeping matches + induced relations,
  label `search:<query>`, reachable from the CLI ✓ (T3). e2e + README ✓ (T8).
- **Reuse, not reinvention.** Clusters come from the canonical `find_clusters`
  op (not a re-implemented clustering); search reuses `_search_similar` (the exact
  internal `semantic-search`/`find-clusters` share, so bundle search == command
  search); the Semantic Map reuses `resolveTypeColor`, the Sigma reducer/overlay
  discipline, `framedGraphToViewport(getNodeDisplayData(...))`, and the rAF
  theme-resolve pattern; the Explorer brush layer is one more reducer in its
  existing four-layer stack. PCA stays exactly as Phase 1 shipped it.
- **Model-free tests, honestly.** `find_clusters` and `_search_similar` both embed
  through `get_embedder()`; every Phase 3 test that reaches them monkeypatches
  `theloom.operations.semantic.get_embedder` with a deterministic stub over seeded
  vectors (T1 helper, reused in T2/T3), preserving the viz suite's model-free
  design — CI never downloads the fastembed model. The UMAP-path tests
  `importorskip("umap")`, so CI (which runs `uv sync --frozen`, no extras) skips
  them and exercises the PCA fallback, exactly the path that ships.
- **`asOf` interaction stated (T3).** Search ranks by *current* embeddings, then
  intersects with the entities that existed at `as_of` (the search branch filters
  the already-`as_of`-reconstructed `_docs` result), consistent with Phase 2's
  rule that analytics/semantic stay current while `asOf` bounds the entity set.
- **Fixture churn is contained and honest (T4/T8).** Enriching `tapestry-dev` with
  three embedded concepts is the only way to ship a demonstrable cluster (any new
  embedded entity necessarily adds temporal events). The new entities are
  non-causal and unrelated, so Systems e2e is untouched; only Chronicle's event
  counts shift, and T8 re-derives them from the rebuilt fixture rather than
  hardcoding — following Phase 2's "verify against the running build, not memory"
  discipline. The fixture stays `method: "pca"` (below the UMAP threshold), so the
  committed artifact matches CI's PCA path.
- **Optional-dependency mechanics.** `viz-umap` is a `[project.optional-
  dependencies]` extra (a user-facing feature toggle), not a `[dependency-groups]`
  dev group; `umap.*` joins the mypy `ignore_missing_imports` override (umap-learn
  ships no stubs and is absent in CI), and the runtime import is lazy inside
  `_umap_project` with an `ImportError` fallback — so mypy, ruff, and CI all pass
  without umap installed.
- **Schema discipline.** Only `SemanticSection` changes (adds optional
  `clusters`); `SCHEMA_VERSION` stays 1 (additive/optional, like `asOf`); T1
  regenerates `tapestry/schema/bundle.schema.json` and the drift test pins it;
  `additionalProperties: false` means the fixture is invalid until the schema is
  regenerated, which T1 does before T4 re-exports.
- **Risks flagged for implementers.** (1) `umap-learn` is a heavy install
  (numba/llvmlite/scikit-learn) and its numba/llvmlite pins can conflict with the
  resolved numpy — install it only in the `viz-umap` extra, never the core deps,
  and keep the UMAP test `importorskip`d. (2) Cluster assembly now runs
  `find_clusters` on every `visualize`/`export-bundle` that ships the semantic
  section, which embeds each entity as a query — an O(n) embedding pass on large
  graphs; it is gated by `include.semantic` and capped by `find_clusters`'s
  `maxEntities` (5000), but note it as a cost. (3) The Semantic Map lasso overlay
  must toggle `pointer-events` so it never steals Sigma's pan/zoom when the lasso
  is off; get this wrong and the map appears frozen. (4) Hull/lasso overlays must
  use `framedGraphToViewport(getNodeDisplayData(id))`, not `graphToViewport`, or
  they drift from the nodes under camera moves.
```
