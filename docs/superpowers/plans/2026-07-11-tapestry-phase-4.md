# Tapestry Phase 4 Implementation Plan — Live Mode

> **For agentic workers:** Execute this plan task-by-task, in order. Each task is
> a self-contained unit with a failing test → verify-fail → implement →
> verify-pass → gates → commit cycle. Do not start a task until the previous
> one's gates are green and committed. Steps use checkbox (`- [ ]`) syntax for
> tracking. This plan is written for a generic executing agent — it depends on no
> "superpowers" skills and no external orchestration; every command it needs is
> spelled out inline.

**Goal:** Ship **Live mode** — the `serve` half of the spec's phased
static-then-server delivery. Add an optional **`viz-serve`** dependency extra
(FastAPI + uvicorn), a **`serve`** registry command that starts a read-only REST
server, the endpoints `/api/bundle`, `/api/graphs`, `/api/neighbors`,
`/api/search`, `/api/as-of`, and `/api/entity/{id}`, and a **live data source in
the SPA** so the same committed single-file app — served with its data sentinel
replaced by a live-config marker — fetches from `/api/*` instead of an inlined
bundle, with a header live indicator, a graph switcher, and a refresh button.
Per the approved spec at
`docs/superpowers/specs/2026-07-11-loom-visualization-design.md` (the **Live
mode** decision, the **`serve` (phase 4)** CLI surface, and the
progressive-loading / expand-on-demand notes).

**Architecture:** Phase 4 adds **no store** and **no required** runtime
dependency. The server is **read-only** and reuses the exact assemblers the
static path already ships: `/api/bundle` builds an `ExportBundleInput` from query
params and calls the same `assemble_bundle` (`theloom/viz/bundle.py`);
`/api/neighbors` calls `resolve_scope` in `"ego"` mode
(`theloom/viz/scope.py`); `/api/search` calls the existing `semantic_search`
operation (`theloom/operations/semantic.py`), so live search agrees with
`loom semantic-search`; `/api/entity/{id}` calls `store.read_entity`. FastAPI and
uvicorn are an **optional extra** (`viz-serve`), imported **lazily** exactly like
Phase 3's `umap` (a guarded import inside the factory, `TYPE_CHECKING`-only type
imports, and a mypy `ignore_missing_imports` override), so the core install, the
core `mypy --strict`, and the base test run are unchanged whether or not the
extra is installed. The FastAPI app is produced by a pure factory
`create_app(multi, default_graph=None) -> FastAPI` that is tested with
`fastapi.testclient.TestClient` **without binding a port**; a thin uvicorn runner
is the only untested line. Typed `LoomError` codes map to HTTP statuses through a
single exception handler (no substring matching). On the frontend, live mode is
**purely additive**: the committed template is served with its `__TAPESTRY_BUNDLE__`
sentinel replaced (server-side, per request, via the same `render_html`) by a
small `{"live": true, "apiBase": "/api"}` marker; `loadBundle` detects
`live === true` and fetches `/api/bundle` instead of parsing an inline bundle —
the static `file://` path (inline bundle) and the dev path (fixture fallback)
stay byte-for-byte intact. Views remain mode-agnostic behind the Phase 1
data-source interface. CLI stays JSON-out: `serve` prints a
`{host, port, url, graph}` handshake line and then blocks.

**Tech Stack:** Python 3.11+/Pydantic v2/Typer · **FastAPI + uvicorn (optional,
new `viz-serve` extra)** · httpx (dev-group, for `TestClient`) · React 18 ·
TypeScript · zustand · sigma.js v3 · graphology · Vitest · Playwright. **The only
new runtime dependency is the optional `viz-serve` extra; the core install stays
unchanged.**

## Prerequisites (fresh environment)

- `uv sync`, `docker compose up -d falkordb` (tests connect to the live store —
  nothing is mocked; `uv run loom init` if the default graph is new).
- Node.js 22+ and npm for the `tapestry/` workspace; `cd tapestry && npm ci`.
  Playwright chromium: `cd tapestry && npx playwright install chromium`.
- Phases 1, 2, and 3 are fully implemented and committed (the `theloom/viz`
  subpackage; `visualize` / `export-bundle`; the SPA with Explorer + Overview +
  Systems + Chronicle + Semantic Map; `asOf`; semantic clusters; optional UMAP;
  `scope.mode: "search"`). Read
  `docs/superpowers/plans/2026-07-11-tapestry-phase-1.md`,
  `docs/superpowers/plans/2026-07-11-tapestry-phase-2.md`,
  `docs/superpowers/plans/2026-07-11-tapestry-phase-3.md`, and the approved spec
  before starting.
- The live store already holds a `tapestry-dev` fixture graph (10 entities, of
  which 9 carry embeddings, 1 balancing loop, 1 leverage point, 1 deprecated
  claim, 1 semantic cluster); `tapestry/fixtures/dev-bundle.json` is exported
  from it. Phase 4 does **not** re-export the fixture.
- For the live path only (Tasks 1–4, 7): `uv sync --extra viz-serve` installs
  FastAPI + uvicorn. The API TestClient tests `pytest.importorskip("fastapi")`,
  so a bare `uv run pytest` (no extra) skips them; CI installs the extra so they
  run (see Task 7 for the CI shape). `httpx` is added to the **dev group**, so it
  is present in every `uv sync` (it is what `fastapi.testclient.TestClient`
  drives the app through).

## Global Constraints

These are load-bearing — every one was learned the hard way in Phases 1–3.
Phase 3's constraints are carried forward verbatim; Phase 4 additions follow.

### Carried forward from Phase 3 (still binding)

- **Gates every commit.** `uv run mypy --strict theloom && uv run ruff check . &&
  uv run ruff format . && uv run pytest` must pass, plus — whenever `tapestry/`
  is touched — `cd tapestry && npm test && npm run build` and
  `uv run pytest tests/test_cli_viz_commands.py`. Keep `main` green.
- **No pydantic mypy plugin.** Aliased Pydantic fields must be constructed with
  **alias (camelCase) kwargs** or via `model_validate({...camelCase...})`;
  snake_case kwargs on an aliased field fail `uv run mypy --strict theloom`.
  Mirror the existing `bundle.py` pattern exactly (`generatedAt=…`,
  `entityCount=…`, `leveragePoints=…`, `asOf=…`); non-aliased fields use their
  plain name. Wire names are camelCase; serialize with
  `model_dump(by_alias=True, exclude_none=True)`.
- **The viz test suite is model-free by design.** No viz test downloads the
  fastembed model — vectors are seeded through `store.set_entity_vector(id, vec)`,
  and any test that reaches `_search_similar` / `find_clusters` / `semantic_search`
  MUST monkeypatch `theloom.operations.semantic.get_embedder` with a
  deterministic stub. `get_embedder` is `@lru_cache`d, so patch the **module
  attribute** `theloom.operations.semantic.get_embedder`, not the cache.
- **`EntityCreate.model_validate(...)` requires `"observations": []`** in test
  fixtures.
- **`create-relation` requires an explicit `"polarity": null`** for a non-causal
  relation type.
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
  test **pure modules only**. E2E via Playwright chromium.
- **Canvas-side colors resolve CSS vars at runtime and MUST re-resolve on theme
  change** (rAF + `readVar` pattern). Phase 4 adds no canvas encoding, but the
  live header/indicator styles live in `tokens.css` with both-theme values.
- **DOM overlays pair `framedGraphToViewport(getNodeDisplayData(id))`, never
  `graphToViewport`.** (Relevant only if you touch the Semantic Map — Phase 4
  does not.)
- **Playwright `.fill()` throws on `<input type="range">`, and React swallows a
  plain `input.value = …`.** Use the `setSlider` helper (native prototype setter
  + `input` event) for any range control. The graph switcher is a `<select>`;
  drive it with `selectOption`, not `setSlider`.
- **macOS case-insensitive FS: a component file must not differ from a module
  file only by case** — the `SystemsView.tsx`/`systems.ts`,
  `SemanticView.tsx`/`semanticMap.ts` precedents. Phase 4's new frontend files
  (`lib/live.ts`, any `LiveBadge`/switcher additions to `App.tsx`) must not
  collide by case with an existing file.
- **One commit per task**, including the rebuilt
  `theloom/viz/static/tapestry.html` whenever the frontend changed. Commit
  messages are plain imperative — never mention AI/Claude, never add co-author
  trailers.
- **Python tests hit live FalkorDB** via the `db` / `redis_client` / `namespace`
  fixtures in `tests/conftest.py`; CLI-level tests go through
  `run_handler(name, input, multi)`.
- **The bundle schema is pinned.** Phase 4 changes **no** bundle model, so the
  committed `tapestry/schema/bundle.schema.json` and `SCHEMA_VERSION` (1) are
  untouched — do not regenerate them.

### New in Phase 4

