# Tapestry Phase 2 Implementation Plan — Systems + Chronicle

> **For agentic workers:** Execute this plan task-by-task, in order. Each task is
> a self-contained unit with a failing test → verify-fail → implement →
> verify-pass → gates → commit cycle. Do not start a task until the previous
> one's gates are green and committed. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Ship the two Loom-unique views the spec promises — a **Systems** view
(causal-loop diagram: polarity-signed edges, reinforcing/balancing loop list,
loop isolation + animated flow, Meadows leverage markers) and a **Chronicle**
view (bi-temporal time travel: a scrubber that replays graph construction from
`temporal.events`, diff mode comparing two instants, an event stream with
jump-to) — plus the `asOf` bi-temporal bundle parameter on the Python side,
per the approved spec at
`docs/superpowers/specs/2026-07-11-loom-visualization-design.md` (Systems View,
Chronicle, and Data-contract sections).

**Architecture:** Phase 2 adds no store, no new Python runtime dependency, and
no new frontend dependency. The Systems and Chronicle views are new tabs in the
existing `tapestry/` SPA, each a Sigma instance over a graphology model derived
from the **same `TapestryBundle`** Phase 1 already ships — Systems builds a
causal-only subgraph and reads `analytics.loops` / `analytics.leveragePoints`
(both already in the bundle); Chronicle reuses the shared graph from
`BundleContext` and drives per-time visibility/styling from `temporal.events`
(both already in the bundle). The one Python change is `asOf` on
`ExportBundleInput` — a system-time bound that reconstructs the entity/relation
snapshot through the store's real `read_entity_as_of` path, sets `meta.asOf`,
and truncates the shipped event log to `asOf`. Views stay mode-agnostic behind
the Phase 1 data-source interface. CLI stays JSON-out.

**Tech Stack (all already installed in Phase 1):** Python 3.11+/Pydantic
v2/Typer · React 18 · TypeScript · zustand · sigma.js v3 · graphology
(+ layout-forceatlas2, communities-louvain, shortest-path) · Vitest · Playwright.
**No new dependencies in this phase.**

## Prerequisites (fresh environment)

- `uv sync`, `docker compose up -d falkordb` (tests connect to the live store —
  nothing is mocked; `uv run loom init` if the default graph is new).
- Node.js 22+ and npm for the `tapestry/` workspace; `cd tapestry && npm ci`.
  Playwright chromium: `cd tapestry && npx playwright install chromium`.
- Phase 1 is fully implemented and committed (viz subpackage, `visualize` /
  `export-bundle`, the SPA with Explorer + Overview). Read
  `docs/superpowers/plans/2026-07-11-tapestry-phase-1.md` and the approved spec
  before starting.
- The live store already holds a `tapestry-dev` fixture graph (6 entities,
  5 relations, 1 balancing loop); `tapestry/fixtures/dev-bundle.json` is
  exported from it. Task 2 enriches it for Phase 2.

## Global Constraints

These are load-bearing — every one was learned the hard way in Phase 1.

- **Gates every commit.** `uv run mypy --strict theloom && uv run ruff check . &&
  uv run ruff format . && uv run pytest` must pass, plus — whenever `tapestry/`
  is touched — `cd tapestry && npm test && npm run build` and
  `uv run pytest tests/test_cli_viz_commands.py`. Keep `main` green.
- **No pydantic mypy plugin.** Aliased Pydantic fields must be constructed with
  **alias (camelCase) kwargs** or via `model_validate({...camelCase...})`;
  snake_case kwargs on an aliased field fail `uv run mypy --strict theloom`.
  Mirror the existing `bundle.py` pattern exactly (`generatedAt=…`,
  `entityCount=…`, `leveragePoints=…`); non-aliased fields (`graph`, `centrality`,
  `components`, `loops`, `bridges`) use their plain name. Wire names are
  camelCase; serialize with `model_dump(by_alias=True, exclude_none=True)`.
- **`EntityCreate.model_validate(...)` requires `"observations": []`** in test
  fixtures.
- **Never introduce a literal `__TAPESTRY_BUNDLE__` string constant in tapestry
  app source.** esbuild constant-folds it; the sentinel must appear exactly once
  in built output. `tapestry/src/lib/data.ts` already detects dev-mode by a
  `JSON.parse` failure rather than comparing against the literal — keep it that
  way. After every frontend-touching task, `cd tapestry && npm run build` and
  confirm the sentinel count is exactly 1:
  `grep -c '__TAPESTRY_BUNDLE__' theloom/viz/static/tapestry.html` → `1`.
- **`tsc -b` strict project references.** `as never` casts that get spread must
  be `as unknown as T` instead. Test-fixture bundles typed `as unknown as
  TapestryBundleRaw` (not `as never`).
- **vitest 3.x + happy-dom (no WebGL).** Never instantiate Sigma in unit tests —
  test **pure modules only** (`systems.ts`, `replay.ts`). E2E via Playwright
  chromium (config `testDir: "e2e"`; vitest excludes `e2e/**`).
- **Canvas-side colors resolve CSS vars at runtime and MUST re-resolve on theme
  change.** Reuse the Explorer's rAF + `readVar` pattern and
  `resolveGraphColors` in any new Sigma view. Any new `--polarity-*` / `--loop-*`
  tokens live in `tokens.css` with both-theme values and concrete fallbacks in
  the resolving module (mirror `buildGraph.ts`'s `TYPE_FALLBACK` / `EDGE_FALLBACK`).
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
- **UI tasks (Tasks 4, 5, 7, 8): load the `dataviz` and `frontend-design`
  skills BEFORE writing any styles or chart/encoding code.** Polarity, loop
  classification, and diff encodings are new color channels — they must pass the
  dataviz palette/contrast rules in both themes, and are never color-alone
  (always paired with a glyph, label, or shape).

## File Structure (Phase 2 additions)

```
theloom/viz/schema.py                 + TapestryMeta.asOf field                (Task 1)
theloom/viz/scope.py                  + as_of reconstruction in _docs/resolve   (Task 1)
theloom/viz/temporal.py               + as_of truncation in assemble_temporal   (Task 1)
theloom/viz/bundle.py                 + ExportBundleInput.asOf, validation       (Task 1)
tapestry/schema/bundle.schema.json    regenerated (asOf added)                   (Task 1)
tests/test_viz_asof.py                                                            (Task 1)
tapestry/fixtures/dev-bundle.json     re-exported (leverage + status history)    (Task 2)

tapestry/src/state/store.ts           + systems/chronicle views, time state      (Tasks 4,7)
tapestry/src/state/urlHash.ts         + time in the hash                          (Task 7)
tapestry/src/lib/data.ts              + meta.asOf on the raw type                 (Task 7)
tapestry/src/App.tsx                  + Systems/Chronicle tabs and routing        (Tasks 4,7)

tapestry/src/views/systems/systems.ts        buildCausalGraph, loop/leverage/flow (Task 3)
tapestry/src/views/systems/systems.test.ts                                        (Task 3)
tapestry/src/views/systems/Systems.tsx       Sigma causal-loop view               (Task 4)
tapestry/src/views/systems/Systems.css                                            (Task 4)
tapestry/src/views/systems/LoopPanel.tsx     reinforcing/balancing loop list      (Task 4)

tapestry/src/views/chronicle/replay.ts       buildTimeline, stateAt, diffStates   (Task 6)
tapestry/src/views/chronicle/replay.test.ts                                       (Task 6)
tapestry/src/views/chronicle/Chronicle.tsx   Sigma replay view                    (Task 7)
tapestry/src/views/chronicle/Chronicle.css                                        (Task 7)
tapestry/src/views/chronicle/Scrubber.tsx    time slider + play                   (Task 7)
tapestry/src/views/chronicle/EventList.tsx   event stream + jump-to               (Task 7)

tapestry/src/design/tokens.css        + --polarity-* / --loop-* / diff tokens     (Tasks 4,8)
tapestry/e2e/smoke.spec.ts            + Systems/Chronicle coverage                 (Task 9)
```

## Phase roadmap (remaining plans, one document each)

- **Phase 3 — Semantic Map:** UMAP upgrade (optional `viz-umap` dependency
  group; PCA stays the fallback), cluster hulls from `find-clusters`, lasso
  brushing into Explorer, `scope.mode: "search"`.
- **Phase 4 — Live mode:** `serve` command (optional `viz-serve` group: FastAPI
  + uvicorn), REST endpoints `/api/bundle|graphs|neighbors|search|as-of|
  entity/{id}`, live data source in the SPA, live ego-expand.
- **Phase 5 — Polish:** saved-view management UI, full a11y/keyboard audit,
  export refinements, 50k-node performance hardening.

---

### Task 1: `asOf` bi-temporal bundle parameter

Add a system-time bound to the assembler. When set, entities are reconstructed
through the store's real `read_entity_as_of` path, relations are pruned to those
that existed at that instant, the shipped event log truncates to `asOf`, and
`meta.asOf` records the bound.

**Files:**
- Modify: `theloom/viz/schema.py` (add `TapestryMeta.as_of`)
- Modify: `theloom/viz/scope.py` (`_docs` + `resolve_scope` take `as_of`)
- Modify: `theloom/viz/temporal.py` (`assemble_temporal` takes `as_of`)
- Modify: `theloom/viz/bundle.py` (`ExportBundleInput.as_of`, validation, wiring)
- Regenerate: `tapestry/schema/bundle.schema.json`, `COMMANDS.md`
- Test: `tests/test_viz_asof.py`

**Interfaces (real signatures — verified in the Phase 1 code):**
- `FalkorGraphStore.read_entity_as_of(entity_id: str, timestamp: str) -> Entity |
  None` (`theloom/store/falkor.py:156`). Returns the live doc when
  `tx_from <= timestamp`, else the `_EntityVersion` snapshot valid at that
  instant (`tx_from <= t < tx_to`), else `None`. String comparison on canonical
  ISO timestamps.
- `iso_now() -> str` (`theloom/timeutil.py`) — `YYYY-MM-DDTHH:MM:SS.mmmZ`,
  byte-comparable / lexicographically ordered.
- Relation wire docs carry `created_at` (snake — the model docstring notes
  `created_at`/`updated_at` are the only non-camel wire fields) and `from`/`to`.
- Known limitation to preserve, not fix here: `delete_entity` (`falkor.py:197`)
  does not write a final `_EntityVersion` snapshot, so a hard-deleted entity's
  last incarnation is not `read_entity_as_of`-recoverable. Reconstruction is
  therefore over currently-existing entities (the dominant case — invalidation
  in Loom is a status change, not a delete). Analytics/semantic remain
  whole-graph/current (they already are scope-independent in Phase 1); `asOf`
  bounds entities, relations, and the event log only. State this in the
  `assemble_bundle` docstring.

- [ ] **Step 1: Write the failing test**

`tests/test_viz_asof.py`:

```python
"""asOf bi-temporal bound — the as-of entity snapshot agrees with
read_entity_as_of, relations prune to survivors, temporal truncates."""

from __future__ import annotations

import time

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.viz.bundle import ExportBundleInput, assemble_bundle


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_as_of_shows_prior_incarnation(multi: MultiGraph) -> None:
    store = multi.get_store()
    entity = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    time.sleep(0.01)
    pivot = iso_now()  # strictly after create, strictly before the update
    time.sleep(0.01)
    store.update_entity(entity.id, {"name": "a2"})

    now_doc = assemble_bundle(ExportBundleInput(), multi)
    assert now_doc["entities"][0]["name"] == "a2"
    assert "asOf" not in now_doc["meta"]

    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    assert as_of_doc["entities"][0]["name"] == "a"
    assert as_of_doc["meta"]["asOf"] == pivot


def test_as_of_prunes_relations_and_entities_created_later(multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )

    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    assert {e["name"] for e in as_of_doc["entities"]} == {"a"}  # b not yet born
    assert as_of_doc["relations"] == []  # its only edge references the unborn b
    assert as_of_doc["meta"]["relationCount"] == 0


def test_as_of_truncates_temporal_events(multi: MultiGraph) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "concept", "observations": []})
    )
    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    types = [e["type"] for e in as_of_doc["temporal"]["events"]]
    assert types == ["entity_created"]  # only the first create is at/before pivot


def test_malformed_as_of_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        assemble_bundle(ExportBundleInput.model_validate({"asOf": "not-a-timestamp"}), multi)
    assert err.value.code == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_asof.py -v`
Expected: FAIL — `asOf` is not accepted / `meta.asOf` absent.

- [ ] **Step 3: Write the implementation**

`theloom/viz/schema.py` — add the field to `TapestryMeta` (after `theme`):

```python
    theme: str | None = None
    as_of: str | None = Field(default=None, alias="asOf")
```

`theloom/viz/scope.py` — thread `as_of` through `_docs` and `resolve_scope`; add a
small ISO parser used for the relation cutoff:

```python
from datetime import datetime


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _docs(store: FalkorGraphStore, as_of: str | None = None) -> tuple[list[Doc], list[Doc]]:
    if as_of is None:
        entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
    else:
        entities = []
        for entity in store.list_entities():
            snapshot = store.read_entity_as_of(entity.id, as_of)
            if snapshot is not None:  # None ⇒ not yet created at `as_of`
                entities.append(snapshot.model_dump(by_alias=True, exclude_unset=True))
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    if as_of is not None:
        entity_ids = {e["id"] for e in entities}
        cutoff = _parse_iso(as_of)
        relations = [
            r
            for r in relations
            if r["from"] in entity_ids
            and r["to"] in entity_ids
            and _parse_iso(r["created_at"]) <= cutoff
        ]
    return entities, relations
```

Add `as_of: str | None = None` to `resolve_scope`'s signature and pass it into
the single `_docs(store)` call (`entities, relations = _docs(store, as_of)`).
Scope extractors then run on the as-of doc sets exactly as before — ego/causal/
typed slicing is correct because it operates on whatever `_docs` returned.

`theloom/viz/temporal.py` — truncate to `as_of`:

```python
def assemble_temporal(
    graph: str | None, multi: MultiGraph, as_of: str | None = None
) -> TemporalSection:
    events = []
    for event in multi.event_log(graph).read_all():
        at = _stream_id_to_iso(event.id)
        if as_of is not None and at > as_of:
            continue
        events.append(TemporalEvent(id=event.id, at=at, type=event.type, payload=event.payload))
    return TemporalSection(events=events)
```

(`_stream_id_to_iso` and `as_of` are both canonical UTC ISO; string `>` is a
correct instant comparison here.)

`theloom/viz/bundle.py` — add the input field, validate, and wire it through:

```python
from theloom.errors import NotFoundError, ValidationError
# ...

class ExportBundleInput(CommandInput):
    graph: str | None = None
    scope: ScopeInput = Field(default_factory=ScopeInput)
    include: IncludeInput = Field(default_factory=IncludeInput)
    title: str | None = None
    as_of: str | None = Field(default=None, alias="asOf")
```

In `assemble_bundle` (keep the existing NOT_FOUND check), after resolving
`target`:

```python
    as_of = params.as_of
    if as_of is not None:
        try:
            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(
                f"Invalid asOf timestamp: '{as_of}'. Expected ISO 8601 (e.g. 2026-07-01T00:00:00Z)."
            ) from exc

    entities, relations, scope_label = resolve_scope(
        params.scope, multi.get_store(target), as_of=as_of
    )
    # analytics/semantic stay whole-graph/current (scope-independent in phase 1);
    # asOf bounds entities, relations, and the event log only.
    analytics = assemble_analytics(target, multi) if params.include.analytics else None
    temporal = assemble_temporal(target, multi, as_of=as_of) if params.include.temporal else None
    semantic = assemble_semantic(target, multi) if params.include.semantic else None
```

and add `asOf=as_of` (alias kwarg) to the `TapestryMeta(...)` construction. Add
`from datetime import datetime` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_asof.py -v`
Expected: 4 passed.

- [ ] **Step 5: Regenerate schema + docs, gates, commit**

```bash
uv run python -m theloom.viz.schema        # rewrites tapestry/schema/bundle.schema.json
uv run loom --generate-docs > COMMANDS.md  # registry-derived; no-op if unchanged
uv run pytest tests/test_viz_schema_drift.py tests/test_generate_docs.py -v
```

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass. (`tapestry/src/lib/schema.test.ts` still passes — the dev
fixture has no `asOf`, and `asOf` is optional in the regenerated schema.)

```bash
git add theloom/viz/ tests/test_viz_asof.py tapestry/schema/bundle.schema.json COMMANDS.md
git commit -m "Add asOf bi-temporal bound to the visualization bundle"
```

---

### Task 2: Enrich the dev fixture (leverage points + status history)

Phase 2's Systems view needs leverage points (Phase 1's fixture ships zero) and
Chronicle's replay/diff needs an `entity_status_changed` event and a
non-`active` status. Enrich the live `tapestry-dev` graph, then re-export the
committed fixture.

**Files:**
- Modify (re-export): `tapestry/fixtures/dev-bundle.json`

**Interfaces (real command/handler shapes):**
- A leverage point is simply an entity with `entityType: "leverage_point"` whose
  observations include `level: <1-12>` (parsed by
  `theloom/graph/metadata.py::parse_leverage_point_observations` into
  `_metadata.level` / `depthCategory` / `meadowsName`), linked to its target by a
  `part_of` relation **from the leverage_point to the target**
  (`list_leverage_points` reads `leverage_point --part_of--> target`).
- A non-`active` status is set via `update-entity` with a `status` field, which
  the store validates against `VALID_TRANSITIONS` and records as an
  `entity_status_changed` event (`falkor.py:193`).

- [ ] **Step 1: Enrich the live `tapestry-dev` graph**

Identify a causal variable to target and a claim to deprecate:

```bash
uv run loom list-entities '{"graph":"tapestry-dev","filter":{"entityType":"variable"}}'
uv run loom list-entities '{"graph":"tapestry-dev","filter":{"entityType":"claim"}}'
```

Pick one variable id (`$VAR`) and one claim id (`$CLAIM`). Then:

```bash
# A Meadows level-6 leverage point (information flows) targeting the variable.
uv run loom create-entity '{"graph":"tapestry-dev","name":"Feedback transparency",
  "entityType":"leverage_point",
  "observations":["level: 6","depthCategory: shallow",
    "The strength of the balancing signal depends on how visible resource depletion is."]}'