- **`viz-serve` is an optional `[project.optional-dependencies]` extra**
  (`fastapi`, `uvicorn`), a user-facing feature toggle installable via
  `uv sync --extra viz-serve` / `pip install 'theloom[viz-serve]'` — **not** a
  dev group. `fastapi.*` and `uvicorn.*` join the mypy `ignore_missing_imports`
  override next to `umap.*`. The runtime imports are **lazy** (inside
  `create_app` / the uvicorn runner) with the type-only imports guarded by
  `if TYPE_CHECKING:` — so `import theloom.cli.registry` (which references the
  serve handler) never imports FastAPI, and `mypy --strict` passes with the extra
  absent. This is the exact `umap` precedent from Phase 3, applied to two more
  packages.
- **`httpx` goes in the dev group**, not the extra — it is a test dependency of
  `fastapi.testclient.TestClient`, not a runtime dependency of the server. Regen
  `uv.lock` with `uv sync` after editing `pyproject.toml` (record both the extra
  and the dev dep), then commit `uv.lock`.
- **API tests `pytest.importorskip("fastapi")`** at the top, mirroring the UMAP
  tests' `importorskip("umap")`. A bare `uv run pytest` without the extra skips
  them and stays green; CI installs `--extra viz-serve` so they run.
- **`create_app(multi, default_graph=None) -> FastAPI` is the tested unit**,
  driven by `TestClient(create_app(multi))` — **no port is ever bound in a
  test.** The thin `run_uvicorn(app, host, port)` wrapper (which calls
  `uvicorn.run`) is the only untested line; the `serve` handler's blocking branch
  is covered lightly by monkeypatching `run_uvicorn` to a no-op and asserting the
  handshake prints.
- **`serve` prints its handshake, then blocks.** A blocking `uvicorn.run` never
  returns to the CLI's post-handler `output_success`, so the handler emits the
  `{host, port, url, graph}` line itself (via `theloom.cli.io.output_success`)
  **before** calling `run_uvicorn`. A `"check": true` input short-circuits: it
  builds `create_app` and **returns** the envelope without binding — that is the
  registry-level test path. On shutdown (Ctrl-C) `run_uvicorn` returns and the
  CLI prints the envelope once more; that duplicate is harmless and expected.
- **Typed codes map to HTTP through one handler; never substring-match prose.**
  `@app.exception_handler(LoomError)` returns
  `{"error": e.message, "code": e.code}` with the status from a single dict:
  `PARSE_ERROR`/`INPUT_REQUIRED` → 400, `VALIDATION_ERROR` → 422 (FastAPI's own
  validation status), `NOT_FOUND` → 404, `OPERATION_ERROR`/`CONFIG_ERROR` → 500.
  Building an `ExportBundleInput` / `ScopeInput` inside a route wraps
  `pydantic.ValidationError` into `theloom.errors.ValidationError` exactly as
  `run_handler` does, so bad query params become a 422 with `code:
  "VALIDATION_ERROR"`.
- **The live marker never breaks the exactly-once sentinel rule.** The committed
  template ships one `__TAPESTRY_BUNDLE__`. The server reuses `render_html` to
  replace it with the live marker JSON per request (the same `</` → `<\/`
  escaping, the same missing-sentinel `CONFIG_ERROR`), so there is exactly one
  server-side replacement path and the committed template is never edited. The
  frontend detects the marker by shape (`live === true`), so the literal string
  is still absent from app source.
- **Live mode is strictly additive; the static path stays 100% intact.**
  `loadBundle` gains a live branch and an optional `graph` argument, but the
  inline-bundle branch (static `file://`) and the `/fixtures/dev-bundle.json`
  fallback (dev) are unchanged. Every existing e2e (which loads the static
  `file://` build) must stay green untouched.
- **Adding `serve` to the registry changes the generated catalog.** After Task 4,
  regenerate `COMMANDS.md` (`uv run loom --generate-docs > COMMANDS.md`) and bump
  the command count in `CLAUDE.md`'s "What this is" line (152 → 153).
  `tests/test_generate_docs.py` generates fresh from the registry, so it passes
  automatically; the committed `COMMANDS.md` must be regenerated to match the
  convention ("generated from the registry — never hand-edit").
- **Commit each task with an explicit pathspec.** A concurrent session shares
  this working tree and holds **uncommitted Python WIP** in files including
  `docker-compose.yml`, `tests/conftest.py`, `theloom/store/falkor.py`,
  `theloom/operations/bulk.py`, `theloom/documents/chunkstore.py`, and others.
  **Never `git add -A`, never `git add` a directory blindly, and never stage or
  commit those files.** Commit with an explicit list:
  `git add <exact paths> && git commit -m "…" -- <exact paths>`. When a task
  rebuilds the frontend, the pathspec includes `theloom/viz/static/tapestry.html`
  and the specific `tapestry/` sources you changed — not the whole `tapestry/`
  tree if it would sweep in unrelated churn.

## File Structure (Phase 4 additions)

```
pyproject.toml                        + [project.optional-dependencies] viz-serve   (Task 1)
                                      + fastapi.*/uvicorn.* mypy override            (Task 1)
                                      + httpx in the dev group                       (Task 1)
uv.lock                               regenerated (viz-serve + httpx recorded)       (Task 1)
theloom/viz/serve.py                  create_app + error map + endpoints + runner    (Tasks 1–4)
tests/test_viz_serve.py               TestClient API tests (importorskip fastapi)    (Tasks 1–3)
theloom/cli/registry.py               + serve CommandDescriptor + _serve handler     (Task 4)
tests/test_cli_viz_commands.py        + serve check-mode + handshake test            (Task 4)
COMMANDS.md                           regenerated (serve added; 152 → 153)           (Task 4)
CLAUDE.md                             command count 152 → 153                        (Task 4)

tapestry/src/lib/live.ts              detectLive / fetchGraphs (pure, testable)      (Task 5)
tapestry/src/lib/live.test.ts                                                        (Task 5)
tapestry/src/lib/data.ts              loadBundle gains a live branch + graph arg     (Task 5)
tapestry/src/lib/BundleContext.tsx    live-aware: graphs, currentGraph, refresh      (Task 5)
tapestry/src/App.tsx                  live indicator + graph switcher + refresh       (Task 6)
tapestry/src/App.css                  live badge / switcher / refresh styles          (Task 6)
tapestry/src/design/tokens.css        + --live-* tokens (both themes)                 (Task 6)
theloom/viz/static/tapestry.html      rebuilt (frontend changed)                     (Tasks 5,6)

scripts/seed_live_dev.py              deterministic no-vector seed for the live e2e  (Task 7)
tapestry/playwright.live.config.ts    live-mode Playwright project (targets a port)   (Task 7)
tapestry/e2e-live/live.spec.ts        live boot + switcher + refresh smoke            (Task 7)
tapestry/package.json                 + "e2e:live" script                             (Task 7)
.github/workflows/ci.yml              base ci gets --extra viz-serve; + tapestry-live (Task 7)
README.md                             Live mode / serve section                       (Task 8)
```

## Phase roadmap (remaining plans, one document each)

- **Phase 5 — Polish:** saved-view management UI, full a11y/keyboard audit, export
  refinements, 50k-node performance hardening (label level-of-detail,
  progressive loading seeded by top-centrality nodes with live expand-on-demand
  through `/api/neighbors`). Phase 4 ships `/api/neighbors` and the live data
  source; Phase 5 wires the Explorer's double-click ego-expand to it and adds the
  large-graph seeded load.

---

### Task 1: `viz-serve` extra + FastAPI app factory + typed-error → HTTP mapping + `/api/graphs`

Stand up the optional dependency, the app factory, the single `LoomError` → HTTP
exception handler, and the first (simplest) endpoint. This task establishes the
whole serving spine; Tasks 2–3 add endpoints onto it.

**Files:**
- Modify: `pyproject.toml` (`viz-serve` extra; `fastapi.*`/`uvicorn.*` mypy
  override; `httpx` in the dev group)
- Regenerate: `uv.lock`
- Create: `theloom/viz/serve.py` (`create_app`, error handler, `/api/graphs`)
- Create: `tests/test_viz_serve.py`

**Interfaces (verified in the Phase 1–3 code):**
- `MultiGraph.list_graphs() -> list[dict[str, Any]]` →
  `[{"name": str, "loaded": False}, …]` sorted by name
  (`theloom/store/multigraph.py:158`); `MultiGraph.has_graph(name)`;
  `MultiGraph.default_graph`; `MultiGraph.get_store(name | None)`.
- `LoomError` carries `.message` and `.code` (`theloom/errors.py`); the CLI's
  own serialization is `{"error": message, "code": code}` (`theloom/cli/io.py::
  format_error`) — mirror it.
- Produces:
  - `create_app(multi: MultiGraph, default_graph: str | None = None) -> FastAPI`
    — a lazily-imported FastAPI app; registers the `LoomError` handler and the
    `/api/graphs` route. `default_graph` is the fallback target for bundle routes
    (Task 2); store it on the app or close over it.
  - `_STATUS: dict[str, int]` — the code → HTTP status map.

- [ ] **Step 1: Write the failing test**

Create `tests/test_viz_serve.py`:

```python
"""Live-server API tests via FastAPI's TestClient — no port is ever bound.

Skipped when the viz-serve extra is absent (bare `uv run pytest`); CI installs
`--extra viz-serve` so they run. The FalkorDB fixtures come from conftest."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

pytest.importorskip("fastapi")  # viz-serve extra; mirrors the UMAP importorskip

from fastapi.testclient import TestClient  # noqa: E402

from theloom.model import EntityCreate  # noqa: E402
from theloom.store.multigraph import MultiGraph  # noqa: E402
from theloom.viz.serve import create_app  # noqa: E402


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


@pytest.fixture()
def client(multi: MultiGraph) -> TestClient:
    return TestClient(create_app(multi))


def test_graphs_lists_the_default_graph(client: TestClient) -> None:
    response = client.get("/api/graphs")
    assert response.status_code == 200
    names = [g["name"] for g in response.json()]
    assert "default" in names


def test_unknown_entity_is_404_with_typed_code(client: TestClient, multi: MultiGraph) -> None:
    # /api/entity/{id} arrives in Task 3; until then this pins the error-handler
    # contract through /api/bundle with a missing graph (Task 2). For Task 1,
    # assert the handler is installed by hitting a missing graph on /api/graphs?
    # Instead pin the mapping directly:
    from theloom.viz.serve import _STATUS

    assert _STATUS["NOT_FOUND"] == 404
    assert _STATUS["VALIDATION_ERROR"] == 422
    assert _STATUS["CONFIG_ERROR"] == 500
```