# → note the new id ($LP)

uv run loom create-relation '{"graph":"tapestry-dev","from":"'$LP'","to":"'$VAR'",
  "relationType":"part_of","strength":"strong","evidence":"Leverage analysis"}'

# Deprecate a claim → emits entity_status_changed + sets status:"deprecated".
uv run loom update-entity '{"graph":"tapestry-dev","id":"'$CLAIM'","status":"deprecated",
  "statusReason":"outdated_knowledge"}'
```

- [ ] **Step 2: Re-export and sanity-check the fixture**

```bash
uv run loom export-bundle '{"graph":"tapestry-dev"}' > tapestry/fixtures/dev-bundle.json
python3 -c "import json; b=json.load(open('tapestry/fixtures/dev-bundle.json')); \
print('leverage', len(b['analytics']['leveragePoints'])); \
print('statuses', sorted({e.get('status','active') for e in b['entities']})); \
print('event types', sorted({e['type'] for e in b['temporal']['events']}))"
```

Expected: `leverage 1`; a `deprecated` among statuses; `entity_status_changed`
among event types. Confirm the loop is still present
(`b['analytics']['loops']` non-empty).

- [ ] **Step 3: Verify the fixture still conforms and nothing regressed**

Run: `cd tapestry && npm test` (schema.test.ts validates the enriched fixture
against the committed schema) `&& cd .. && uv run pytest tests/test_cli_viz_commands.py`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tapestry/fixtures/dev-bundle.json
git commit -m "Enrich the Tapestry dev fixture with a leverage point and status history"
```