(Task 2 replaces the placeholder `test_unknown_entity_is_404…` with a real
end-to-end 404 through `/api/bundle?graph=missing`; keep the `_STATUS`
assertions.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv sync --extra viz-serve && uv run pytest tests/test_viz_serve.py -v`
Expected: FAIL — `theloom.viz.serve` does not exist. (Without the extra the whole
module skips at `importorskip`; install the extra to see the real failure.)

- [ ] **Step 3: Write the implementation**

`pyproject.toml` — add the extra, the mypy override entries, and the dev dep:

```toml
[project.optional-dependencies]
viz-umap = ["umap-learn>=0.5"]  # optional UMAP projection for the Semantic Map (PCA is the default)
viz-serve = ["fastapi>=0.115", "uvicorn>=0.30"]  # optional read-only live server (loom serve)
```

```toml
[[tool.mypy.overrides]]
module = ["falkordb.*", "sympy.*", "constraint.*", "z3.*", "tree_sitter_typescript.*", "umap.*", "fastapi.*", "uvicorn.*"]
ignore_missing_imports = true
```

```toml
[dependency-groups]
dev = [
  "pytest>=8",
  "ruff>=0.6",
  "mypy>=1.11",
  "z3-solver>=4.13",             # optional CEGIS backend
  "httpx>=0.27",                 # fastapi.testclient.TestClient transport (viz-serve API tests)
]
```

`theloom/viz/serve.py`:

```python
"""Live mode — a read-only FastAPI server over the same assemblers the static
bundle uses (assemble_bundle / resolve_scope / semantic_search / read_entity).

FastAPI and uvicorn are the optional `viz-serve` extra: the imports are lazy
(inside create_app / run_uvicorn) and the type-only imports are TYPE_CHECKING,
so importing this module never requires the extra. Typed LoomError codes map to
HTTP statuses through one exception handler — never by substring-matching prose."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from theloom.errors import LoomError
from theloom.store.multigraph import MultiGraph

if TYPE_CHECKING:  # the extra may be absent; annotations are strings under __future__
    from fastapi import FastAPI

# Typed CLI code → HTTP status. Single source; no prose matching.
_STATUS: dict[str, int] = {
    "PARSE_ERROR": 400,
    "INPUT_REQUIRED": 400,
    "VALIDATION_ERROR": 422,
    "NOT_FOUND": 404,
    "OPERATION_ERROR": 500,
    "CONFIG_ERROR": 500,
}


def create_app(multi: MultiGraph, default_graph: str | None = None) -> FastAPI:
    """Build the read-only live-mode app. `default_graph` is the fallback target
    for bundle routes when no `?graph=` is supplied."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.requests import Request

    app = FastAPI(title="Tapestry (The Loom live server)", docs_url=None, redoc_url=None)
    target_default = default_graph or multi.default_graph

    @app.exception_handler(LoomError)
    async def _loom_error(_: Request, exc: LoomError) -> JSONResponse:
        return JSONResponse(
            {"error": exc.message, "code": exc.code},
            status_code=_STATUS.get(exc.code, 500),
        )

    @app.get("/api/graphs")
    def graphs() -> list[dict[str, Any]]:
        return multi.list_graphs()

    # Tasks 2–4 register /api/bundle, /api/as-of, /api/neighbors, /api/search,
    # /api/entity/{id}, and GET / onto this same app + handler.
    return app
```

Then `uv sync` (no extra) to refresh `uv.lock` with the new extra + dev dep
metadata, then `uv sync --extra viz-serve` to install FastAPI locally for the
tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_serve.py -v`
Expected: 2 passed (with the extra installed).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass (mypy typechecks `serve.py` with FastAPI treated as `Any` via
the override; a bare `pytest` skips the API tests). Confirm the base run is green
**without** the extra too: `uv sync && uv run pytest tests/test_viz_serve.py -v`
→ all **skipped**; then `uv sync --extra viz-serve` again.

```bash
git add pyproject.toml uv.lock theloom/viz/serve.py tests/test_viz_serve.py
git commit -m "Add viz-serve extra and the read-only live-server app factory" -- pyproject.toml uv.lock theloom/viz/serve.py tests/test_viz_serve.py
```

---

### Task 2: `/api/bundle` and `/api/as-of`

The core endpoint: map query params to an `ExportBundleInput` and return the same
bundle the static path ships, so a live client and `loom export-bundle` agree.
`/api/as-of` is a **named alias** that requires an `asOf` param and delegates to
the same handler — the roadmap lists it, but as-of is a bundle *parameter*, not a
distinct resource, so it shares one assembler path (justification below).

**Files:**
- Modify: `theloom/viz/serve.py` (`/api/bundle`, `/api/as-of`, query→input helper)
- Modify: `tests/test_viz_serve.py`

**Interfaces (verified):**
- `ExportBundleInput` (`theloom/viz/bundle.py:28`): `graph`, `scope: ScopeInput`,
  `include: IncludeInput` (`analytics`/`temporal`/`semantic` bools), `title`,
  `as_of` (alias `asOf`). `ScopeInput` (`theloom/viz/scope.py:47`): `mode`,
  `center`, `depth` (1–5), `entity_type` (alias `entityType`), `relation_type`
  (alias `relationType`), `query`.
- `assemble_bundle(params, multi) -> dict[str, Any]` — already returns
  `model_dump(by_alias=True, exclude_none=True)`; raises `NotFoundError` for a
  missing graph, `ValidationError` for a bad `asOf` or bad scope.
- `run_handler`'s pattern for turning a `pydantic.ValidationError` into a typed
  `theloom.errors.ValidationError` (`theloom/cli/registry.py:1557`) — reuse it.

**Decision — fold as-of into `/api/bundle?asOf=`, keep `/api/as-of` as a thin
required-param alias.** `assemble_bundle` already accepts `asOf`; there is no
distinct behavior or resource for time-travel — it is a system-time bound on the
same bundle. A second full assembler path would duplicate logic. So `/api/bundle`
accepts an optional `asOf`, and `/api/as-of` is the same handler with `asOf`
**required** (a `422 VALIDATION_ERROR` when absent) — it documents the
time-travel affordance as a named endpoint (honoring the roadmap) while sharing
exactly one code path.

- [ ] **Step 1: Write the failing test**

Replace the Task-1 placeholder and add bundle coverage in `tests/test_viz_serve.py`:

```python
def test_bundle_returns_the_scoped_bundle(client: TestClient, multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get("/api/bundle")
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == 1
    assert body["meta"]["entityCount"] == 1


def test_bundle_missing_graph_is_404(client: TestClient) -> None:
    response = client.get("/api/bundle", params={"graph": "does-not-exist"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_bundle_bad_asof_is_422(client: TestClient, multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get("/api/bundle", params={"asOf": "not-a-timestamp"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_as_of_requires_the_param(client: TestClient) -> None:
    response = client.get("/api/as-of")
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_bundle_ego_scope_needs_a_center(client: TestClient, multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get("/api/bundle", params={"mode": "ego"})  # no center
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_viz_serve.py -v`
  → FAIL (no `/api/bundle`).

- [ ] **Step 3: Implement**

Add to `theloom/viz/serve.py`. Introduce a query→`ExportBundleInput` helper and
the two routes. `Query` params are all optional; the include flags default to the
model's defaults (all True) when omitted:

```python
# add to the top-of-module imports
import pydantic

from theloom.errors import ValidationError
from theloom.viz.bundle import ExportBundleInput, assemble_bundle


def _build_bundle_input(
    graph: str | None,
    mode: str,
    center: str | None,
    depth: int,
    entity_type: str | None,
    relation_type: str | None,
    query: str | None,
    analytics: bool,
    temporal: bool,
    semantic: bool,
    as_of: str | None,
    title: str | None,
) -> ExportBundleInput:
    doc: dict[str, Any] = {
        "graph": graph,
        "scope": {
            "mode": mode,
            "center": center,
            "depth": depth,
            "entityType": entity_type,
            "relationType": relation_type,
            "query": query,
        },
        "include": {"analytics": analytics, "temporal": temporal, "semantic": semantic},
        "asOf": as_of,
        "title": title,
    }
    try:
        return ExportBundleInput.model_validate(doc)
    except pydantic.ValidationError as exc:  # mirror run_handler's mapping
        raise ValidationError(str(exc)) from exc
```

Inside `create_app`, after the imports, add the routes (note `Query` comes from
`fastapi`):

```python
    from fastapi import Query

    def _bundle(
        graph: str | None = Query(default=None),
        mode: str = Query(default="full"),
        center: str | None = Query(default=None),
        depth: int = Query(default=1),
        entityType: str | None = Query(default=None),
        relationType: str | None = Query(default=None),
        query: str | None = Query(default=None),
        analytics: bool = Query(default=True),
        temporal: bool = Query(default=True),
        semantic: bool = Query(default=True),
        asOf: str | None = Query(default=None),
        title: str | None = Query(default=None),
    ) -> dict[str, Any]:
        params = _build_bundle_input(
            graph if graph is not None else target_default,
            mode, center, depth, entityType, relationType, query,
            analytics, temporal, semantic, asOf, title,
        )
        return assemble_bundle(params, multi)

    app.add_api_route("/api/bundle", _bundle, methods=["GET"])

    def _as_of(
        asOf: str | None = Query(default=None),
        graph: str | None = Query(default=None),
    ) -> dict[str, Any]:
        if asOf is None or not asOf.strip():
            raise ValidationError("Endpoint /api/as-of requires a non-empty 'asOf' query parameter.")
        params = _build_bundle_input(
            graph if graph is not None else target_default,
            "full", None, 1, None, None, None, True, True, True, asOf, None,
        )
        return assemble_bundle(params, multi)

    app.add_api_route("/api/as-of", _as_of, methods=["GET"])
```

Note: `graph if graph is not None else target_default` makes `/api/bundle` fall
back to the server's default graph, but a supplied `graph` still flows through
`assemble_bundle`'s own `has_graph` check (→ 404). Passing `target_default`
(which is a real registered graph) means the no-`graph` case never 404s.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_viz_serve.py -v`
  → all pass (with the extra).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`

```bash
git add theloom/viz/serve.py tests/test_viz_serve.py
git commit -m "Add live-server bundle and as-of endpoints" -- theloom/viz/serve.py tests/test_viz_serve.py
```

---

### Task 3: `/api/neighbors`, `/api/search`, `/api/entity/{id}`

The expand-on-demand and detail endpoints. `/api/neighbors` returns the ego
subgraph around an entity (Phase 5 wires the Explorer's double-click to it);
`/api/search` returns semantic hits equal to `loom semantic-search`;
`/api/entity/{id}` returns the full wire entity doc.

**Files:**
- Modify: `theloom/viz/serve.py`
- Modify: `tests/test_viz_serve.py`

**Interfaces (verified):**
- `resolve_scope(ScopeInput(mode="ego", center=id, depth=d), store) ->
  (entities, relations, label)` — raises `NotFoundError` when the center is
  absent (`theloom/viz/scope.py:119`). Reuse it rather than re-walking the graph.
- `semantic_search(SemanticSearchInput(query=…, limit=…, graph=…), multi) ->
  list[dict]` with keys `entityId`/`name`/`entityType`/`score`/…
  (`theloom/operations/semantic.py:503`). It calls `_search_similar`, so results
  match the search scope and the `semantic-search` command. Returns `[]` when the
  graph has no vectors.
- `store.read_entity(id) -> Entity | None` (`theloom/store/falkor.py:151`);
  `entity.model_dump(by_alias=True, exclude_unset=True)` is the wire doc.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_viz_serve.py`:

```python
from theloom.model import RelationCreate  # noqa: E402  (add near the other model import)


def test_neighbors_returns_the_ego_subgraph(client: TestClient, multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate(
            {"from": a.id, "to": b.id, "relationType": "causes", "polarity": "positive"}
        )
    )
    response = client.get("/api/neighbors", params={"id": a.id, "depth": 1})
    assert response.status_code == 200
    body = response.json()
    assert {e["id"] for e in body["entities"]} == {a.id, b.id}
    assert [r["relationType"] for r in body["relations"]] == ["causes"]


def test_neighbors_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/api/neighbors", params={"id": "nope"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_search_returns_hits(client: TestClient, multi: MultiGraph, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = multi.get_store()
    hit = store.create_entity(
        EntityCreate.model_validate({"name": "vector search", "entityType": "concept", "observations": []})
    )
    miss = store.create_entity(
        EntityCreate.model_validate({"name": "unrelated", "entityType": "concept", "observations": []})
    )
    store.set_entity_vector(hit.id, [1.0, 0.0])
    store.set_entity_vector(miss.id, [0.0, 1.0])

    class _Stub:
        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: _Stub())
    response = client.get("/api/search", params={"q": "vector", "limit": 5})
    assert response.status_code == 200
    names = [h["name"] for h in response.json()]
    assert names[0] == "vector search"


def test_entity_returns_the_wire_doc(client: TestClient, multi: MultiGraph) -> None:
    entity = multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "solo", "entityType": "concept", "observations": ["x"]})
    )
    response = client.get(f"/api/entity/{entity.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "solo"


def test_entity_unknown_is_404(client: TestClient) -> None:
    response = client.get("/api/entity/missing")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_viz_serve.py -v`
  → FAIL (routes missing).

- [ ] **Step 3: Implement**

Add to `theloom/viz/serve.py` (top-of-module imports and, inside `create_app`,
three routes):

```python
# top-of-module
from theloom.errors import NotFoundError
from theloom.operations.semantic import SemanticSearchInput, semantic_search
from theloom.viz.scope import ScopeInput, resolve_scope
```

```python
    # inside create_app, after the bundle routes:

    def _neighbors(
        id: str = Query(...),
        depth: int = Query(default=1),
        graph: str | None = Query(default=None),
    ) -> dict[str, Any]:
        store = multi.get_store(graph if graph is not None else target_default)
        entities, relations, _ = resolve_scope(
            ScopeInput(mode="ego", center=id, depth=depth), store
        )
        return {"entities": entities, "relations": relations}

    app.add_api_route("/api/neighbors", _neighbors, methods=["GET"])

    def _search(
        q: str = Query(...),
        limit: int = Query(default=10),
        graph: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return semantic_search(
            SemanticSearchInput.model_validate(
                {"query": q, "limit": limit, "graph": graph if graph is not None else target_default}
            ),
            multi,
        )

    app.add_api_route("/api/search", _search, methods=["GET"])

    def _entity(entity_id: str, graph: str | None = Query(default=None)) -> dict[str, Any]:
        store = multi.get_store(graph if graph is not None else target_default)
        entity = store.read_entity(entity_id)
        if entity is None:
            raise NotFoundError(f"Entity not found with ID: {entity_id}")
        return entity.model_dump(by_alias=True, exclude_unset=True)

    app.add_api_route("/api/entity/{entity_id}", _entity, methods=["GET"])
```

`resolve_scope`'s ego branch already raises `NotFoundError` for a missing center,
so `/api/neighbors` 404s through the shared handler with no extra code. `depth` is
validated by `ScopeInput` (1–5); an out-of-range `depth` becomes a
`pydantic.ValidationError` — wrap it like `_build_bundle_input` does if you want a
clean 422 (optional; the default FastAPI 422 is acceptable, but the typed-code
contract is cleaner — construct `ScopeInput` inside a try/except and re-raise
`theloom.errors.ValidationError`).

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_viz_serve.py -v`
  → all pass.

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`

```bash
git add theloom/viz/serve.py tests/test_viz_serve.py
git commit -m "Add live-server neighbors, search, and entity endpoints" -- theloom/viz/serve.py tests/test_viz_serve.py
```

---

### Task 4: The `serve` command — root SPA page, registry wiring, uvicorn runner

Serve the committed template at `/` with its sentinel replaced by the live-config
marker, register `serve` in the CLI registry with a `check` test hook, and add the
thin (untested) uvicorn runner. This is the task that turns the app factory into a
runnable command and the app into a live SPA.

**Files:**
- Modify: `theloom/viz/serve.py` (`GET /`, `ServeInput`, `run_uvicorn`)
- Modify: `theloom/cli/registry.py` (`serve` descriptor + `_serve` handler)
- Modify: `tests/test_cli_viz_commands.py` (check-mode + handshake tests)
- Regenerate: `COMMANDS.md`; Modify: `CLAUDE.md` (152 → 153)

**Interfaces (verified):**
- `render_html(bundle: dict[str, Any], template_text: str) -> str` and
  `load_template() -> str` (`theloom/viz/html.py`) — the exact server-side
  sentinel replacement (with `</` escaping and the missing-sentinel
  `CONFIG_ERROR`). Reuse them; the "bundle" dict here is the live marker.
- The CLI runs a registry handler then calls `output_success(result)`
  (`theloom/cli/app.py::_make_command`), so a blocking handler must print its own
  handshake first. `theloom.cli.io.output_success` is the indented JSON-line
  writer; importing it into `registry.py`'s handler is fine (io imports only
  `theloom.errors`, so there is no import cycle).
- `CommandDescriptor` fields (`theloom/cli/registry.py:92`): `name`, `category`,
  `summary`, `input_model`, `handler`, `allow_empty`. `serve` registers under the
  **Visualization** category alongside `visualize` / `export-bundle`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_viz_commands.py`:

```python
def test_serve_check_returns_the_handshake_without_binding(multi: MultiGraph) -> None:
    result = run_handler("serve", {"check": True, "host": "127.0.0.1", "port": 8123}, multi)
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 8123
    assert result["url"] == "http://127.0.0.1:8123"
    assert result["graph"] == "default"


def test_serve_registered_under_visualization() -> None:
    by_name = {c.name: c for c in COMMANDS}
    assert by_name["serve"].category == "Visualization"


def test_serve_blocking_prints_handshake_then_runs(multi: MultiGraph, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("fastapi")  # create_app needs the extra
    calls: list[tuple[str, int]] = []
    # Stub the blocking runner so the test never binds a port.
    monkeypatch.setattr(
        "theloom.viz.serve.run_uvicorn",
        lambda app, host, port: calls.append((host, port)),
    )
    run_handler("serve", {"host": "127.0.0.1", "port": 8124}, multi)
    assert calls == [("127.0.0.1", 8124)]
    printed = capsys.readouterr().out
    assert '"url": "http://127.0.0.1:8124"' in printed
```

(Add `import pytest` if the module lacks it — `test_cli_viz_commands.py` already
imports it.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_viz_commands.py -v`
Expected: FAIL — no `serve` command registered; `run_uvicorn` / `ServeInput` do
not exist.

- [ ] **Step 3: Implement**

`theloom/viz/serve.py` — add the root page, the input model, and the runner:

```python
# top-of-module
from theloom.operations.common import CommandInput
from theloom.viz.html import load_template, render_html

_LIVE_MARKER = {"live": True, "apiBase": "/api"}


class ServeInput(CommandInput):
    graph: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    # Test hook: build the app and return the handshake WITHOUT binding a port.
    check: bool = False
```

Inside `create_app`, add the root route (reuse `render_html`, so the exactly-once
sentinel contract and `</` escaping are shared with static mode):

```python
    from fastapi.responses import HTMLResponse

    def _index() -> HTMLResponse:
        html = render_html(_LIVE_MARKER, load_template())
        return HTMLResponse(content=html)

    app.add_api_route("/", _index, methods=["GET"], response_class=HTMLResponse)
```

Then the thin, untested runner (module level):

```python
def run_uvicorn(app: FastAPI, host: str, port: int) -> None:  # thin wrapper — untested
    import uvicorn

    uvicorn.run(app, host=host, port=port)
```

`theloom/cli/registry.py` — import and register (near the other viz imports):

```python
from theloom.viz.serve import ServeInput, create_app, run_uvicorn
```

Add the handler beside the other Visualization handlers:

```python
def _serve(params: ServeInput, multi: MultiGraph) -> dict[str, Any]:
    """Start the read-only live server. Prints the {host, port, url, graph}
    handshake, then blocks in uvicorn until shutdown. `check: true` builds the
    app and returns the handshake without binding — the tested path."""
    app = create_app(multi, default_graph=params.graph)
    graph = params.graph or multi.default_graph
    envelope: dict[str, Any] = {
        "host": params.host,
        "port": params.port,
        "url": f"http://{params.host}:{params.port}",
        "graph": graph,
    }
    if params.check:
        return envelope
    from theloom.cli.io import output_success

    output_success(envelope)  # the handshake — uvicorn.run below never returns to the CLI
    run_uvicorn(app, params.host, params.port)
    return envelope  # reached only after shutdown (Ctrl-C)
```

And the descriptor in the `COMMANDS` list, next to `visualize`:

```python
    CommandDescriptor(
        name="serve",
        category="Visualization",
        summary="Serve the interactive visualization live over a read-only REST API.",
        input_model=ServeInput,
        handler=_serve,
        allow_empty=True,
    ),
```

Regenerate the catalog and bump the count:

```bash
uv run loom --generate-docs > COMMANDS.md
```

In `CLAUDE.md`, change "It exposes **152 commands**" → "**153 commands**".

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli_viz_commands.py tests/test_generate_docs.py -v`
Expected: all pass — the check-mode and handshake tests, the existing viz tests,
and the docs test (which generates fresh from the registry, so `serve` is
included automatically).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass. (No frontend change yet — the committed template already
contains the sentinel `render_html` replaces; do not rebuild here.)

```bash
git add theloom/viz/serve.py theloom/cli/registry.py tests/test_cli_viz_commands.py COMMANDS.md CLAUDE.md
git commit -m "Add the serve command and the live SPA root page" -- theloom/viz/serve.py theloom/cli/registry.py tests/test_cli_viz_commands.py COMMANDS.md CLAUDE.md
```

---

### Task 5: SPA live data source

Teach the frontend to detect the live marker and fetch `/api/bundle`, add a pure
`live.ts` module for detection + graph listing, and make `BundleContext`
live-aware (current graph, graph list, refresh). The static `file://` path (inline
bundle) and the dev fixture fallback stay byte-for-byte intact.

**Load the `dataviz` and `frontend-design` skills before writing any styles or
UI code** (this task is mostly data plumbing, but Task 6's header work builds on
the context shape defined here — keep the live surface deliberate, not a
default-looking dropdown).

**Files:**
- Create: `tapestry/src/lib/live.ts`, `tapestry/src/lib/live.test.ts`
- Modify: `tapestry/src/lib/data.ts` (`loadBundle` live branch + `graph` arg)
- Modify: `tapestry/src/lib/BundleContext.tsx` (live state + `refresh`/`setGraph`)

**Interfaces:**
- Consumes: the `#tapestry-data` block (inline JSON), `parseInlineBundle`
  (`lib/data.ts`), `TapestryBundleRaw`.
- Produces:
  - `interface LiveConfig { live: true; apiBase: string }`
  - `detectLive(): LiveConfig | null` — parses `#tapestry-data`; returns the
    config only when the parsed object has `live === true`; otherwise `null`
    (static inline bundle, or dev sentinel that fails to parse).
  - `fetchGraphs(apiBase): Promise<string[]>` — `GET {apiBase}/graphs` → names.
  - `loadBundle(graph?: string): Promise<TapestryBundleRaw>` — **live** →
    `GET {apiBase}/bundle[?graph=]`; **static** → inline bundle; **dev** →
    `/fixtures/dev-bundle.json`.
  - `BundleContext` value gains: `live: boolean`, `graphs: string[]`,
    `currentGraph: string`, `setGraph(name)`, `refresh()`.

- [ ] **Step 1: Write the failing test**

`tapestry/src/lib/live.test.ts` (pure detection + URL building; no DOM Sigma):

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { detectLive } from "./live";

function setDataBlock(text: string): void {
  document.body.innerHTML = `<script id="tapestry-data" type="application/json">${text}</script>`;
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("detectLive", () => {
  it("returns the config when the marker says live", () => {
    setDataBlock(JSON.stringify({ live: true, apiBase: "/api" }));
    expect(detectLive()).toEqual({ live: true, apiBase: "/api" });
  });

  it("defaults apiBase to /api when omitted", () => {
    setDataBlock(JSON.stringify({ live: true }));
    expect(detectLive()).toEqual({ live: true, apiBase: "/api" });
  });

  it("returns null for a static inline bundle", () => {
    setDataBlock(JSON.stringify({ schemaVersion: 1, meta: {}, entities: [], relations: [] }));
    expect(detectLive()).toBeNull();
  });

  it("returns null for the dev sentinel (unparseable)", () => {
    setDataBlock("__TAPESTRY_BUNDLE__");
    expect(detectLive()).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd tapestry && npm test` → FAIL
  (`live.ts` missing).

- [ ] **Step 3: Implement**

`tapestry/src/lib/live.ts`:

```typescript
/**
 * live.ts — live-mode detection and the live REST client.
 *
 * The server serves the committed template with its `__TAPESTRY_BUNDLE__`
 * sentinel replaced (server-side, per request, via the same render_html the
 * static path uses) by `{"live": true, "apiBase": "/api"}`. We detect live mode
 * by that PARSED SHAPE (`live === true`) — never by the sentinel literal, so the
 * literal stays out of app source and can't be constant-folded. Static mode
 * (a real inline bundle) and dev mode (the unparseable sentinel) both return
 * null here and fall through to loadBundle's existing branches.
 */
export interface LiveConfig {
  live: true;
  apiBase: string;
}

export function detectLive(): LiveConfig | null {
  const block = document.getElementById("tapestry-data");
  if (!block) return null;
  try {
    const parsed = JSON.parse(block.textContent ?? "") as { live?: unknown; apiBase?: unknown };
    if (parsed && parsed.live === true) {
      return { live: true, apiBase: typeof parsed.apiBase === "string" ? parsed.apiBase : "/api" };
    }
  } catch {
    // dev sentinel — not JSON
  }
  return null;
}

export async function fetchGraphs(apiBase: string): Promise<string[]> {
  const response = await fetch(`${apiBase}/graphs`);
  const graphs = (await response.json()) as { name: string }[];
  return graphs.map((g) => g.name);
}
```

`tapestry/src/lib/data.ts` — add the live branch and the optional `graph` arg
(leave `parseInlineBundle` and the dev fallback exactly as they are):

```typescript
import { detectLive } from "./live";

export async function loadBundle(graph?: string): Promise<TapestryBundleRaw> {
  const live = detectLive();
  if (live) {
    const url = graph
      ? `${live.apiBase}/bundle?graph=${encodeURIComponent(graph)}`
      : `${live.apiBase}/bundle`;
    const response = await fetch(url);
    return (await response.json()) as TapestryBundleRaw;
  }
  const block = document.getElementById("tapestry-data");
  const inline = block ? parseInlineBundle(block.textContent ?? "") : null;
  if (inline) return inline;
  const response = await fetch("/fixtures/dev-bundle.json");
  return (await response.json()) as TapestryBundleRaw;
}
```

`tapestry/src/lib/BundleContext.tsx` — make the provider live-aware. Keep the
`ReadyProvider` split (so `buildGraph` stays memoized under an unconditional
hook) and add the live state to the context value:

```typescript
import { detectLive, fetchGraphs } from "./live";
// ...
interface BundleContextValue {
  bundle: TapestryBundleRaw;
  graph: Graph;
  live: boolean;
  graphs: string[];
  currentGraph: string;
  setGraph: (name: string) => void;
  refresh: () => void;
}
```

In `BundleProvider`, track the requested graph and a reload nonce; reload when
either changes; in live mode also fetch the graph list once:

```typescript
export function BundleProvider({ children }: { children: ReactNode }) {
  const live = useMemo(() => detectLive(), []);
  const [bundle, setBundle] = useState<TapestryBundleRaw | null>(null);
  const [requestedGraph, setRequestedGraph] = useState<string | undefined>(undefined);
  const [graphs, setGraphs] = useState<string[]>([]);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    loadBundle(live ? requestedGraph : undefined).then((loaded) => {
      if (alive) setBundle(loaded);
    });
    return () => {
      alive = false;
    };
  }, [live, requestedGraph, nonce]);

  useEffect(() => {
    if (!live) return;
    fetchGraphs(live.apiBase).then(setGraphs).catch(() => setGraphs([]));
  }, [live]);

  if (!bundle) return <div className="app__loading">Loading graph…</div>;
  return (
    <ReadyProvider
      bundle={bundle}
      live={live !== null}
      graphs={graphs}
      currentGraph={requestedGraph ?? bundle.meta.graph}
      setGraph={setRequestedGraph}
      refresh={() => setNonce((n) => n + 1)}
    >
      {children}
    </ReadyProvider>
  );
}
```

Thread the new props through `ReadyProvider` into the context value, and add
hooks `useLive()`, `useGraphs()`, `useCurrentGraph()`, `useSetGraph()`,
`useRefresh()` (or a single `useLiveControls()` returning
`{ live, graphs, currentGraph, setGraph, refresh }`) beside `useBundle`/`useGraph`.
In static/dev mode `live` is `false`, `graphs` is `[]`, `setGraph`/`refresh`
still work (they re-run `loadBundle`, which in static mode returns the same inline
bundle — a harmless no-op refetch).

- [ ] **Step 4: Verify** — `cd tapestry && npm test` → the `live.test.ts` cases
  pass and every existing vitest stays green (the static `loadBundle` path is
  unchanged for inline/dev).

- [ ] **Step 5: Build, sentinel check, gates, commit**

```bash
cd tapestry && npm run build && cd ..
test "$(grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html)" = "1" && echo OK
uv run pytest tests/test_cli_viz_commands.py
```

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

```bash
git add tapestry/src/lib/live.ts tapestry/src/lib/live.test.ts tapestry/src/lib/data.ts tapestry/src/lib/BundleContext.tsx theloom/viz/static/tapestry.html
git commit -m "Add the live data source to the Tapestry SPA" -- tapestry/src/lib/live.ts tapestry/src/lib/live.test.ts tapestry/src/lib/data.ts tapestry/src/lib/BundleContext.tsx theloom/viz/static/tapestry.html
```

---

### Task 6: Header live indicator, graph switcher, and refresh

Surface the live mode in the header: a subtle "Live" indicator, a graph switcher
`<select>` when `/api/graphs` returns more than one graph (drives a `?graph=`
refetch through `setGraph`), and a refresh button that re-fetches the current
bundle. In static/dev mode none of these render — the header is unchanged.

**Load the `dataviz` and `frontend-design` skills before writing any styles.**
The live indicator is a new status channel: it must read in both themes, never be
color-alone (pair the dot with a "Live" label), and sit quietly in the existing
header rhythm rather than shouting. The switcher is a real `<select>`, styled to
match the theme switches, not a browser default.

**Files:**
- Modify: `tapestry/src/App.tsx` (indicator + switcher + refresh in `app__header`)
- Modify: `tapestry/src/App.css` (styles)
- Modify: `tapestry/src/design/tokens.css` (`--live-*` tokens, both themes)

**Interfaces:**
- Consumes: `useLiveControls()` (or the individual hooks) from Task 5's
  `BundleContext` — `{ live, graphs, currentGraph, setGraph, refresh }`.

- [ ] **Step 1: Wire the header**

In `App.tsx`, read the live controls and render, inside the `brand__context`
group (next to the counts), a live cluster that only appears when `live`:

```tsx
const { live, graphs, currentGraph, setGraph, refresh } = useLiveControls();
// ...inside brand__context, after brand__counts:
{live && (
  <span className="brand__live" role="status" aria-label="Live server">
    <span className="brand__livedot" aria-hidden="true" />
    Live
  </span>
)}
```

Add the switcher + refresh to the header's right side (near the theme group), each
guarded by `live`:

```tsx
{live && graphs.length > 1 && (
  <label className="live__switcher">
    <span className="live__switcherlabel">Graph</span>
    <select
      className="live__select"
      value={currentGraph}
      onChange={(e) => setGraph(e.target.value)}
    >
      {graphs.map((g) => (
        <option key={g} value={g}>{g}</option>
      ))}
    </select>
  </label>
)}
{live && (
  <button type="button" className="live__refresh" onClick={refresh} title="Refresh from the server">
    <RefreshIcon />
    Refresh
  </button>
)}
```

Add a small inline `RefreshIcon` SVG mirroring the existing `BrandMark`/theme-icon
style (a circular arrow), and `--live-dot` / `--live-fg` tokens in `tokens.css`
for both `:root` and `[data-theme="dark"]` (a calm success/live hue that meets
WCAG-AA on the header background and is distinct from the entity-type and
status/polarity channels). Style `.brand__live`, `.live__switcher`,
`.live__select`, and `.live__refresh` in `App.css` to sit in the header rhythm.

Because the graph switch and refresh flow through `BundleProvider`'s state, a
switch/refresh re-runs `loadBundle` and rebuilds the graph via the existing
`useMemo(buildGraph)` — selection, filters, and time are preserved where they
still apply (they live in the zustand store, not the bundle), so a refresh keeps
the user's view sensible.

- [ ] **Step 2: Verify (static path unchanged)** — `cd tapestry && npm test` →
  green (no pure-logic change; the header additions are conditional on `live`,
  which is `false` under the vitest/happy-dom static path). Manual: `npm run dev`
  shows the header **without** the live cluster (dev mode). Live rendering is
  exercised end-to-end in Task 7.

- [ ] **Step 3: Build + confirm the sentinel**

```bash
cd tapestry && npm run build && cd ..
test "$(grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html)" = "1" && echo OK
uv run pytest tests/test_cli_viz_commands.py
```

- [ ] **Step 4: Full gates**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

- [ ] **Step 5: Commit**

```bash
git add tapestry/src/App.tsx tapestry/src/App.css tapestry/src/design/tokens.css theloom/viz/static/tapestry.html
git commit -m "Add the live indicator, graph switcher, and refresh to the header" -- tapestry/src/App.tsx tapestry/src/App.css tapestry/src/design/tokens.css theloom/viz/static/tapestry.html
```

---

### Task 7: Live-mode e2e + CI

Prove live mode end-to-end against a real `uv run loom serve` process, and wire
CI so both the Python API tests and the browser live-smoke run green. The static
`file://` e2e (`tapestry/e2e/`) and the fast `tapestry` job stay untouched.

**Files:**
- Create: `scripts/seed_live_dev.py` (deterministic, no-vector seed)
- Create: `tapestry/playwright.live.config.ts`, `tapestry/e2e-live/live.spec.ts`
- Modify: `tapestry/package.json` (`"e2e:live"` script)
- Modify: `.github/workflows/ci.yml` (base `ci` gets `--extra viz-serve`; add a
  `tapestry-live` job)

**CI shape (decision).** Two changes, no churn to the existing jobs:
1. **Extend the base `ci` job** to `uv sync --frozen --extra viz-serve`, so the
   `tests/test_viz_serve.py` TestClient tests **run in CI** (they `importorskip`,
   so a bare local `pytest` still skips them). FastAPI + uvicorn are lightweight
   pure-Python — this is cheap, unlike the UMAP extra CI deliberately omits.
2. **Add a dedicated `tapestry-live` job** (kept separate so the fast static
   `tapestry` job stays dependency-light): a FalkorDB service, uv +
   `--extra viz-serve`, Node 22 + `npm ci`, seed the live graphs, start
   `loom serve` in the background, wait for the port, then run the live Playwright
   spec. The live e2e uses the **committed** `theloom/viz/static/tapestry.html`
   the server serves — no rebuild — and does **not** depend on embeddings (the
   seed carries no vectors), so it never downloads the fastembed model and stays
   fast and deterministic.

- [ ] **Step 1: Deterministic no-vector seed**

`scripts/seed_live_dev.py` — recreates a small `tapestry-dev` graph (entities +
one causal loop, enough for the Explorer/Overview to render) and a second
`tapestry-alt` graph (so the switcher has more than one graph). **No vectors**, so
no model download; the Semantic tab shows its empty state in live CI, which is
expected and fine.

```python
"""Seed a minimal live graph set for the live-mode e2e (no embeddings).

Run: `uv run python scripts/seed_live_dev.py`. Idempotent-ish: it wipes and
recreates the two demo graphs it owns. Uses MultiGraph + config directly (the
single config path), never touching a user's real graphs beyond these two names.
"""

from __future__ import annotations

from falkordb import FalkorDB

from theloom.config import load_config
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


def main() -> None:
    config = load_config()
    db = FalkorDB(host=config.host, port=config.port)
    multi = MultiGraph(db, db.connection, default_graph=config.default_graph)

    for name in ("tapestry-dev", "tapestry-alt"):
        if multi.has_graph(name):
            multi.delete_graph(name) if name != multi.default_graph else None
        multi.create_graph(name)

    dev = multi.get_store("tapestry-dev")
    ids: dict[str, str] = {}
    for entity_name, kind in (
        ("Resource stock", "variable"),
        ("Consumption rate", "variable"),
        ("Scarcity signal", "variable"),
        ("Conservation policy", "claim"),
    ):
        entity = dev.create_entity(
            EntityCreate.model_validate(
                {"name": entity_name, "entityType": kind, "observations": [f"{entity_name} note."]}
            )
        )
        ids[entity_name] = entity.id

    for src, dst, rel, pol in (
        ("Resource stock", "Consumption rate", "inhibits", "negative"),
        ("Consumption rate", "Scarcity signal", "causes", "positive"),
        ("Scarcity signal", "Resource stock", "inhibits", "negative"),
    ):
        dev.create_relation(
            RelationCreate.model_validate(
                {"from": ids[src], "to": ids[dst], "relationType": rel, "polarity": pol}
            )
        )

    alt = multi.get_store("tapestry-alt")
    alt.create_entity(
        EntityCreate.model_validate(
            {"name": "Alt-graph seed", "entityType": "concept", "observations": ["Second graph."]}
        )
    )
    print("Seeded tapestry-dev (4 entities, 3 causal relations) and tapestry-alt (1 entity).")


if __name__ == "__main__":
    main()
```

Verify the exact `create_entity` / `create_relation` signatures against
`theloom/store/falkor.py` before finalizing (the model kwargs are aliased —
construct via `model_validate` with camelCase keys, per the Global Constraints).
Confirm `delete_graph` on a non-default graph and `create_graph` behave as the
seed assumes; adjust the wipe logic if a graph already exists.

- [ ] **Step 2: Live Playwright config + spec**

`tapestry/playwright.live.config.ts` — a separate project pointing at the running
server (no `webServer`; CI starts the server explicitly so ordering is
deterministic):

```typescript
import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.LIVE_PORT ?? "8100";

export default defineConfig({
  testDir: "e2e-live",
  fullyParallel: false,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, ...devices["Desktop Chrome"] },
  projects: [{ name: "chromium-live", use: { ...devices["Desktop Chrome"] } }],
});
```

`tapestry/e2e-live/live.spec.ts`:

```typescript
import { expect, test } from "@playwright/test";

/**
 * Live-mode smoke: against a real `loom serve` process (seeded by
 * scripts/seed_live_dev.py — no embeddings). Asserts the boot path (marker →
 * /api/bundle → Explorer renders), the live indicator, the graph switcher, and
 * the refresh button. Semantic/search live features need vectors and are covered
 * locally, not here.
 */
test("live mode boots, shows the indicator, switches graphs, and refreshes", async ({ page }) => {
  await page.goto("/");

  // The app fetched /api/bundle and built the graph — Sigma's canvas mounts.
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();

  // The live indicator is present (absent in the static file:// build).
  await expect(page.getByRole("status", { name: "Live server" })).toBeVisible();

  // The switcher lists the two seeded graphs and can switch.
  const select = page.locator(".live__select");
  await expect(select).toBeVisible();
  await select.selectOption("tapestry-alt");
  await expect(page.locator(".brand__graph")).toHaveText("tapestry-alt");

  // Refresh re-fetches without error (Explorer still renders).
  await page.getByRole("button", { name: /refresh/i }).click();
  await expect(page.locator(".explorer__canvas canvas").first()).toBeVisible();
});
```

Verify the `.brand__graph` locator reflects the switched graph — `App.tsx` renders
`bundle.meta.title ?? bundle.meta.graph`; after a switch the new bundle's
`meta.graph` is `tapestry-alt`, so this holds. Adjust locators to the real markup
you shipped in Task 6 (verify against a locally running `loom serve`, not from
memory).

`tapestry/package.json` — add the script:

```json
    "e2e:live": "playwright test --config playwright.live.config.ts"
```

- [ ] **Step 3: CI**

Extend the base `ci` job's sync and add the `tapestry-live` job in
`.github/workflows/ci.yml`:

```yaml
      - name: Sync dependencies
        run: uv sync --frozen --extra viz-serve
```

```yaml
  tapestry-live:
    runs-on: ubuntu-latest
    services:
      falkordb:
        image: falkordb/falkordb:latest
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 20
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen --extra viz-serve
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: tapestry/package-lock.json }
      - run: npm ci
        working-directory: tapestry
      - run: npx playwright install --with-deps chromium
        working-directory: tapestry
      - name: Seed the live demo graphs
        run: uv run python scripts/seed_live_dev.py
      - name: Start the live server
        run: |
          uv run loom serve '{"graph":"tapestry-dev","host":"127.0.0.1","port":8100}' &
          for i in $(seq 1 30); do
            curl -sf http://127.0.0.1:8100/api/graphs && break || sleep 1;
          done
      - name: Live e2e
        run: npm run e2e:live
        working-directory: tapestry
        env:
          LIVE_PORT: "8100"
```

Run the whole thing locally first:

```bash
uv sync --extra viz-serve
docker compose up -d falkordb
uv run python scripts/seed_live_dev.py
uv run loom serve '{"graph":"tapestry-dev","host":"127.0.0.1","port":8100}' &
# wait for http://127.0.0.1:8100/api/graphs to answer, then:
cd tapestry && LIVE_PORT=8100 npm run e2e:live
```

Confirm the base-CI change is coherent: `uv sync --frozen --extra viz-serve`
requires `uv.lock` to already record the extra (Task 1 regenerated it) — if
`--frozen` complains, the lock is stale; re-run `uv sync` and recommit `uv.lock`.

- [ ] **Step 4: Gates**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build && npm run e2e`
Expected: everything green, including the **unchanged** static `e2e/` suite (the
live suite runs via `e2e:live`, not `e2e`). Confirm `mypy --strict` covers
`scripts/seed_live_dev.py` only if it is under a checked path — it is not in
`theloom/`, so mypy's `files = ["theloom"]` skips it; run
`uv run ruff check scripts/` to keep it lint-clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_live_dev.py tapestry/playwright.live.config.ts tapestry/e2e-live/live.spec.ts tapestry/package.json .github/workflows/ci.yml
git commit -m "Cover live mode with an end-to-end smoke test and CI" -- scripts/seed_live_dev.py tapestry/playwright.live.config.ts tapestry/e2e-live/live.spec.ts tapestry/package.json .github/workflows/ci.yml
```

---

### Task 8: Docs

Document live mode: the `serve` command, the REST endpoints, the optional
`viz-serve` extra, and the live indicator/switcher/refresh — with a runnable
example. Confirm `COMMANDS.md` (regenerated in Task 4) is current.

**Files:**
- Modify: `README.md` (a **Live mode / `serve`** subsection under Visualization)

- [ ] **Step 1: Write the README section**

Under the existing `## Visualization` section, after the static `visualize`
paragraphs, add a **Live mode** subsection covering:

- What live mode is: `loom serve` serves the *same* single-file SPA against the
  live store over a read-only REST API — no rebuild, no separate frontend.
- Install: the optional extra — `uv sync --extra viz-serve`
  (`pip install 'theloom[viz-serve]'`).
- A runnable example:
  ```bash
  uv sync --extra viz-serve
  uv run loom serve '{"graph": "tapestry-dev", "host": "127.0.0.1", "port": 8100}'
  # → {"host": "127.0.0.1", "port": 8100, "url": "http://127.0.0.1:8100", "graph": "tapestry-dev"}
  # open http://127.0.0.1:8100
  ```
- The endpoints table: `GET /` (the live SPA), `/api/bundle` (query params mirror
  `export-bundle`: `graph`, `mode`/`center`/`depth`/`entityType`/`relationType`/
  `query`, `analytics`/`temporal`/`semantic`, `asOf`, `title`), `/api/graphs`,
  `/api/neighbors?id=&depth=`, `/api/search?q=&limit=`, `/api/as-of?asOf=`,
  `/api/entity/{id}`. Note the server is **read-only**.
- The live UI: a "Live" indicator in the header, a graph switcher when more than
  one graph exists, and a refresh button. Note that the static `visualize` file
  and live mode share one codebase.

Optionally capture a real screenshot of the header live cluster
(`docs/images/tapestry-live.png`) against a running `loom serve` — no
placeholder. If you add it, include it in the commit pathspec.

- [ ] **Step 2: Verify docs are current**

Run: `uv run pytest tests/test_generate_docs.py -v` and confirm `COMMANDS.md`
contains `serve` (regenerated in Task 4): `grep -n '`serve`' COMMANDS.md`.

- [ ] **Step 3: Gates**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/images/tapestry-live.png
git commit -m "Document live mode and the serve command" -- README.md docs/images/tapestry-live.png
```

(Drop `docs/images/tapestry-live.png` from the `git add`/pathspec if you did not
capture a screenshot.)

---

## Plan self-review notes

- **Spec coverage (Phase 4 scope).** Optional `viz-serve` extra (FastAPI +
  uvicorn) with a lazy, mypy-strict-safe import following the `viz-umap`
  precedent ✓ (T1). `serve` registry command that serves the SPA at `/` (committed
  template + server-side sentinel replacement to a live-config marker) and the REST
  endpoints `/api/bundle`, `/api/graphs`, `/api/neighbors`, `/api/search`,
  `/api/as-of`, `/api/entity/{id}` ✓ (T1–T4). Typed error codes → HTTP statuses
  through one handler, no substring matching (NOT_FOUND→404, VALIDATION_ERROR→422,
  CONFIG_ERROR/OPERATION_ERROR→500) ✓ (T1). CLI JSON-out handshake with a `check`
  test hook and a TestClient-tested app factory that never binds a port ✓ (T1/T4).
  Live data source in the SPA: `loadBundle` detects the marker → fetches
  `/api/bundle`; live indicator, graph switcher (>1 graph), refresh ✓ (T5/T6);
  static single-file path 100% intact ✓ (T5 leaves inline/dev branches unchanged).
  e2e against a real `loom serve` + green CI ✓ (T7). README + COMMANDS.md ✓
  (T4/T8).
- **Reuse, not reinvention.** `/api/bundle` calls the same `assemble_bundle` the
  static path ships; `/api/neighbors` reuses `resolve_scope`'s ego mode;
  `/api/search` reuses the public `semantic_search` op (so live search ==
  `loom semantic-search`); `/api/entity/{id}` reuses `store.read_entity`; the root
  `/` reuses `render_html` + `load_template` (one server-side sentinel path,
  shared with static mode). The server adds no store and no assembler.
- **Live marker vs. sentinel contract (resolved).** The committed template keeps
  its single `__TAPESTRY_BUNDLE__`. The server replaces it *per request* with
  `{"live": true, "apiBase": "/api"}` via `render_html` — the same unbounded
  replace + `</`→`<\/` escaping + missing-sentinel `CONFIG_ERROR` the static path
  uses, so there is exactly one replacement code path and the template file is
  never edited. The frontend detects live mode by the parsed shape (`live ===
  true`), never by the literal, so the `never-introduce-the-literal` rule holds and
  the "exactly one sentinel in built output" grep still passes.
- **As-of endpoint folding (resolved + justified).** As-of is a system-time
  *parameter* on the same bundle, not a distinct resource — `assemble_bundle`
  already takes `asOf`. `/api/bundle?asOf=` carries it; `/api/as-of` is a thin
  alias that **requires** `asOf` (422 otherwise) and delegates to the same
  assembler, so the roadmap's named endpoint exists without a duplicate code path.
- **CI shape (resolved).** Base `ci` gains `--extra viz-serve` so the TestClient
  API tests run in the main test job (cheap, real coverage), while still
  `importorskip`-guarded for a bare local run. A separate `tapestry-live` job
  (FalkorDB + `--extra viz-serve` + Node + seed + background `loom serve` + wait +
  `e2e:live`) keeps the fast static `tapestry` job untouched. The live e2e uses a
  no-vector seed, so CI never downloads the fastembed model; live semantic/search
  behavior is covered locally, not in CI.
- **Blocking-server JSON-out (resolved honestly).** A blocking `uvicorn.run` never
  returns to the CLI's post-handler `output_success`, so `_serve` prints the
  handshake itself before blocking; `check: true` returns the envelope without
  binding (the registry-level test), and monkeypatching `run_uvicorn` covers the
  print-then-block wiring without a port. The only untested line is
  `uvicorn.run` inside the thin `run_uvicorn` wrapper.
- **Optional-dependency mechanics.** `viz-serve` is a `[project.optional-
  dependencies]` extra (feature toggle); `fastapi.*`/`uvicorn.*` join the mypy
  `ignore_missing_imports` override; the runtime imports are lazy inside
  `create_app`/`run_uvicorn` with `TYPE_CHECKING`-only annotations — so importing
  `theloom.cli.registry` never imports FastAPI, and `mypy --strict`, `ruff`, and
  the core test run all pass with the extra absent. `httpx` is a **dev-group** dep
  (TestClient transport), not shipped to users. Only `serve` changes the CLI
  surface, so Task 4 regenerates `COMMANDS.md` and bumps `CLAUDE.md`'s count; no
  bundle model changes, so the schema/`SCHEMA_VERSION` are untouched.
- **Concurrent-session hygiene.** Every commit uses an explicit pathspec and never
  stages the concurrent session's uncommitted Python WIP (`docker-compose.yml`,
  `tests/conftest.py`, `theloom/store/falkor.py`, `theloom/operations/bulk.py`,
  `theloom/documents/chunkstore.py`, and others). No task edits those files —
  Task 7's seed reads `theloom/store/falkor.py`'s signatures but commits only
  `scripts/seed_live_dev.py` and the tapestry/CI files it owns.
- **Risks flagged for implementers.** (1) **`TestClient` needs `httpx`** —
  without it, `from fastapi.testclient import TestClient` raises at import; the
  dev-group `httpx` addition (T1) is load-bearing, and the base-CI `--extra
  viz-serve` change is what makes the API tests actually execute in CI. (2) **The
  no-vector seed** means the live Semantic tab shows its empty state in CI — do
  not assert clusters or search results in the live e2e; seeding real vectors
  would pull the fastembed model into CI and make it slow/flaky. (3) **Server
  start-up ordering in CI** — the seed must run before `loom serve`, and the e2e
  must wait for `/api/graphs` to answer (the `curl` retry loop) before Playwright
  connects; a race here is the most likely CI flake. (4) **Model-kwarg aliasing in
  the seed** — construct `EntityCreate`/`RelationCreate` via `model_validate` with
  camelCase keys and an explicit `polarity` for causal relations, per the Global
  Constraints, or the seed fails validation. (5) **`create_app` returning `Any`**
  — with `fastapi.*` in the mypy override, `FastAPI` is `Any`, so the factory's
  return type and the route decorators are effectively untyped; this is the same
  trade-off the `umap` path accepts, but it means route bugs surface at test time,
  not typecheck time — the TestClient tests (T1–T3) are the real safety net, so
  keep their coverage of every endpoint and every error code. (6) **`--frozen`
  vs. the new extra** — `uv sync --frozen --extra viz-serve` only works once
  `uv.lock` records the extra (T1); if CI's base job errors on `--frozen`, the
  lock was not recommitted.