---

### Task 3: Systems view — causal-loop model helpers (pure)

The pure functions the Systems view renders from: a causal-only subgraph, a
loop's ordered edge keys, leverage-point targets, and the flow-animation
intensity curve. No Sigma, no DOM — unit-tested in isolation.

**Files:**
- Create: `tapestry/src/views/systems/systems.ts`
- Test: `tapestry/src/views/systems/systems.test.ts`

**Interfaces:**
- Consumes: `TapestryBundleRaw` (`lib/data.ts`), `edgeFamily` /
  `resolveTypeColor` (`views/explorer/buildGraph.ts`).
- Produces:
  - `buildCausalGraph(bundle): Graph` — directed multigraph of only causal
    entities/relations (relation kept when `edgeFamily(relationType) ===
    "causal"`; endpoints added as nodes with `label`, `entityType`, `color`,
    seeded `x`/`y`); edge attrs `{relationType, polarity, strength, size, color,
    type: "arrow"}`. Dangling causal relations skipped (mirror `buildGraph`).
  - `loopEdgeKeys(loop: LoopInfo, graph: Graph): string[]` — for a loop whose
    `path` is `[a,b,c,a]`, the ordered directed edge keys `a→b, b→c, c→a`
    (first match via `graph.edges(from, to)`), skipping any pair with no edge.
  - `leverageTargets(bundle): Map<string, LeverageMark>` — `targetEntityId →
    {level, meadowsName, pointName}` from `analytics.leveragePoints` (each a
    leverage_point entity doc + `_metadata`) resolved through the
    `leverage_point --part_of--> target` relation in `bundle.relations`.
  - `flowIntensity(index: number, count: number, phase: number): number` — a
    traveling pulse in `[0,1]`: a raised-cosine bump centered on
    `phase * count`, wrapping around the cycle, so exactly one edge peaks at a
    time and neighbors glow. Used by the animation rAF (Task 5).
  - Types: `LoopInfo` (mirrors the bundle loop shape:
    `{id, name, classification: "reinforcing"|"balancing", netPolarity, memberCount,
    path: string[], memberIds: string[]}`), `LeverageMark`.

- [ ] **Step 1: Write the failing test**

```typescript
import Graph from "graphology";
import { describe, expect, it } from "vitest";
import {
  buildCausalGraph,
  flowIntensity,
  leverageTargets,
  loopEdgeKeys,
  type LoopInfo,
} from "./systems";
import type { TapestryBundleRaw } from "../../lib/data";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 4, relationCount: 4, sections: [] },
  entities: [
    { id: "a", name: "A", entityType: "variable" },
    { id: "b", name: "B", entityType: "variable" },
    { id: "c", name: "C", entityType: "variable" },
    { id: "lp", name: "Signal clarity", entityType: "leverage_point",
      _metadata: { level: 6, depthCategory: "shallow", meadowsName: "Information flows" } },
  ],
  relations: [
    { id: "e1", from: "a", to: "b", relationType: "causes", polarity: "+" },
    { id: "e2", from: "b", to: "c", relationType: "inhibits", polarity: "-" },
    { id: "e3", from: "c", to: "a", relationType: "causes", polarity: "+" },
    { id: "e4", from: "lp", to: "b", relationType: "part_of" }, // structural — not causal
  ],
} as unknown as TapestryBundleRaw;

const loop: LoopInfo = {
  id: null, name: "ABC Balancing Loop", classification: "balancing", netPolarity: "-",
  memberCount: 3, path: ["a", "b", "c", "a"], memberIds: ["a", "b", "c"],
};

describe("buildCausalGraph", () => {
  it("keeps only causal edges and their endpoints", () => {
    const g: Graph = buildCausalGraph(bundle);
    expect(g.order).toBe(3); // a, b, c — not the leverage point (no causal edge)
    expect(g.size).toBe(3); // e1, e2, e3 — not the part_of edge
    expect(g.getEdgeAttribute("e2", "polarity")).toBe("-");
    expect(g.hasNode("lp")).toBe(false);
  });
});

describe("loopEdgeKeys", () => {
  it("returns the loop's directed edge keys in path order", () => {
    expect(loopEdgeKeys(loop, buildCausalGraph(bundle))).toEqual(["e1", "e2", "e3"]);
  });
});

describe("leverageTargets", () => {
  it("maps each leverage point to its part_of target", () => {
    const marks = leverageTargets(bundle);
    expect(marks.get("b")).toMatchObject({ level: 6, pointName: "Signal clarity" });
    expect(marks.has("a")).toBe(false);
  });
});

describe("flowIntensity", () => {
  it("peaks on one edge and wraps around the cycle", () => {
    expect(flowIntensity(0, 3, 0)).toBeCloseTo(1, 5);
    expect(flowIntensity(0, 3, 1)).toBeCloseTo(1, 5); // phase 1 wraps back to edge 0
    expect(flowIntensity(1, 3, 0)).toBeLessThan(flowIntensity(0, 3, 0));
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd tapestry && npm test` → FAIL (module missing).

- [ ] **Step 3: Implement `systems.ts`**

Model the loop/leverage types after the real bundle shapes (Task 1/Phase 1).
`buildCausalGraph`: `new Graph({ multi: true, type: "directed" })`; first pass
collects causal relations (`edgeFamily(relationType) === "causal"`) and their
endpoint ids; add those entities as nodes (reuse `resolveTypeColor`, seed x/y via
the same deterministic hash approach used in `buildGraph.ts` — factor the seeder
out or re-implement the tiny FNV/mulberry pair); then add the causal edges,
skipping dangling ones. `loopEdgeKeys`: walk consecutive `path` pairs, take
`graph.edges(from, to)[0]`. `leverageTargets`: index `bundle.relations` by
`part_of` from each leverage_point id → its `to`; read `_metadata.level` /
`meadowsName` off each `analytics.leveragePoints` doc. `flowIntensity`: raised
cosine `0.5*(1+cos(2π·d))` where `d` is the wrapped circular distance between
`index` and `phase*count`, normalized so exactly one edge peaks.

- [ ] **Step 4: Run to verify pass** — `cd tapestry && npm test` → all pass.

- [ ] **Step 5: Gates + commit**

Run: `cd tapestry && npm test && cd .. && uv run pytest tests/test_cli_viz_commands.py`
(no build — no app source changed yet, only a new untested-by-build module set;
building here is harmless but not required until a view imports it in Task 4.)

```bash
git add tapestry/src/views/systems/systems.ts tapestry/src/views/systems/systems.test.ts
git commit -m "Add causal-loop model helpers for the Systems view"
```

---

### Task 4: Systems view — render, loop list, loop isolation

The Systems tab: a Sigma causal-loop diagram with polarity-encoded edges, a
reinforcing/balancing loop panel, and loop isolation (select a loop → dim the
rest). Animation and leverage markers land in Task 5.

**Load the `dataviz` and `frontend-design` skills before writing styles/encoding.**

**Files:**
- Create: `tapestry/src/views/systems/Systems.tsx`, `Systems.css`,
  `views/systems/LoopPanel.tsx`
- Modify: `tapestry/src/design/tokens.css` (+ `--polarity-positive` /
  `--polarity-negative`, `--loop-reinforcing` / `--loop-balancing`, both themes)
- Modify: `tapestry/src/state/store.ts` (View union + `selectedLoop`)
- Modify: `tapestry/src/App.tsx` (Systems tab + routing)

**Interfaces:**
- Consumes: `useBundle` / `useGraph` context, Task 3 `systems.ts`, the Explorer's
  `createLayout` (`views/explorer/layout.ts`), `resolveGraphColors` /
  `readVar` patterns.
- Store additions: `View = "explorer" | "overview" | "systems" | "chronicle"`
  (add both now to avoid re-touching the union in Task 7);
  `selectedLoop: string | number | null` + `selectLoop(id)`.

- [ ] **Step 1: Extend the store + tokens**

`store.ts`:
```typescript
export type View = "explorer" | "overview" | "systems" | "chronicle";
// ...in TapestryState:
selectedLoop: string | number | null;
selectLoop: (id: string | number | null) => void;
// ...in the creator:
selectedLoop: null,
selectLoop: (selectedLoop) => set({ selectedLoop }),
```

`tokens.css` — add (values chosen with the dataviz validator, both themes; these
are a diverging polarity channel and a two-class loop channel, never reused as
entity-type or status colors, always paired with a `+`/`−` glyph or `R`/`B`
badge):
```css
/* :root (light) */
--polarity-positive: <validated>;   --polarity-negative: <validated>;
--loop-reinforcing:  <validated>;   --loop-balancing:  <validated>;
/* [data-theme="dark"] — brightened counterparts */
```

Update `store.test.ts` with a case asserting `selectedLoop` defaults to `null`
and `selectLoop` sets it. Run `cd tapestry && npm test` to confirm the store
change is green.

- [ ] **Step 2: Implement the Systems view**

`Systems.tsx` — mirror the Explorer's lifecycle (instantiate Sigma over
`useMemo(() => buildCausalGraph(bundle), [bundle])`, run `createLayout` for
~2.5 s then freeze, rAF theme re-resolve, `readVar` fallbacks). Differences:
- **Edge encoding by polarity:** color each edge by
  `polarity === "-" ? --polarity-negative : --polarity-positive`; render a `+`/`−`
  glyph badge as a DOM overlay at each edge's viewport midpoint
  (`sigma.graphToViewport` of the two endpoints, averaged), updated on
  `afterRender`. Color is a redundant cue — the glyph carries polarity for CVD.
- **Loop isolation reducers:** hold `isolatedNodes` / `isolatedEdges` refs
  (Sets). When `selectedLoop` is set, resolve the `LoopInfo` from
  `bundle.analytics.loops` by id/name, fill the sets from `loop.memberIds` and
  `loopEdgeKeys(loop, graph)`; nodeReducer/edgeReducer dim (muted color, drop
  label) everything outside them, exactly like the Explorer's PATH layer. Empty
  selection ⇒ everything at full strength.
- **`LoopPanel`** (right rail): list `bundle.analytics.loops` with an `R`/`B`
  badge (`--loop-reinforcing` / `--loop-balancing`), the loop `name`, and
  `memberCount`; clicking a row calls `selectLoop`; the active row is
  highlighted; a "Clear" affffordance calls `selectLoop(null)`. When
  `analytics` or `loops` is absent, render an empty-state note
  ("Re-export with the analytics section to surface feedback loops.").
- **Empty causal graph:** when `buildCausalGraph` is empty, show a note
  ("No causal relations in this scope — the Systems view needs causes / enables /
  inhibits / … edges.").

`App.tsx` — add the Systems tab to `VIEWS`
(`{ id: "systems", label: "Systems", color: "var(--type-loop)" }`) and switch
the `app__main` body from the two-way ternary to a lookup that renders
`<Systems key="systems" />` for `view === "systems"` (keep Explorer/Overview;
Chronicle added in Task 7). All views must remain wrapped by `BundleProvider`
(they already are, via the Phase 1 shell).

- [ ] **Step 3: Verify** — `cd tapestry && npm test` → pass (store test).
  Manual: `npm run dev` → Systems tab shows the causal loop with signed edges;
  clicking a loop dims the rest; `+`/`−` glyphs track the edges through zoom/pan;
  both themes read correctly.

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
git commit -m "Add the Systems view with causal-loop rendering and loop isolation"
```

---

### Task 5: Systems view — animated flow + leverage markers

Animate signed flow around the isolated loop, and mark variables that carry a
leverage point with their Meadows level.

**Load the `dataviz` and `frontend-design` skills before touching encoding.**

**Files:**
- Modify: `tapestry/src/views/systems/Systems.tsx`, `Systems.css`

**Interfaces:**
- Consumes: Task 3 `flowIntensity`, `loopEdgeKeys`, `leverageTargets`.

- [ ] **Step 1: Implement flow animation**

Add an `animating` toggle (button in the Systems controls, default off). When
on and a loop is selected, run an rAF loop (mirror the Explorer's rAF discipline,
including cleanup on unmount / view switch) that advances a `phaseRef` (0→1 over
~2.5 s, wrapping). Hold the isolated loop's ordered edge keys in a ref; each
frame, set each loop edge's `size` and highlight to
`base + flowIntensity(i, count, phase) * boost` and `sigma.refresh()`. Direction
follows the loop path order (Task 3 returns edges in `a→b→c→a` order), so the
pulse visibly travels the way the loop's influence flows. Respect
`prefers-reduced-motion` — skip the rAF and render a static gradient along the
loop instead. Stop the rAF when animation is toggled off, no loop is selected,
or the view unmounts.

- [ ] **Step 2: Implement leverage markers**

Compute `const marks = useMemo(() => leverageTargets(bundle), [bundle])`. Render
a DOM badge overlay for each `marks` entry whose target node is in the causal
graph, positioned at the node's viewport point (updated on `afterRender`, like
the polarity glyphs): a small pill showing the Meadows `level` (and
`meadowsName` on hover/title), tinted by `--type-leverage_point`. A legend entry
explains the badge. Nodes without a leverage point get no badge.

- [ ] **Step 3: Verify** — `npm test` still green (no pure-logic change; the
  covered helpers already pass). Manual: `npm run dev` → toggling animation
  sends a pulse traveling around the selected loop; leverage badges sit on the
  right variables and track camera moves; reduced-motion shows the static
  variant.

- [ ] **Step 4: Build + confirm the sentinel** (as Task 4 Step 4).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Animate signed flow and mark leverage points in the Systems view"
```

---

### Task 6: Chronicle — temporal replay engine (pure)

The pure engine behind the scrubber and diff mode: a timeline built from
`temporal.events`, an as-of state projection, and a two-instant diff. This is
`read_entity_as_of` semantics reimplemented client-side over the event log
(exactly what the roadmap calls for). No Sigma, no DOM.

**Files:**
- Create: `tapestry/src/views/chronicle/replay.ts`, `replay.test.ts`

**Interfaces (real event shapes — verified in `theloom/store/`):**
Event types and payloads: `entity_created {entity}`, `entity_updated
{entity, previous}`, `entity_status_changed {entity, previous}`, `entity_deleted
{entity}`, `relation_created {relation}`, `relation_updated {relation, previous}`,
`relation_deleted {relation}`, `entities_merged {primary, secondary, …}`. Each
`TapestryEventRaw` is `{id, at, type, payload}` with `at` a canonical ISO string.

- Produces:
  - `buildTimeline(bundle): Timeline` — one pass over `temporal.events` (`at`
    parsed to epoch ms via `Date.parse`), recording: `nodeCreated`,
    `nodeRemoved`, `edgeCreated`, `edgeRemoved` (id → ms), `nodeStatus` (id →
    `{t, status}[]` sorted, from `entity_status_changed`), `nodeUpdated` (id →
    ms[], from `entity_updated`), and a normalized, time-sorted
    `events: ChronicleEvent[]` (`{t, type, kind: "node"|"edge", id, label}`) for
    the event list. `start`/`end` are the min/max event ms (equal/degenerate
    handled).
  - `stateAt(timeline, graph, t): ChronicleState` —
    `{visibleNodes: Set, visibleEdges: Set, statusById: Map<string,string>}`. A
    node is visible when its creation ms `<= t` (or it has no creation record —
    imported/migrated entities emit no event, so treat "unknown creation" as
    present-from-start) and it is not removed at/before `t`. Effective status is
    the latest `nodeStatus` entry with `time <= t`, else `"active"`. An edge is
    visible when created `<= t`, not removed, and both endpoints visible.
  - `diffStates(timeline, t0, t1): Diff` — `{added, invalidated, changed}` sets
    of node ids over `(t0, t1]`: **added** = created in the window;
    **invalidated** = a `nodeStatus` change to a non-`active` status in the
    window; **changed** = a `nodeUpdated` in the window and not `added`.
  - Types `Timeline`, `ChronicleState`, `ChronicleEvent`, `Diff`.

- [ ] **Step 1: Write the failing test**

```typescript
import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { buildTimeline, diffStates, stateAt } from "./replay";
import type { TapestryBundleRaw } from "../../lib/data";

// Three instants: create a & b + edge (t=1000), update a (t=2000),
// deprecate b (t=3000).
const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 2, relationCount: 1, sections: ["temporal"] },
  entities: [
    { id: "a", name: "A", entityType: "concept", status: "active" },
    { id: "b", name: "B", entityType: "claim", status: "deprecated" },
  ],
  relations: [{ id: "e1", from: "a", to: "b", relationType: "supports" }],
  temporal: {
    events: [
      { id: "1000-0", at: "1970-01-01T00:00:01.000Z", type: "entity_created", payload: { entity: { id: "a" } } },
      { id: "1000-1", at: "1970-01-01T00:00:01.000Z", type: "entity_created", payload: { entity: { id: "b" } } },
      { id: "1000-2", at: "1970-01-01T00:00:01.000Z", type: "relation_created", payload: { relation: { id: "e1", from: "a", to: "b" } } },
      { id: "2000-0", at: "1970-01-01T00:00:02.000Z", type: "entity_updated", payload: { entity: { id: "a" } } },
      { id: "3000-0", at: "1970-01-01T00:00:03.000Z", type: "entity_status_changed", payload: { entity: { id: "b", status: "deprecated" } } },
    ],
  },
} as unknown as TapestryBundleRaw;

function currentGraph(): Graph {
  const g = new Graph({ multi: true, type: "directed" });
  g.addNode("a", {}); g.addNode("b", {});
  g.addEdgeWithKey("e1", "a", "b", {});
  return g;
}

describe("stateAt", () => {
  it("shows nothing before the first event", () => {
    const t = buildTimeline(bundle);
    const s = stateAt(t, currentGraph(), 500);
    expect(s.visibleNodes.size).toBe(0);
    expect(s.visibleEdges.size).toBe(0);
  });
  it("at the end equals the current graph and statuses", () => {
    const t = buildTimeline(bundle);
    const s = stateAt(t, currentGraph(), t.end);
    expect([...s.visibleNodes].sort()).toEqual(["a", "b"]);
    expect(s.visibleEdges.has("e1")).toBe(true);
    expect(s.statusById.get("b")).toBe("deprecated");
    expect(s.statusById.get("a") ?? "active").toBe("active");
  });
  it("b is still active at t=2500, deprecated at t=3000", () => {
    const t = buildTimeline(bundle);
    expect(stateAt(t, currentGraph(), 2500).statusById.get("b") ?? "active").toBe("active");
    expect(stateAt(t, currentGraph(), 3000).statusById.get("b")).toBe("deprecated");
  });
});

describe("diffStates", () => {
  it("classifies added / changed / invalidated across a window", () => {
    const t = buildTimeline(bundle);
    const d = diffStates(t, 1500, 3000);
    expect([...d.added]).toEqual([]);        // nothing created after 1500
    expect([...d.changed]).toEqual(["a"]);   // a updated at 2000
    expect([...d.invalidated]).toEqual(["b"]); // b deprecated at 3000
  });
  it("counts creations as added", () => {
    const t = buildTimeline(bundle);
    expect([...diffStates(t, 0, 1500).added].sort()).toEqual(["a", "b"]);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd tapestry && npm test` → FAIL.

- [ ] **Step 3: Implement `replay.ts`** per the interface. One pass builds the
  timeline maps and the normalized event list (label from
  `payload.entity?.id`/`payload.relation?.id`; ignore `entities_merged` /
  `relation_updated` for state effect but still record them in `events` so the
  stream is complete). `stateAt` and `diffStates` are pure map/set lookups.
  Guard degenerate `start === end` (single instant) so the scrubber range is
  non-empty (`end = start + 1` when equal).

- [ ] **Step 4: Run to verify pass** — `cd tapestry && npm test` → all pass.

- [ ] **Step 5: Gates + commit**

Run: `cd tapestry && npm test && cd .. && uv run pytest tests/test_cli_viz_commands.py`

```bash
git add tapestry/src/views/chronicle/replay.ts tapestry/src/views/chronicle/replay.test.ts
git commit -m "Add the Chronicle temporal replay engine"
```

---

### Task 7: Chronicle view — scrubber, play, event stream

The Chronicle tab: reuse the shared graph, drive per-time visibility/styling from
the replay engine, scrub or play through construction, and list the events with
jump-to. Diff mode is Task 8.

**Load the `dataviz` and `frontend-design` skills before writing styles.**

**Files:**
- Create: `tapestry/src/views/chronicle/Chronicle.tsx`, `Chronicle.css`,
  `chronicle/Scrubber.tsx`, `chronicle/EventList.tsx`
- Modify: `tapestry/src/state/store.ts` (time state), `state/urlHash.ts`
  (time in the hash), `lib/data.ts` (`meta.asOf`), `App.tsx` (Chronicle tab)

**Interfaces:**
- Consumes: `useBundle` / `useGraph`, Task 6 `replay.ts`, `createLayout`,
  `resolveGraphColors` / `readVar`.
- Store additions: `time: number | null` (scrubber position, epoch ms; `null` ⇒
  end/current), `playing: boolean`, plus `setTime`, `setPlaying`.
- `urlHash`: extend `HashState` with optional `time?: number | null` so a
  Chronicle position deep-links (spec: "time position serialize into the URL
  hash"); `applyHash` pushes it via `setTime`. The existing round-trip test must
  still pass — add a new case for `time` rather than changing the old one.
- `lib/data.ts`: add `asOf?: string` to the `meta` type (surfaced by the header
  when present — "as of <time>").

- [ ] **Step 1: Extend store + hash (with a test)**

Add `time`/`playing`/setters to `store.ts`. Extend `urlHash.ts`
`serializeState`/`parseHash`/`applyHash` to carry `time`. Add a `urlHash.test.ts`
case:
```typescript
it("round-trips a chronicle time position", () => {
  const state = { view: "chronicle" as const, selection: null,
    filters: { entityTypes: [], relationTypes: [], confidenceMin: 0, statuses: [] }, time: 1720000000000 };
  expect(parseHash(serializeState(state))).toEqual(state);
});
```
Run `cd tapestry && npm test` → the new store/hash tests pass (view is now a
valid union member from Task 4).

- [ ] **Step 2: Implement the Chronicle view**

`Chronicle.tsx` — reuse `useGraph()` (the same graphology model the Explorer
renders, so the weave you watch assemble is the real one). Instantiate Sigma,
run `createLayout` ~2 s then freeze (positions carry over if Explorer already
settled them). Compute `const timeline = useMemo(() => buildTimeline(bundle),
[bundle])`. Hold a `stateRef` and recompute `stateAt(timeline, graph, t)`
whenever `time` changes (default `t = timeline.end`), then `sigma.refresh()`.
Reducers (mirror the Explorer's layered reducers):
- node/edge not in `visibleNodes`/`visibleEdges` ⇒ `hidden: true` (not yet born
  at `t`);
- a visible node whose `statusById` is non-`active` ⇒ styled as invalidated
  (dimmed + a small status badge/ring, reusing the status token treatment the
  detail panel uses), so replaying past a deprecation visibly restyles the node;
- optionally, a node created within a small trailing window of `t` gets a brief
  "just appeared" highlight.

`Scrubber.tsx` — a range input over `[timeline.start, timeline.end]` bound to
`time` (with a readable current-instant label). A play/pause button advances
`time` via rAF from the current position to `end` over ~6 s (respect
`prefers-reduced-motion`: step discretely per event instead of smoothly). Pausing
or reaching the end stops the rAF; changing tabs unmounts and cleans it up.

`EventList.tsx` — render `timeline.events` (type icon, humanized label, relative
instant); the row at/near `time` is highlighted; clicking a row sets `time` to
that event's `t` (jump-to). Virtualize only if needed — the fixture is tiny;
correctness first.

`App.tsx` — add the Chronicle tab (`{ id: "chronicle", label: "Chronicle",
color: "var(--type-event)" }`) and route `view === "chronicle"` to
`<Chronicle key="chronicle" />`. Surface `meta.asOf` in the header when present.

- [ ] **Step 3: Verify** — `cd tapestry && npm test` → pass. Manual:
  `npm run dev` → Chronicle scrubs from empty to full; nodes/edges appear at
  their creation instants; scrubbing past the deprecation restyles that node;
  play animates the build; clicking an event jumps the scrubber; both themes read.

- [ ] **Step 4: Build + confirm the sentinel** (as Task 4 Step 4).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add the Chronicle view with a time scrubber and event stream"
```

---

### Task 8: Chronicle — diff mode

Compare two instants and color-code what changed between them.

**Load the `dataviz` and `frontend-design` skills before writing the diff encoding.**

**Files:**
- Modify: `tapestry/src/views/chronicle/Chronicle.tsx`, `Chronicle.css`,
  `state/store.ts` (`diffAnchor`)
- Modify: `tapestry/src/design/tokens.css` (diff channel, if not reusing status
  tokens)

**Interfaces:**
- Consumes: Task 6 `diffStates`.
- Store: `diffAnchor: number | null` (the first-picked instant; `null` ⇒ diff
  off) + `setDiffAnchor`.

- [ ] **Step 1: Extend the store** — add `diffAnchor` + setter (small store
  test case: defaults `null`, setter works). `cd tapestry && npm test` green.

- [ ] **Step 2: Implement diff mode**

Add a "Diff" toggle in the Chronicle controls. Turning it on captures
`diffAnchor = time`; the scrubber then picks the second instant. Compute
`const diff = diffStates(timeline, min(a,b), max(a,b))` and swap the reducers to
the diff layer: **added** nodes → `--color-good` ring/fill accent, **invalidated**
→ `--color-critical`, **changed** → `--color-warning` (the reserved dataviz
status tokens, which is exactly their purpose — never entity-type or polarity
hues), each paired with a legend chip and an icon so the encoding is not
color-alone. Nodes outside all three sets render neutral (present but unchanged).
A compact diff summary bar shows the two instants and the three counts. Turning
diff off (or clearing) restores the plain time-replay reducers from Task 7.

- [ ] **Step 3: Verify** — `npm test` green (diff logic already covered in Task
  6). Manual: pick an anchor, move the scrubber → added/invalidated/changed nodes
  light up in their categories; the summary counts match; toggling off returns to
  replay; both themes read with legible contrast.

- [ ] **Step 4: Build + confirm the sentinel** (as Task 4 Step 4).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build`

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add Chronicle diff mode comparing two instants"
```

---

### Task 9: E2E smoke coverage + docs

Extend the Playwright smoke suite to boot the two new tabs, and update the docs.
CI needs no change — the Phase 1 `tapestry` job already runs `npm test`, rebuilds,
asserts template freshness (`git diff --exit-code theloom/viz/static/tapestry.html`),
and runs `npm run e2e`.

**Files:**
- Modify: `tapestry/e2e/smoke.spec.ts`
- Modify: `README.md` (Visualization section — Systems + Chronicle), `CLAUDE.md`
  layout block only if it lists views (leave `COMMANDS.md` to its generator)

- [ ] **Step 1: Extend the e2e smoke test**

Add cases to `smoke.spec.ts` (reuse the existing `beforeAll` that injects the
committed dev fixture into the committed template and opens it via `file://`):

```typescript
test("systems tab shows the causal loop and isolates it", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Systems" }).click();
  const panel = page.locator("#panel-systems");
  await expect(panel).toBeVisible();
  await expect(panel.locator("canvas").first()).toBeVisible(); // sigma mounted
  // The enriched fixture ships one balancing loop — its row is clickable.
  await expect(panel.getByText(/balancing/i).first()).toBeVisible();
});

test("chronicle tab scrubs the graph's construction", async ({ page }) => {
  await page.goto("file://" + OUT);
  await page.getByRole("tab", { name: "Chronicle" }).click();
  const panel = page.locator("#panel-chronicle");
  await expect(panel).toBeVisible();
  await expect(panel.locator("canvas").first()).toBeVisible();
  await expect(panel.getByRole("slider")).toBeVisible(); // the time scrubber
});
```

(Use the real panel ids `#panel-systems` / `#panel-chronicle` and the real tab
labels from `App.tsx`'s `VIEWS`; adjust the loop/slider locators to the markup
you shipped — verify against the running dev build, not from memory.)

Run: `cd tapestry && npx playwright install chromium && npm run build && npm run e2e`
Expected: all smoke tests pass (including the Phase 1 Explorer/Overview/path-mode
cases).

- [ ] **Step 2: Docs**

README Visualization section: add short paragraphs for the Systems view (causal
loops, R/B classification, isolation + animated flow, leverage markers) and the
Chronicle view (scrubber replay, diff mode), plus a note on `asOf`
(`uv run loom export-bundle '{"asOf":"2026-07-01T00:00:00Z"}'`). Capture real
screenshots of the two views against the dev fixture
(`docs/images/tapestry-systems.png`, `docs/images/tapestry-chronicle.png`) — no
placeholders. Verify `COMMANDS.md` is current
(`uv run pytest tests/test_generate_docs.py`).

- [ ] **Step 3: Full gates**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest && cd tapestry && npm test && npm run build && npm run e2e`
Expected: everything passes; sentinel count in the committed template is 1.

- [ ] **Step 4: Commit**

```bash
git add tapestry/e2e/ README.md CLAUDE.md docs/images/
git commit -m "Cover Systems and Chronicle with smoke tests and docs"
```

---

## Plan self-review notes

- **Spec coverage (Phase 2 scope).** Systems view: causal-loop rendering ✓ (T3
  `buildCausalGraph`, T4 render), polarity-encoded edges with +/− glyphs ✓ (T4),
  reinforcing/balancing loop list from `analytics.loops` ✓ (T4 `LoopPanel`), loop
  isolation ✓ (T4 reducers), animated signed flow ✓ (T5 `flowIntensity` + rAF),
  Meadows leverage markers from `analytics.leveragePoints` ✓ (T5). Chronicle:
  scrubber replaying `temporal.events` ✓ (T6 `buildTimeline`/`stateAt`, T7
  Scrubber), play ✓ (T7), event stream with jump-to ✓ (T7 `EventList`),
  entity_updated/status_changed restyle ✓ (T7 status layer), diff mode
  added/invalidated/changed ✓ (T8 `diffStates`). Bi-temporal `asOf` bundle param
  reusing `read_entity_as_of` ✓ (T1), and its client-side counterpart is exactly
  Chronicle's replay engine (T6). URL-hash time position ✓ (T7).
- **Reuse, not reinvention.** Systems and Chronicle both build on the committed
  Phase 1 substrate: the same `TapestryBundle` (loops + leveragePoints + temporal
  already ship — no new analytics needed, matching the roadmap), the same
  `BundleContext` graph, `createLayout`, `resolveGraphColors`/`readVar`, the
  layered-reducer + rAF-theme patterns, and the status tokens. No new Python or
  frontend dependency.
- **`asOf` boundary, stated honestly (T1).** `asOf` bounds entities (via the real
  `read_entity_as_of` path), relations (survivor + `created_at` cutoff), and the
  event log. Analytics/semantic stay whole-graph/current — consistent with Phase
  1, where analytics is already scope-independent. Hard-deleted entities are not
  as-of-recoverable because `delete_entity` writes no final version snapshot; the
  plan documents this rather than silently mis-reconstructing. Full as-of
  analytics (replaying into an ephemeral graph) is intentionally deferred.
- **Spec vs roadmap — no contradictions found.** The spec's Systems section adds
  "influence propagation — select a node and watch signed influence ripple
  downstream (semiring distances)"; the Phase 1 roadmap's Phase 2 line lists
  causal-loop view / isolation / animation / leverage markers and does not name
  influence propagation. Treated as an optional stretch, not required here — it
  needs a semiring-distance section the bundle does not yet ship, so it is left
  out of Phase 2 scope (a candidate for Phase 4/5 or a bundle-analytics
  extension). The spec's Chronicle "event-stream lane chart (mutation activity by
  type over time)" is likewise a nice-to-have beyond the roadmap's "time scrubber
  + diff mode + event list"; the event list (T7) satisfies the roadmap, and the
  lane chart can be folded into T7's `EventList` or a Phase 5 polish pass. Diff
  mode's spec wording "added / invalidated / changed" is adopted verbatim as the
  three diff categories (T8), resolving the roadmap's looser "added/removed/
  changed" — "invalidated" is the correct Loom term because invalidation is a
  status transition, not a hard delete.
- **Ordering is independently green.** T1 (Python+schema) and T2 (fixture) are
  backend-only; T3 and T6 are pure-logic (vitest-only); T4/T5/T7/T8 are view
  tasks that each rebuild the template and stay green; T9 is smoke+docs. Adding
  both `"systems"` and `"chronicle"` to the `View` union in T4 avoids re-editing
  it in T7. Every task ends with all gates green and one commit that includes the
  rebuilt `theloom/viz/static/tapestry.html` whenever the frontend changed.
