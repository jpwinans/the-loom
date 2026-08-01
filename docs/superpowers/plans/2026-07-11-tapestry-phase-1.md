# Tapestry Phase 1 Implementation Plan — Foundation + Graph Explorer + Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `loom visualize` / `loom export-bundle` — a self-contained, offline, interactive HTML visualization (Graph Explorer + Overview dashboard) of any Loom graph, per the approved spec at `docs/superpowers/specs/2026-07-11-loom-visualization-design.md`.

**Architecture:** A new `theloom/viz/` subpackage assembles a versioned **TapestryBundle** JSON from existing operations, then injects it into a committed, Vite-built single-file SPA template (`theloom/viz/static/tapestry.html`). The frontend lives in a new `tapestry/` workspace (Vite + React + TypeScript, sigma.js v3 WebGL rendering, graphology graph model). CLI stays JSON-out: HTML goes to a file, the command returns `{path, ...}`.

**Tech Stack:** Python 3.11+/Pydantic v2/Typer (existing) · numpy (PCA) · Vite · React 18 · TypeScript · zustand · sigma.js v3 · graphology (+ layout-forceatlas2, communities-louvain, shortest-path) · fuse.js · vite-plugin-singlefile · Vitest · Playwright.

## Prerequisites (fresh environment)

- `uv sync` (installs the Python venv), `docker compose up -d falkordb` (tests connect to the live store — nothing is mocked; `uv run loom init` if the default graph is new).
- Node.js 22+ and npm for the `tapestry/` workspace (contributor-only; end users consume the committed template).
- The approved design spec is `docs/superpowers/specs/2026-07-11-loom-visualization-design.md` — read it before starting.

## Global Constraints

- Python `>=3.11`; `uv run mypy --strict theloom`, `uv run ruff check .`, `uv run ruff format .`, `uv run pytest` must pass at every commit (CLAUDE.md: keep main green).
- CLI protocol: JSON in / JSON out on stdout; errors as `{error, code}` on stderr, exit 1; codes only from `theloom/errors.py`. Never classify errors by prose matching.
- Commands are declared only in `theloom/cli/registry.py`; regenerate `COMMANDS.md` with `uv run loom --generate-docs > COMMANDS.md` after registry changes (never hand-edit).
- Wire names are camelCase via Pydantic aliases; internal Python is snake_case; serialize with `model_dump(by_alias=True, exclude_unset=True)`.
- No new store, no file locks (architecture invariant 1). The viz layer only reads through existing store/operations APIs.
- New Python runtime dependency allowed in this phase: `numpy>=1.26` only (already transitive via scipy; made explicit). Node/npm is a **contributor-only** dependency — end users consume the committed built template.
- Frontend: zero CDN/network dependencies in built output; all JS/CSS inlined by vite-plugin-singlefile.
- Tests hit live FalkorDB via the `namespace` fixture pattern in `tests/conftest.py` (per-test prefix, auto-teardown). CLI-level tests go through `run_handler(name, input, multi)`.
- Commit messages: plain imperative, no AI/tool references, no co-authors (user convention).
- UI implementation tasks MUST load the `dataviz` and `frontend-design` skills before writing styles/chart code.

## File Structure (Phase 1 end state)

```
theloom/viz/__init__.py            empty package marker
theloom/viz/schema.py              TapestryBundle Pydantic models + JSON Schema export
theloom/viz/scope.py               scope resolution (full|ego|causal|typed)
theloom/viz/analytics.py           analytics section assembly (reuses operations)
theloom/viz/temporal.py            temporal section from the event log
theloom/viz/semantic.py            2D projection (PCA) from entity vectors
theloom/viz/bundle.py              assemble_bundle() — the one entry point
theloom/viz/html.py                template load + JSON injection + file write
theloom/viz/static/tapestry.html   built SPA template (committed artifact)
theloom/store/multigraph.py        + event_log() accessor (small addition)
theloom/cli/registry.py            + Visualization category (visualize, export-bundle)
tapestry/                          frontend workspace (see Task 8)
tests/test_viz_schema.py           Task 1
tests/test_viz_scope.py            Task 2
tests/test_viz_analytics.py        Task 3
tests/test_viz_temporal.py         Task 4
tests/test_viz_semantic.py         Task 5
tests/test_viz_bundle.py           Task 6
tests/test_viz_html.py             Task 7
tests/test_cli_viz_commands.py     Task 7
```

## Phase roadmap (later plans, one document each)

- **Phase 2 — Systems + Chronicle:** causal-loop view, loop isolation/animation, leverage markers; time scrubber replaying `temporal.events`, diff mode, `asOf` bundle param (uses `read_entity_as_of` semantics client-side).
- **Phase 3 — Semantic Map:** UMAP upgrade (optional `viz-umap` dependency group; PCA stays the fallback), cluster hulls from `find-clusters`, lasso brushing into Explorer, `scope.mode: "search"`.
- **Phase 4 — Live mode:** `serve` command (optional `viz-serve` group: FastAPI + uvicorn), REST endpoints `/api/bundle|graphs|neighbors|search|as-of|entity/{id}`, live data source in the SPA.
- **Phase 5 — Polish:** saved-view management UI, full a11y/keyboard audit, export refinements, 50k-node performance hardening.

---

### Task 1: TapestryBundle schema

**Files:**
- Create: `theloom/viz/__init__.py` (empty)
- Create: `theloom/viz/schema.py`
- Test: `tests/test_viz_schema.py`

**Interfaces:**
- Produces: `TapestryBundle`, `TapestryMeta`, `AnalyticsSection`, `TemporalSection`, `TemporalEvent`, `SemanticSection` (Pydantic, camelCase wire aliases); `SCHEMA_VERSION: int = 1`; `bundle_json_schema() -> dict[str, Any]`.
- Consumed by: Tasks 2–7 (assemblers, commands), Task 9 (schema drift test).

- [ ] **Step 1: Write the failing test**

```python
"""TapestryBundle schema tests — wire shape and JSON Schema export."""

from __future__ import annotations

from theloom.viz.schema import SCHEMA_VERSION, TapestryBundle, bundle_json_schema


def _minimal_bundle() -> TapestryBundle:
    return TapestryBundle.model_validate(
        {
            "schemaVersion": SCHEMA_VERSION,
            "meta": {
                "graph": "default",
                "scope": "full",
                "generatedAt": "2026-07-11T00:00:00Z",
                "entityCount": 0,
                "relationCount": 0,
                "sections": [],
            },
            "entities": [],
            "relations": [],
        }
    )


def test_minimal_bundle_round_trips_camel_case() -> None:
    bundle = _minimal_bundle()
    doc = bundle.model_dump(by_alias=True, exclude_none=True)
    assert doc["schemaVersion"] == 1
    assert doc["meta"]["generatedAt"] == "2026-07-11T00:00:00Z"
    assert "analytics" not in doc  # optional sections omitted when absent


def test_full_bundle_sections() -> None:
    bundle = TapestryBundle.model_validate(
        {
            "schemaVersion": 1,
            "meta": {
                "graph": "g",
                "title": "T",
                "scope": "ego:abc",
                "generatedAt": "2026-07-11T00:00:00Z",
                "entityCount": 1,
                "relationCount": 0,
                "sections": ["analytics", "temporal", "semantic"],
            },
            "entities": [{"id": "e1", "name": "N", "entityType": "concept"}],
            "relations": [],
            "analytics": {
                "centrality": {"degree": {"e1": 1.0}},
                "components": [["e1"]],
                "loops": [],
                "leveragePoints": [],
                "bridges": [],
            },
            "temporal": {
                "events": [
                    {
                        "id": "1720656000000-0",
                        "at": "2026-07-11T00:00:00+00:00",
                        "type": "entity_created",
                        "payload": {"entity": {"id": "e1"}},
                    }
                ]
            },
            "semantic": {"method": "pca", "projection": {"e1": [0.0, 0.0]}},
        }
    )
    assert bundle.analytics is not None
    assert bundle.temporal is not None and bundle.temporal.events[0].type == "entity_created"
    assert bundle.semantic is not None and bundle.semantic.method == "pca"


def test_json_schema_exports() -> None:
    schema = bundle_json_schema()
    assert schema["properties"]["schemaVersion"]
    assert "meta" in schema["properties"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'theloom.viz'`

- [ ] **Step 3: Write the implementation**

`theloom/viz/__init__.py` — empty file.

`theloom/viz/schema.py`:

```python
"""TapestryBundle — the versioned wire contract between the Python assembler
and the SPA. Entities/relations are the model's wire docs verbatim; sections
beyond them are optional and flag-controlled."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.model import LoomModel

SCHEMA_VERSION = 1


class TapestryMeta(LoomModel):
    graph: str
    title: str | None = None
    scope: str
    generated_at: str = Field(alias="generatedAt")
    entity_count: int = Field(alias="entityCount")
    relation_count: int = Field(alias="relationCount")
    sections: list[str]


class AnalyticsSection(LoomModel):
    centrality: dict[str, dict[str, float]]
    components: list[list[str]]
    loops: list[dict[str, Any]]
    leverage_points: list[dict[str, Any]] = Field(alias="leveragePoints")
    bridges: list[dict[str, Any]]


class TemporalEvent(LoomModel):
    id: str
    at: str
    type: str
    payload: dict[str, Any]


class TemporalSection(LoomModel):
    events: list[TemporalEvent]


class SemanticSection(LoomModel):
    method: str
    projection: dict[str, list[float]]


class TapestryBundle(LoomModel):
    schema_version: int = Field(alias="schemaVersion")
    meta: TapestryMeta
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    analytics: AnalyticsSection | None = None
    temporal: TemporalSection | None = None
    semantic: SemanticSection | None = None


def bundle_json_schema() -> dict[str, Any]:
    """JSON Schema of the wire shape (camelCase), committed for frontend drift tests."""
    return TapestryBundle.model_json_schema(by_alias=True)
```

Note: `LoomModel` (from `theloom.model`) is the shared alias-aware base the whole
model uses; check its config — if it does not already set
`populate_by_name=True`, add `model_config` overrides on these classes exactly as
`theloom/operations/common.py::CommandInput` does.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_schema.py -v`
Expected: 3 passed

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run ruff format . && uv run pytest`
Expected: all pass

```bash
git add theloom/viz/ tests/test_viz_schema.py
git commit -m "Add TapestryBundle schema for visualization wire contract"
```

---

### Task 2: Scope resolution

**Files:**
- Create: `theloom/viz/scope.py`
- Test: `tests/test_viz_scope.py`

**Interfaces:**
- Consumes: `theloom.graph.subgraph.extract_{causal,ego,typed}_subgraph`, `theloom.operations.analysis._docs` pattern (reimplement locally — it is private), `theloom.store.falkor.FalkorGraphStore`.
- Produces:
  - `class ScopeInput(CommandInput)` — fields `mode: str = "full"`, `center: str | None`, `depth: int = Field(default=1, ge=1, le=5)`, `entity_type: str | None (alias "entityType")`, `relation_type: str | None (alias "relationType")`.
  - `resolve_scope(scope: ScopeInput, store: FalkorGraphStore) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]` — returns `(entities, relations, scope_label)` where `scope_label` is e.g. `"full"`, `"ego:<center>:d2"`, `"causal"`, `"typed:concept/causes"`.

- [ ] **Step 1: Write the failing test**

```python
"""Scope resolution tests over a live namespaced store."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.scope import ScopeInput, resolve_scope


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


@pytest.fixture()
def seeded(multi: MultiGraph) -> dict[str, str]:
    """a --causes--> b --supports--> c ; returns name->id."""
    store = multi.get_store()
    ids: dict[str, str] = {}
    for name, etype in (("a", "variable"), ("b", "variable"), ("c", "claim")):
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": etype})
        )
        ids[name] = entity.id
    store.create_relation(
        RelationCreate.model_validate(
            {"from": ids["a"], "to": ids["b"], "relationType": "causes"}
        )
    )
    store.create_relation(
        RelationCreate.model_validate(
            {"from": ids["b"], "to": ids["c"], "relationType": "supports"}
        )
    )
    return ids


def test_full_scope(multi: MultiGraph, seeded: dict[str, str]) -> None:
    entities, relations, label = resolve_scope(ScopeInput(), multi.get_store())
    assert {e["name"] for e in entities} == {"a", "b", "c"}
    assert len(relations) == 2
    assert label == "full"


def test_causal_scope_keeps_only_causal_relations(
    multi: MultiGraph, seeded: dict[str, str]
) -> None:
    entities, relations, label = resolve_scope(
        ScopeInput(mode="causal"), multi.get_store()
    )
    assert {e["name"] for e in entities} == {"a", "b"}
    assert [r["relationType"] for r in relations] == ["causes"]
    assert label == "causal"


def test_ego_scope(multi: MultiGraph, seeded: dict[str, str]) -> None:
    entities, _, label = resolve_scope(
        ScopeInput(mode="ego", center=seeded["a"], depth=1), multi.get_store()
    )
    assert {e["name"] for e in entities} == {"a", "b"}
    assert label == f"ego:{seeded['a']}:d1"


def test_typed_scope(multi: MultiGraph, seeded: dict[str, str]) -> None:
    entities, relations, _ = resolve_scope(
        ScopeInput.model_validate({"mode": "typed", "entityType": "variable"}),
        multi.get_store(),
    )
    assert {e["name"] for e in entities} == {"a", "b"}
    assert [r["relationType"] for r in relations] == ["causes"]


def test_ego_without_center_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(ScopeInput(mode="ego"), multi.get_store())
    assert err.value.code == "VALIDATION_ERROR"


def test_ego_with_missing_center_is_not_found(
    multi: MultiGraph, seeded: dict[str, str]
) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(
            ScopeInput(mode="ego", center="00000000-0000-0000-0000-000000000000"),
            multi.get_store(),
        )
    assert err.value.code == "NOT_FOUND"


def test_unknown_mode_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(ScopeInput(mode="banana"), multi.get_store())
    assert err.value.code == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_scope.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` on `theloom.viz.scope`

- [ ] **Step 3: Write the implementation**

`theloom/viz/scope.py`:

```python
"""Bundle scoping — which slice of the graph goes into the visualization."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, ValidationError
from theloom.graph.subgraph import (
    extract_causal_subgraph,
    extract_ego_subgraph,
    extract_typed_subgraph,
)
from theloom.operations.common import CommandInput
from theloom.store.falkor import FalkorGraphStore

Doc = dict[str, Any]

_MODES = ("full", "ego", "causal", "typed")


class ScopeInput(CommandInput):
    mode: str = "full"
    center: str | None = None
    depth: int = Field(default=1, ge=1, le=5)
    entity_type: str | None = Field(default=None, alias="entityType")
    relation_type: str | None = Field(default=None, alias="relationType")


def _docs(store: FalkorGraphStore) -> tuple[list[Doc], list[Doc]]:
    entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    return entities, relations


def resolve_scope(
    scope: ScopeInput, store: FalkorGraphStore
) -> tuple[list[Doc], list[Doc], str]:
    if scope.mode not in _MODES:
        raise ValidationError(
            f"Invalid scope mode: '{scope.mode}'. Must be one of: {', '.join(_MODES)}"
        )
    entities, relations = _docs(store)
    if scope.mode == "full":
        return entities, relations, "full"
    if scope.mode == "causal":
        causal_entities, causal_relations = extract_causal_subgraph(entities, relations)
        return causal_entities, causal_relations, "causal"
    if scope.mode == "ego":
        if scope.center is None:
            raise ValidationError("Scope mode 'ego' requires 'center' (an entity id).")
        result = extract_ego_subgraph(entities, relations, scope.center, depth=scope.depth)
        if result is None:
            raise NotFoundError(f"Entity not found with ID: {scope.center}")
        ego_entities, ego_relations = result
        return ego_entities, ego_relations, f"ego:{scope.center}:d{scope.depth}"
    typed_entities, typed_relations = extract_typed_subgraph(
        entities, relations, scope.entity_type, scope.relation_type
    )
    label = f"typed:{scope.entity_type or '*'}/{scope.relation_type or '*'}"
    return typed_entities, typed_relations, label
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_scope.py -v`
Expected: 7 passed

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest`
Expected: all pass

```bash
git add theloom/viz/scope.py tests/test_viz_scope.py
git commit -m "Add visualization scope resolution over subgraph extractors"
```

---

### Task 3: Analytics section assembly

**Files:**
- Create: `theloom/viz/analytics.py`
- Test: `tests/test_viz_analytics.py`

**Interfaces:**
- Consumes: `theloom.operations.analysis.{analyze_centrality, detect_components, detect_loops, list_leverage_points}` with their input models (`AnalyzeCentralityInput`, `DetectComponentsInput`, `DetectLoopsInput`, `ListLeveragePointsInput`); `multi.bridges.list_bridges()`; Task 1's `AnalyticsSection`.
- Produces: `assemble_analytics(graph: str | None, multi: MultiGraph) -> AnalyticsSection`.

- [ ] **Step 1: Write the failing test**

```python
"""Analytics section assembly tests. Seeds a 3-node line graph with one causal
loop (a->b->a) and asserts each analytics field is populated with the shapes
the operations already emit."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.analytics import assemble_analytics


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_assembles_all_fields(multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(EntityCreate.model_validate({"name": "a", "entityType": "variable"}))
    b = store.create_entity(EntityCreate.model_validate({"name": "b", "entityType": "variable"}))
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": b.id, "to": a.id, "relationType": "inhibits"})
    )
    section = assemble_analytics(None, multi)
    assert set(section.centrality.keys()) == {"degree", "betweenness", "pagerank"}
    assert a.id in section.centrality["degree"]
    assert any({a.id, b.id} <= set(component) for component in section.components)
    assert len(section.loops) >= 1  # a->b->a is a feedback loop
    assert section.leverage_points == []
    assert section.bridges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_analytics.py -v`
Expected: FAIL with `ImportError` on `theloom.viz.analytics`

- [ ] **Step 3: Write the implementation**

`theloom/viz/analytics.py`:

```python
"""Analytics for the bundle — thin reuse of the existing analysis operations."""

from __future__ import annotations

from theloom.operations.analysis import (
    AnalyzeCentralityInput,
    DetectComponentsInput,
    DetectLoopsInput,
    ListLeveragePointsInput,
    analyze_centrality,
    detect_components,
    detect_loops,
    list_leverage_points,
)
from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import AnalyticsSection

_ALGORITHMS = ("degree", "betweenness", "pagerank")


def assemble_analytics(graph: str | None, multi: MultiGraph) -> AnalyticsSection:
    centrality = {
        algorithm: analyze_centrality(
            AnalyzeCentralityInput(algorithm=algorithm, graph=graph), multi
        )["scores"]
        for algorithm in _ALGORITHMS
    }
    components = detect_components(DetectComponentsInput(graph=graph), multi)["components"]
    loops = detect_loops(DetectLoopsInput(graph=graph, persist=False), multi)["loops"]
    leverage = list_leverage_points(ListLeveragePointsInput(graph=graph), multi)[
        "leveragePoints"
    ]
    bridges = multi.bridges.list_bridges()
    return AnalyticsSection(
        centrality=centrality,
        components=components,
        loops=loops,
        leverage_points=leverage,
        bridges=bridges,
    )
```

Note: if `detect_loops`'s return dict uses a different key than `"loops"`, read
`theloom/graph/cycles.py::detect_loops` and use the actual key — `theloom/composites/graph_reconnaissance.py:62-70` accesses `detected["loops"]`, so `"loops"` is expected. Constructor kwargs above use Python field names; `AnalyticsSection` must accept them (`populate_by_name=True`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_analytics.py -v`
Expected: 1 passed

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest`

```bash
git add theloom/viz/analytics.py tests/test_viz_analytics.py
git commit -m "Assemble bundle analytics from existing analysis operations"
```

---

### Task 4: Temporal section + `MultiGraph.event_log()`

**Files:**
- Modify: `theloom/store/multigraph.py` (add one method after `run_store()`, ~line 194)
- Create: `theloom/viz/temporal.py`
- Test: `tests/test_viz_temporal.py`

**Interfaces:**
- Consumes: `theloom.store.events.{EventLog, Event}` (`Event.id` is a Redis stream id `"<ms>-<seq>"`).
- Produces:
  - `MultiGraph.event_log(name: str | None = None) -> EventLog`
  - `assemble_temporal(graph: str | None, multi: MultiGraph) -> TemporalSection` — events in append order, `at` derived from the stream id milliseconds as UTC ISO.

- [ ] **Step 1: Write the failing test**

```python
"""Temporal section tests: events appear in order with ISO timestamps."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.temporal import assemble_temporal


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_event_log_accessor(multi: MultiGraph, namespace: str) -> None:
    log = multi.event_log()
    assert log.key == f"{namespace}:default:events"


def test_temporal_section_replays_mutations(multi: MultiGraph) -> None:
    store = multi.get_store()
    entity = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept"})
    )
    store.update_entity(entity.id, {"name": "a2"})
    section = assemble_temporal(None, multi)
    types = [event.type for event in section.events]
    assert types == ["entity_created", "entity_updated"]
    assert section.events[0].at.endswith("+00:00")
    assert section.events[0].payload["entity"]["id"] == entity.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_temporal.py -v`
Expected: FAIL (`event_log` attribute missing / module missing)

- [ ] **Step 3: Write the implementation**

In `theloom/store/multigraph.py`, after `run_store()`:

```python
    def event_log(self, name: str | None = None) -> EventLog:
        """The append-only event stream for one named graph (viz/history reads)."""
        return EventLog(self._redis, name or self.default_graph, self._prefix)
```

with `from theloom.store.events import EventLog` added to the imports.

`theloom/viz/temporal.py`:

```python
"""Temporal section — the graph's event stream shaped for client-side replay."""

from __future__ import annotations

from datetime import UTC, datetime

from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import TemporalEvent, TemporalSection


def _stream_id_to_iso(stream_id: str) -> str:
    milliseconds = int(stream_id.split("-", maxsplit=1)[0])
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()


def assemble_temporal(graph: str | None, multi: MultiGraph) -> TemporalSection:
    events = [
        TemporalEvent(
            id=event.id,
            at=_stream_id_to_iso(event.id),
            type=event.type,
            payload=event.payload,
        )
        for event in multi.event_log(graph).read_all()
    ]
    return TemporalSection(events=events)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_temporal.py -v`
Expected: 2 passed

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest`

```bash
git add theloom/store/multigraph.py theloom/viz/temporal.py tests/test_viz_temporal.py
git commit -m "Expose per-graph event log and assemble bundle temporal section"
```

---

### Task 5: Semantic section (PCA projection)

**Files:**
- Modify: `pyproject.toml` (add `"numpy>=1.26",` to `[project] dependencies` after `"scipy>=1.13",`)
- Create: `theloom/viz/semantic.py`
- Test: `tests/test_viz_semantic.py`

**Interfaces:**
- Consumes: `FalkorGraphStore.get_entity_vectors() -> dict[str, list[float]]` (`theloom/store/falkor.py:127`), `FalkorGraphStore.set_entity_vector()` (test seeding).
- Produces: `assemble_semantic(graph: str | None, multi: MultiGraph) -> SemanticSection | None` — `None` when fewer than 3 embedded entities; otherwise PCA to 2D, coordinates rounded to 4 decimals, `method="pca"`.

- [ ] **Step 1: Write the failing test**

```python
"""Semantic projection tests using synthetic 4-dim vectors (no fastembed needed)."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.semantic import assemble_semantic


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_none_when_too_few_vectors(multi: MultiGraph) -> None:
    assert assemble_semantic(None, multi) is None


def test_pca_projection_shape(multi: MultiGraph) -> None:
    store = multi.get_store()
    vectors = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": [0.0, 1.0, 0.0, 0.0],
        "c": [0.0, 0.0, 1.0, 0.0],
        "d": [1.0, 1.0, 0.0, 0.0],
    }
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept"})
        )
        store.set_entity_vector(entity.id, vector)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "pca"
    assert len(section.projection) == 4
    assert all(len(point) == 2 for point in section.projection.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_semantic.py -v`
Expected: FAIL with `ImportError` on `theloom.viz.semantic`

- [ ] **Step 3: Write the implementation**

Add `"numpy>=1.26",` to `pyproject.toml` dependencies, then `uv sync`.

`theloom/viz/semantic.py`:

```python
"""Semantic section — 2D PCA projection of entity embedding vectors.

PCA via numpy SVD; UMAP arrives as an optional upgrade in phase 3."""

from __future__ import annotations

import numpy as np

from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import SemanticSection

_MIN_VECTORS = 3


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
    return SemanticSection(method="pca", projection=projection)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_semantic.py -v`
Expected: 2 passed

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest`

```bash
git add pyproject.toml uv.lock theloom/viz/semantic.py tests/test_viz_semantic.py
git commit -m "Project entity embeddings to 2D for the bundle semantic section"
```

---

### Task 6: Bundle assembler

**Files:**
- Create: `theloom/viz/bundle.py`
- Test: `tests/test_viz_bundle.py`

**Interfaces:**
- Consumes: Tasks 1–5 (`ScopeInput`, `resolve_scope`, `assemble_analytics`, `assemble_temporal`, `assemble_semantic`, schema models); `theloom.timeutil.iso_now`.
- Produces:
  - `class IncludeInput(CommandInput)` — `analytics: bool = True`, `temporal: bool = True`, `semantic: bool = True`.
  - `class ExportBundleInput(CommandInput)` — `graph: str | None`, `scope: ScopeInput = Field(default_factory=ScopeInput)`, `include: IncludeInput = Field(default_factory=IncludeInput)`, `title: str | None`.
  - `assemble_bundle(params: ExportBundleInput, multi: MultiGraph) -> dict[str, Any]` — validates named graph exists (NOT_FOUND otherwise, message matching the `graph_stats` phrasing), returns `TapestryBundle.model_dump(by_alias=True, exclude_none=True)`. `meta.sections` lists only the sections actually present (semantic omitted when `assemble_semantic` returns None).

- [ ] **Step 1: Write the failing test**

```python
"""Bundle assembler tests — the one entry point both commands share."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.bundle import ExportBundleInput, assemble_bundle
from theloom.viz.schema import SCHEMA_VERSION, TapestryBundle


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


@pytest.fixture()
def seeded(multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(EntityCreate.model_validate({"name": "a", "entityType": "concept"}))
    b = store.create_entity(EntityCreate.model_validate({"name": "b", "entityType": "claim"}))
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "supports"})
    )


def test_default_bundle(multi: MultiGraph, seeded: None) -> None:
    doc = assemble_bundle(ExportBundleInput(), multi)
    TapestryBundle.model_validate(doc)  # schema-valid
    assert doc["schemaVersion"] == SCHEMA_VERSION
    assert doc["meta"]["graph"] == "default"
    assert doc["meta"]["scope"] == "full"
    assert doc["meta"]["entityCount"] == 2
    assert doc["meta"]["relationCount"] == 1
    assert set(doc["meta"]["sections"]) == {"analytics", "temporal"}  # no vectors seeded
    assert "semantic" not in doc


def test_includes_are_flags(multi: MultiGraph, seeded: None) -> None:
    doc = assemble_bundle(
        ExportBundleInput.model_validate(
            {"include": {"analytics": False, "temporal": False, "semantic": False}}
        ),
        multi,
    )
    assert doc["meta"]["sections"] == []
    assert "analytics" not in doc and "temporal" not in doc


def test_unknown_graph_is_not_found(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        assemble_bundle(ExportBundleInput.model_validate({"graph": "nope"}), multi)
    assert err.value.code == "NOT_FOUND"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_viz_bundle.py -v`
Expected: FAIL with `ImportError` on `theloom.viz.bundle`

- [ ] **Step 3: Write the implementation**

`theloom/viz/bundle.py`:

```python
"""assemble_bundle — the single assembler behind export-bundle, visualize, and
(phase 4) the live server."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.viz.analytics import assemble_analytics
from theloom.viz.schema import SCHEMA_VERSION, TapestryBundle, TapestryMeta
from theloom.viz.scope import ScopeInput, resolve_scope
from theloom.viz.semantic import assemble_semantic
from theloom.viz.temporal import assemble_temporal


class IncludeInput(CommandInput):
    analytics: bool = True
    temporal: bool = True
    semantic: bool = True


class ExportBundleInput(CommandInput):
    graph: str | None = None
    scope: ScopeInput = Field(default_factory=ScopeInput)
    include: IncludeInput = Field(default_factory=IncludeInput)
    title: str | None = None


def assemble_bundle(params: ExportBundleInput, multi: MultiGraph) -> dict[str, Any]:
    target = params.graph or multi.default_graph
    if params.graph and not multi.has_graph(params.graph):
        raise NotFoundError(
            f"Graph '{params.graph}' not found. Use list_graphs to see available graphs."
        )
    entities, relations, scope_label = resolve_scope(params.scope, multi.get_store(target))

    analytics = assemble_analytics(target, multi) if params.include.analytics else None
    temporal = assemble_temporal(target, multi) if params.include.temporal else None
    semantic = assemble_semantic(target, multi) if params.include.semantic else None

    sections = [
        name
        for name, value in (
            ("analytics", analytics),
            ("temporal", temporal),
            ("semantic", semantic),
        )
        if value is not None
    ]
    bundle = TapestryBundle(
        schema_version=SCHEMA_VERSION,
        meta=TapestryMeta(
            graph=target,
            title=params.title,
            scope=scope_label,
            generated_at=iso_now(),
            entity_count=len(entities),
            relation_count=len(relations),
            sections=sections,
        ),
        entities=entities,
        relations=relations,
        analytics=analytics,
        temporal=temporal,
        semantic=semantic,
    )
    return bundle.model_dump(by_alias=True, exclude_none=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_viz_bundle.py -v`
Expected: 3 passed

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest`

```bash
git add theloom/viz/bundle.py tests/test_viz_bundle.py
git commit -m "Add TapestryBundle assembler composing scope and sections"
```

---

### Task 7: HTML rendering + registry commands

**Files:**
- Create: `theloom/viz/html.py`
- Modify: `theloom/cli/registry.py` (imports near line 60; descriptors appended to `COMMANDS`)
- Modify: `COMMANDS.md` (regenerated, never hand-edited)
- Test: `tests/test_viz_html.py`, `tests/test_cli_viz_commands.py`

**Interfaces:**
- Consumes: Task 6 (`ExportBundleInput`, `assemble_bundle`); `theloom.errors.ConfigError`.
- Produces:
  - `DATA_SENTINEL = "__TAPESTRY_BUNDLE__"`
  - `render_html(bundle: dict[str, Any], template_text: str) -> str` — replaces the sentinel with `json.dumps(bundle).replace("</", "<\\/")`; raises `ConfigError` if the sentinel is absent.
  - `load_template() -> str` — reads `theloom/viz/static/tapestry.html` via `importlib.resources`; raises `ConfigError` ("Tapestry template missing — build the frontend: cd tapestry && npm ci && npm run build") when absent.
  - `write_visualization(params: VisualizeInput, multi: MultiGraph) -> dict[str, Any]` — returns `{"path", "entityCount", "relationCount", "bytes", "sections"}`.
  - `class VisualizeInput(ExportBundleInput)` — adds `output: str | None` (default `loom-viz/<graph>.html`), `theme: str = "auto"` (validated against `auto|dark|light`, VALIDATION_ERROR otherwise; theme is embedded into the bundle meta as `meta.theme` — add `theme: str | None = None` to `TapestryMeta` in this task).
  - Registry: `visualize` and `export-bundle` descriptors, `category="Visualization"`, `allow_empty=True`.

- [ ] **Step 1: Write the failing tests**

`tests/test_viz_html.py`:

```python
"""HTML rendering tests — sentinel injection, escaping, template errors."""

from __future__ import annotations

import json

import pytest

from theloom.errors import LoomError
from theloom.viz.html import DATA_SENTINEL, render_html

TEMPLATE = (
    "<html><script id=\"tapestry-data\" type=\"application/json\">"
    f"{DATA_SENTINEL}</script></html>"
)


def test_injects_bundle_json() -> None:
    html = render_html({"meta": {"graph": "g"}}, TEMPLATE)
    assert '"graph": "g"' in html or '"graph":"g"' in html
    assert DATA_SENTINEL not in html


def test_escapes_script_close() -> None:
    html = render_html({"x": "</script><script>alert(1)</script>"}, TEMPLATE)
    assert "</script><script>alert(1)" not in html
    start = html.index(">", html.index("tapestry-data")) + 1
    end = html.index("</script>", start)
    assert json.loads(html[start:end].replace("<\\/", "</"))["x"].startswith("</script>")


def test_template_without_sentinel_is_config_error() -> None:
    with pytest.raises(LoomError) as err:
        render_html({}, "<html></html>")
    assert err.value.code == "CONFIG_ERROR"
```

`tests/test_cli_viz_commands.py`:

```python
"""Visualization command tests through run_handler, per the CLI test convention."""

from __future__ import annotations

from pathlib import Path

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.cli.registry import COMMANDS, run_handler
from theloom.errors import LoomError
from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_registry_has_visualization_commands() -> None:
    by_name = {c.name: c for c in COMMANDS}
    assert by_name["visualize"].category == "Visualization"
    assert by_name["export-bundle"].category == "Visualization"


def test_export_bundle_returns_bundle(multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept"})
    )
    result = run_handler("export-bundle", {}, multi)
    assert result["schemaVersion"] == 1
    assert result["meta"]["entityCount"] == 1


def test_visualize_writes_file(multi: MultiGraph, tmp_path: Path) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept"})
    )
    output = tmp_path / "out.html"
    result = run_handler("visualize", {"output": str(output)}, multi)
    assert result["path"] == str(output)
    assert result["entityCount"] == 1
    assert result["bytes"] == len(output.read_bytes())
    assert "tapestry-data" in output.read_text()


def test_visualize_bad_theme_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        run_handler("visualize", {"theme": "sepia"}, multi)
    assert err.value.code == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_viz_html.py tests/test_cli_viz_commands.py -v`
Expected: FAIL (`ImportError`; `KeyError: 'visualize'`)

- [ ] **Step 3: Write the implementation**

Add `theme: str | None = None` to `TapestryMeta` in `theloom/viz/schema.py`.

`theloom/viz/html.py`:

```python
"""Static HTML emission — inject the bundle into the built SPA template.

The template is the committed single-file Vite build; its data block holds the
sentinel this module replaces. `</` is escaped so bundle content can never
terminate the script block."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from theloom.errors import ConfigError, ValidationError
from theloom.store.multigraph import MultiGraph
from theloom.viz.bundle import ExportBundleInput, assemble_bundle

DATA_SENTINEL = "__TAPESTRY_BUNDLE__"
_THEMES = ("auto", "dark", "light")


class VisualizeInput(ExportBundleInput):
    output: str | None = None
    theme: str = "auto"


def render_html(bundle: dict[str, Any], template_text: str) -> str:
    if DATA_SENTINEL not in template_text:
        raise ConfigError(
            "Tapestry template is missing its data sentinel — rebuild the frontend: "
            "cd tapestry && npm ci && npm run build"
        )
    payload = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    return template_text.replace(DATA_SENTINEL, payload)


def load_template() -> str:
    resource = resources.files("theloom.viz").joinpath("static/tapestry.html")
    try:
        return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ConfigError(
            "Tapestry template missing — build the frontend: "
            "cd tapestry && npm ci && npm run build"
        ) from exc


def write_visualization(params: VisualizeInput, multi: MultiGraph) -> dict[str, Any]:
    if params.theme not in _THEMES:
        raise ValidationError(
            f"Invalid theme: '{params.theme}'. Must be one of: {', '.join(_THEMES)}"
        )
    bundle = assemble_bundle(params, multi)
    bundle["meta"]["theme"] = params.theme
    html = render_html(bundle, load_template())
    target = Path(params.output or f"loom-viz/{bundle['meta']['graph']}.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = html.encode("utf-8")
    target.write_bytes(data)
    return {
        "path": str(target),
        "entityCount": bundle["meta"]["entityCount"],
        "relationCount": bundle["meta"]["relationCount"],
        "bytes": len(data),
        "sections": bundle["meta"]["sections"],
    }
```

In `theloom/cli/registry.py` — add imports:

```python
from theloom.viz.bundle import ExportBundleInput, assemble_bundle
from theloom.viz.html import VisualizeInput, write_visualization
```

and append to `COMMANDS` (with the other category groups):

```python
    CommandDescriptor(
        name="export-bundle",
        category="Visualization",
        summary="Assemble the TapestryBundle JSON for a graph scope.",
        input_model=ExportBundleInput,
        handler=assemble_bundle,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="visualize",
        category="Visualization",
        summary="Write a self-contained interactive HTML visualization of a graph scope.",
        input_model=VisualizeInput,
        handler=write_visualization,
        allow_empty=True,
    ),
```

**Test-only template:** `test_visualize_writes_file` needs a template before the
real frontend exists. Create a placeholder now at `theloom/viz/static/tapestry.html`:

```html
<!doctype html><html><head><meta charset="utf-8"><title>Tapestry</title></head>
<body><script id="tapestry-data" type="application/json">__TAPESTRY_BUNDLE__</script>
<div id="root">Tapestry frontend not yet built.</div></body></html>
```

Task 8 replaces this placeholder with the real build.

Regenerate docs: `uv run loom --generate-docs > COMMANDS.md`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_viz_html.py tests/test_cli_viz_commands.py tests/test_generate_docs.py -v`
Expected: all pass (the docs-drift test passes because COMMANDS.md was regenerated)

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest`

```bash
git add theloom/viz/ theloom/cli/registry.py COMMANDS.md tests/test_viz_html.py tests/test_cli_viz_commands.py
git commit -m "Add visualize and export-bundle commands with HTML emission"
```

---

### Task 8: Frontend scaffold (tapestry/) + real template build

**Files:**
- Create: `tapestry/package.json`, `tapestry/vite.config.ts`, `tapestry/tsconfig.json`, `tapestry/index.html`, `tapestry/src/main.tsx`, `tapestry/src/App.tsx`, `tapestry/src/lib/data.ts`, `tapestry/src/lib/data.test.ts`, `tapestry/fixtures/dev-bundle.json`, `tapestry/scripts/emit-template.mjs`, `tapestry/.gitignore` (`node_modules/`, `dist/`)
- Replace: `theloom/viz/static/tapestry.html` (real build output overwrites the Task 7 placeholder)

**Interfaces:**
- Consumes: the `<script id="tapestry-data">` sentinel contract from Task 7.
- Produces: `loadBundle(): Promise<TapestryBundleRaw>` in `src/lib/data.ts` (sentinel → fetch `fixtures/dev-bundle.json` in dev; otherwise parse inline JSON); `npm run build` emitting the single-file template; `npm test` running Vitest.

- [ ] **Step 1: Scaffold the workspace**

`tapestry/package.json`:

```json
{
  "name": "tapestry",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build && node scripts/emit-template.mjs",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "fuse.js": "^7.0.0",
    "graphology": "^0.26.0",
    "graphology-communities-louvain": "^2.0.2",
    "graphology-layout-forceatlas2": "^0.10.1",
    "graphology-shortest-path": "^2.1.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "sigma": "^3.0.0",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "ajv": "^8.17.0",
    "happy-dom": "^15.0.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0",
    "vite-plugin-singlefile": "^2.0.0",
    "vitest": "^2.1.0"
  }
}
```

(Adjust minor versions to what `npm install` resolves; commit `package-lock.json`.)

`tapestry/vite.config.ts`:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  test: { environment: "happy-dom" },
});
```

`tapestry/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Tapestry — The Loom</title>
  </head>
  <body>
    <script id="tapestry-data" type="application/json">__TAPESTRY_BUNDLE__</script>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`tapestry/src/lib/data.ts`:

```typescript
const SENTINEL = "__TAPESTRY" + "_BUNDLE__"; // split so the build never inlines the sentinel verbatim elsewhere

export interface TapestryBundleRaw {
  schemaVersion: number;
  meta: {
    graph: string;
    title?: string;
    theme?: string;
    scope: string;
    generatedAt: string;
    entityCount: number;
    relationCount: number;
    sections: string[];
  };
  entities: Record<string, unknown>[];
  relations: Record<string, unknown>[];
  analytics?: Record<string, unknown>;
  temporal?: { events: Record<string, unknown>[] };
  semantic?: { method: string; projection: Record<string, number[]> };
}

export function parseInlineBundle(text: string): TapestryBundleRaw | null {
  if (text.trim() === SENTINEL) return null; // dev mode — no data injected
  return JSON.parse(text) as TapestryBundleRaw;
}

export async function loadBundle(): Promise<TapestryBundleRaw> {
  const block = document.getElementById("tapestry-data");
  const inline = block ? parseInlineBundle(block.textContent ?? "") : null;
  if (inline) return inline;
  const response = await fetch("/fixtures/dev-bundle.json");
  return (await response.json()) as TapestryBundleRaw;
}
```

`tapestry/src/lib/data.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { parseInlineBundle } from "./data";

describe("parseInlineBundle", () => {
  it("returns null for the sentinel (dev mode)", () => {
    expect(parseInlineBundle("__TAPESTRY_BUNDLE__")).toBeNull();
  });
  it("parses injected JSON", () => {
    const bundle = parseInlineBundle(
      JSON.stringify({ schemaVersion: 1, meta: { graph: "g" }, entities: [], relations: [] }),
    );
    expect(bundle?.meta.graph).toBe("g");
  });
});
```

`tapestry/src/main.tsx`:

```typescript
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(<App />);
```

`tapestry/src/App.tsx` (minimal — the shell grows in Task 10):

```typescript
import { useEffect, useState } from "react";
import { loadBundle, type TapestryBundleRaw } from "./lib/data";

export function App() {
  const [bundle, setBundle] = useState<TapestryBundleRaw | null>(null);
  useEffect(() => {
    loadBundle().then(setBundle);
  }, []);
  if (!bundle) return <p>Loading…</p>;
  return (
    <main>
      <h1>{bundle.meta.title ?? bundle.meta.graph}</h1>
      <p>
        {bundle.meta.entityCount} entities · {bundle.meta.relationCount} relations
      </p>
    </main>
  );
}
```

`tapestry/scripts/emit-template.mjs`:

```javascript
import { readFileSync, writeFileSync } from "node:fs";

const html = readFileSync("dist/index.html", "utf8");
if (!html.includes("__TAPESTRY_BUNDLE__")) {
  console.error("Built template lost the data sentinel — check index.html/singlefile config.");
  process.exit(1);
}
writeFileSync("../theloom/viz/static/tapestry.html", html);
console.log("Template emitted to theloom/viz/static/tapestry.html");
```

`tapestry/fixtures/dev-bundle.json`: generate from a seeded test graph once Task 7 lands — `uv run loom export-bundle '{}' > tapestry/fixtures/dev-bundle.json` against a populated dev graph (or hand-write ~6 entities / 6 relations covering causal + epistemic types).

- [ ] **Step 2: Verify tests and build**

Run: `cd tapestry && npm install && npm test`
Expected: 2 passed

Run: `npm run build`
Expected: `Template emitted to theloom/viz/static/tapestry.html`

- [ ] **Step 3: Verify the Python side accepts the real template**

Run: `cd .. && uv run pytest tests/test_cli_viz_commands.py -v`
Expected: all pass (real template still carries the sentinel + data block)

- [ ] **Step 4: Commit**

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Scaffold Tapestry frontend workspace with single-file build"
```

---

### Task 9: Schema drift test (Python ⇄ TypeScript)

**Files:**
- Create: `tapestry/schema/bundle.schema.json` (generated, committed)
- Create: `tests/test_viz_schema_drift.py`
- Create: `tapestry/src/lib/schema.test.ts`

**Interfaces:**
- Consumes: Task 1 `bundle_json_schema()`; Task 8 fixture `tapestry/fixtures/dev-bundle.json`.
- Produces: the committed schema file both sides pin against.

- [ ] **Step 1: Write the failing Python drift test**

```python
"""The committed JSON Schema must match the live Pydantic model — regenerate
with: uv run python -m theloom.viz.schema"""

from __future__ import annotations

import json
from pathlib import Path

from theloom.viz.schema import bundle_json_schema

SCHEMA_PATH = Path(__file__).parent.parent / "tapestry" / "schema" / "bundle.schema.json"


def test_committed_schema_matches_model() -> None:
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == bundle_json_schema()
```

- [ ] **Step 2: Add the generator and generate**

Append to `theloom/viz/schema.py`:

```python
if __name__ == "__main__":  # regenerate the committed schema for the frontend
    import json
    from pathlib import Path

    out = Path(__file__).parents[2] / "tapestry" / "schema" / "bundle.schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle_json_schema(), indent=2) + "\n")
    print(f"Wrote {out}")
```

Run: `uv run python -m theloom.viz.schema` then `uv run pytest tests/test_viz_schema_drift.py -v`
Expected: PASS

- [ ] **Step 3: Frontend validates the fixture against the schema**

`tapestry/src/lib/schema.test.ts`:

```typescript
import Ajv from "ajv";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("dev fixture conforms to the committed bundle schema", () => {
  it("validates", () => {
    const schema = JSON.parse(readFileSync("schema/bundle.schema.json", "utf8"));
    const fixture = JSON.parse(readFileSync("fixtures/dev-bundle.json", "utf8"));
    const ajv = new Ajv({ strict: false });
    expect(ajv.validate(schema, fixture)).toBe(true);
  });
});
```

Run: `cd tapestry && npm test`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tapestry/schema/ tests/test_viz_schema_drift.py tapestry/src/lib/schema.test.ts theloom/viz/schema.py
git commit -m "Pin the bundle wire contract with a committed JSON Schema"
```

---

### Task 10: Design system + app shell + shared state

**Files:**
- Create: `tapestry/src/design/tokens.css`, `tapestry/src/design/theme.ts`
- Create: `tapestry/src/state/store.ts`, `tapestry/src/state/store.test.ts`
- Create: `tapestry/src/state/urlHash.ts`, `tapestry/src/state/urlHash.test.ts`
- Modify: `tapestry/src/App.tsx` (view-switcher shell: Explorer | Overview tabs, theme toggle, header with `meta.title ?? meta.graph`)

**Interfaces:**
- Produces:
  - zustand store `useTapestry` with state `{view: "explorer" | "overview", theme: "auto" | "dark" | "light", selection: string | null, filters: Filters, setView, setTheme, select, setFilters}` where `Filters = {entityTypes: string[], relationTypes: string[], confidenceMin: number, statuses: string[]}` (empty array = no filter).
  - `serializeState(state) → location.hash` string and `parseHash(hash) → Partial<state>` — round-trip tested; applied on load and on state change (deep-linkable static file).
  - CSS custom properties in `tokens.css` for both themes (`:root` + `[data-theme="dark"]`): surfaces, text, borders, accent, and the 19-entity-type categorical palette (`--type-concept`, `--type-claim`, … one per `EntityType` in `theloom/model.py:49-68`).

**Execution note:** load the `dataviz` skill (palette/contrast rules) and `frontend-design` skill BEFORE writing `tokens.css`. Palette must pass the dataviz validator in both themes.

- [ ] **Step 1: Write failing store + hash tests**

```typescript
// tapestry/src/state/store.test.ts
import { describe, expect, it } from "vitest";
import { useTapestry } from "./store";

describe("tapestry store", () => {
  it("defaults to explorer view with empty filters", () => {
    const s = useTapestry.getState();
    expect(s.view).toBe("explorer");
    expect(s.filters.entityTypes).toEqual([]);
    expect(s.filters.confidenceMin).toBe(0);
  });
  it("selects and filters", () => {
    useTapestry.getState().select("e1");
    useTapestry.getState().setFilters({ entityTypes: ["concept"] });
    expect(useTapestry.getState().selection).toBe("e1");
    expect(useTapestry.getState().filters.entityTypes).toEqual(["concept"]);
  });
});
```

```typescript
// tapestry/src/state/urlHash.test.ts
import { describe, expect, it } from "vitest";
import { parseHash, serializeState } from "./urlHash";

describe("url hash state", () => {
  it("round-trips view, selection, and filters", () => {
    const state = {
      view: "overview" as const,
      selection: "e42",
      filters: { entityTypes: ["claim"], relationTypes: [], confidenceMin: 0.5, statuses: [] },
    };
    expect(parseHash(serializeState(state))).toEqual(state);
  });
  it("returns {} for an empty or malformed hash", () => {
    expect(parseHash("")).toEqual({});
    expect(parseHash("#garbage")).toEqual({});
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd tapestry && npm test` → FAIL (modules missing)

- [ ] **Step 3: Implement**

`tapestry/src/state/store.ts`:

```typescript
import { create } from "zustand";

export interface Filters {
  entityTypes: string[];
  relationTypes: string[];
  confidenceMin: number;
  statuses: string[];
}

export type View = "explorer" | "overview";
export type Theme = "auto" | "dark" | "light";

export const EMPTY_FILTERS: Filters = {
  entityTypes: [],
  relationTypes: [],
  confidenceMin: 0,
  statuses: [],
};

interface TapestryState {
  view: View;
  theme: Theme;
  selection: string | null;
  filters: Filters;
  setView: (view: View) => void;
  setTheme: (theme: Theme) => void;
  select: (id: string | null) => void;
  setFilters: (partial: Partial<Filters>) => void;
}

export const useTapestry = create<TapestryState>((set) => ({
  view: "explorer",
  theme: "auto",
  selection: null,
  filters: EMPTY_FILTERS,
  setView: (view) => set({ view }),
  setTheme: (theme) => set({ theme }),
  select: (selection) => set({ selection }),
  setFilters: (partial) => set((s) => ({ filters: { ...s.filters, ...partial } })),
}));
```

`tapestry/src/state/urlHash.ts`:

```typescript
import type { Filters, View } from "./store";

export interface HashState {
  view: View;
  selection: string | null;
  filters: Filters;
}

export function serializeState(state: HashState): string {
  return "#s=" + encodeURIComponent(JSON.stringify(state));
}

export function parseHash(hash: string): Partial<HashState> {
  if (!hash.startsWith("#s=")) return {};
  try {
    return JSON.parse(decodeURIComponent(hash.slice(3))) as HashState;
  } catch {
    return {};
  }
}
```

`tokens.css` per the execution note (dataviz-validated palette, both themes); shell in `App.tsx` wires theme to `document.documentElement.dataset.theme`, applies `parseHash(location.hash)` on mount, and subscribes to the store to keep `location.hash` current via `history.replaceState`.

- [ ] **Step 4: Run tests** — `npm test` → all pass. Manual check: `npm run dev`, switch tabs/theme, reload with hash → state restored.

- [ ] **Step 5: Commit**

```bash
git add tapestry/src/
git commit -m "Add Tapestry design tokens, app shell, shared state, and deep links"
```

---

### Task 11: Graph Explorer — model, layout, rendering

**Files:**
- Create: `tapestry/src/views/explorer/buildGraph.ts`, `buildGraph.test.ts`
- Create: `tapestry/src/views/explorer/Explorer.tsx`, `tapestry/src/views/explorer/layout.ts`
- Create: `tapestry/src/lib/BundleContext.tsx` (React context providing the loaded bundle + memoized `buildGraph` result to all views)
- Modify: `tapestry/src/App.tsx` (mount Explorer for `view === "explorer"`, wrap views in `BundleContext`)

**Interfaces:**
- Consumes: `TapestryBundleRaw` (Task 8), tokens (Task 10).
- Produces: `buildGraph(bundle: TapestryBundleRaw): Graph` (graphology directed multigraph; node attrs `{label, entityType, confidence, status, degree, community, size, color, x, y}`; edge attrs `{relationType, polarity, strength, confidence, size, color, type: "arrow"}`). Louvain communities assigned via `graphology-communities-louvain`. `layout.ts` runs ForceAtlas2 via `graphology-layout-forceatlas2/worker` with start/stop controls.

- [ ] **Step 1: Write failing buildGraph tests**

```typescript
import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { buildGraph } from "./buildGraph";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 2, relationCount: 1, sections: [] },
  entities: [
    { id: "a", name: "A", entityType: "concept" },
    { id: "b", name: "B", entityType: "claim", confidence: { score: 0.9 } },
  ],
  relations: [
    { id: "r1", from: "a", to: "b", relationType: "supports", strength: "strong" },
  ],
} as never;

describe("buildGraph", () => {
  it("builds nodes and edges with visual attributes", () => {
    const graph: Graph = buildGraph(bundle);
    expect(graph.order).toBe(2);
    expect(graph.size).toBe(1);
    expect(graph.getNodeAttribute("a", "entityType")).toBe("concept");
    expect(graph.getNodeAttribute("b", "confidence")).toBe(0.9);
    expect(graph.getNodeAttribute("a", "size")).toBeGreaterThan(0);
    expect(graph.getEdgeAttribute("r1", "relationType")).toBe("supports");
    expect(typeof graph.getNodeAttribute("a", "community")).toBe("number");
  });
  it("skips dangling relations", () => {
    const broken = { ...bundle, relations: [{ id: "r2", from: "a", to: "zzz", relationType: "causes" }] };
    expect(buildGraph(broken as never).size).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → FAIL

- [ ] **Step 3: Implement**

`buildGraph.ts`: create `new Graph({ multi: true, type: "directed" })`; add nodes from `entities` (label = name, size = `3 + 2 * Math.sqrt(degree)` computed after edges land, color from `--type-<entityType>` token map, random initial x/y — deterministic via a simple mulberry32 seeded on node id hash so layouts are reproducible); add edges skipping dangling endpoints (mirror `hydrate_graph`'s behavior); then `louvain.assign(graph)`; edge color by relation family (structural neutral, epistemic hues, causal accent), `size` by strength (`weak:1, moderate:1.5, strong:2.5, foundational:3.5`).

`layout.ts`: `FA2Layout` worker wrapper with `{start(), stop(), running}` and settings `forceAtlas2.inferSettings(graph)`.

`Explorer.tsx`: instantiate `new Sigma(graph, container, { renderEdgeLabels: false, defaultEdgeType: "arrow" })`; run layout ~3 s then stop; physics toggle button; camera fit on mount. Use bundle from a `BundleContext` provided by `App.tsx`.

- [ ] **Step 4: Verify** — `npm test` → pass. Manual: `npm run dev` → force-laid-out colored directed graph renders; wheel-zoom and drag-pan work.

- [ ] **Step 5: Build + commit**

Run: `npm run build` (refresh committed template), `cd .. && uv run pytest tests/test_cli_viz_commands.py`

```bash
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Render the Graph Explorer with WebGL force layout and typed encoding"
```

---

### Task 12: Graph Explorer — interactions, filters, search, detail panel

**Files:**
- Create: `tapestry/src/views/explorer/filters.ts`, `filters.test.ts`
- Create: `tapestry/src/views/explorer/DetailPanel.tsx`, `SearchBox.tsx`, `FilterPanel.tsx`
- Modify: `tapestry/src/views/explorer/Explorer.tsx`

**Interfaces:**
- Consumes: Task 10 store (`selection`, `filters`), Task 11 graph.
- Produces: `applyFilters(graph: Graph, filters: Filters): {visibleNodes: Set<string>, visibleEdges: Set<string>}` — a node is visible when it passes entityTypes/status/confidenceMin; an edge when both endpoints are visible AND it passes relationTypes. Sigma `nodeReducer`/`edgeReducer` consume these sets (hidden, not removed — filters are non-destructive).

- [ ] **Step 1: Write failing filter tests**

```typescript
import { describe, expect, it } from "vitest";
import Graph from "graphology";
import { applyFilters } from "./filters";

function tinyGraph(): Graph {
  const g = new Graph({ multi: true, type: "directed" });
  g.addNode("a", { entityType: "concept", confidence: 0.9, status: "active" });
  g.addNode("b", { entityType: "claim", confidence: 0.4, status: "active" });
  g.addEdgeWithKey("r1", "a", "b", { relationType: "supports" });
  return g;
}

describe("applyFilters", () => {
  it("empty filters keep everything", () => {
    const { visibleNodes, visibleEdges } = applyFilters(tinyGraph(), {
      entityTypes: [], relationTypes: [], confidenceMin: 0, statuses: [],
    });
    expect(visibleNodes.size).toBe(2);
    expect(visibleEdges.size).toBe(1);
  });
  it("confidence floor hides low-confidence nodes and their edges", () => {
    const { visibleNodes, visibleEdges } = applyFilters(tinyGraph(), {
      entityTypes: [], relationTypes: [], confidenceMin: 0.5, statuses: [],
    });
    expect(visibleNodes.has("b")).toBe(false);
    expect(visibleEdges.size).toBe(0);
  });
  it("relation type filter hides non-matching edges only", () => {
    const { visibleNodes, visibleEdges } = applyFilters(tinyGraph(), {
      entityTypes: [], relationTypes: ["causes"], confidenceMin: 0, statuses: [],
    });
    expect(visibleNodes.size).toBe(2);
    expect(visibleEdges.size).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → FAIL

- [ ] **Step 3: Implement**

`filters.ts` pure function per the interface (nodes without `confidence` treated as passing any floor — absence of evidence is not a hidden node). In `Explorer.tsx`: wire sigma reducers — hidden nodes/edges get `hidden: true`; hovered node highlights its neighbors (others dimmed via reduced alpha); click sets `selection` in the store; `SearchBox` uses fuse.js over `{id, name, entityType}` with results dropdown, choosing one selects + animates camera to the node; `FilterPanel` renders type checkbox groups (counts per type), confidence range slider, status toggles; `DetailPanel` renders the selected entity's full doc: name, type chip, observations list, confidence gauge (score + basis), provenance block, status + reason, and a clickable neighbor list (click → select + pan).

- [ ] **Step 4: Verify** — `npm test` → pass; manual dev-server check of hover/click/search/filter flows.

- [ ] **Step 5: Build + commit**

```bash
npm run build
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add Explorer interactions: filters, fuzzy search, detail panel"
```

---

### Task 13: Path mode, minimap, keyboard navigation

**Files:**
- Create: `tapestry/src/views/explorer/pathMode.ts`, `pathMode.test.ts`
- Create: `tapestry/src/views/explorer/Minimap.tsx`
- Create: `tapestry/src/lib/keyboard.ts`
- Modify: `tapestry/src/views/explorer/Explorer.tsx`, `tapestry/src/state/store.ts` (add `pathEndpoints: [string | null, string | null]`, `pathMode: boolean`)

**Interfaces:**
- Produces: `findPath(graph: Graph, from: string, to: string): {nodes: string[], edges: string[]} | null` using `graphology-shortest-path` (`bidirectional`), treating the graph as undirected for pathfinding but returning the actual directed edge keys traversed. Keyboard map: `/` focus search, `p` toggle path mode, `f` fit camera, `Escape` clear selection/path, arrow keys walk to the nearest neighbor in that screen direction.

- [ ] **Step 1: Write failing path test**

```typescript
import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { findPath } from "./pathMode";

describe("findPath", () => {
  it("finds a shortest path and its edge keys", () => {
    const g = new Graph({ multi: true, type: "directed" });
    ["a", "b", "c"].forEach((n) => g.addNode(n));
    g.addEdgeWithKey("r1", "a", "b", {});
    g.addEdgeWithKey("r2", "b", "c", {});
    expect(findPath(g, "a", "c")).toEqual({ nodes: ["a", "b", "c"], edges: ["r1", "r2"] });
  });
  it("returns null when no path exists", () => {
    const g = new Graph();
    g.addNode("a");
    g.addNode("z");
    expect(findPath(g, "a", "z")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → FAIL

- [ ] **Step 3: Implement** — `pathMode.ts` per interface; in Explorer, path mode clicks set endpoints (first click = from, second = to), reducers dim everything outside the path and give path edges a highlight color + increased size; a path summary bar lists the hops (`A —supports→ B —causes→ C`) with a clear button. `Minimap.tsx`: a small canvas drawing node positions + the current camera viewport rectangle; click-to-pan. `keyboard.ts`: a `useKeyboard(bindings)` hook attached in Explorer; bindings per the interface (skip when focus is in an input).

- [ ] **Step 4: Verify** — `npm test` → pass; manual: two clicks in path mode highlight the chain, minimap tracks camera, all shortcuts work.

- [ ] **Step 5: Build + commit**

```bash
npm run build
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add path mode, minimap, and keyboard navigation to the Explorer"
```

---

### Task 14: Overview dashboard

**Files:**
- Create: `tapestry/src/views/overview/Overview.tsx`, `tapestry/src/views/overview/stats.ts`, `stats.test.ts`
- Modify: `tapestry/src/App.tsx` (mount for `view === "overview"`)

**Interfaces:**
- Consumes: bundle `meta`, `entities`, `relations`, `analytics` (Task 6 shapes: `centrality.{degree,betweenness,pagerank}` id→score maps, `components: string[][]`, `loops`, `leveragePoints`, `bridges`).
- Produces: `computeOverviewStats(bundle): {typeCounts: Record<string, number>, relationTypeCounts: Record<string, number>, confidenceHistogram: number[] /* 10 bins */, danglingRelationCount: number, contradictionCount: number, topCentral: {id, name, score}[] /* top 10 pagerank */}`.

**Execution note:** load the `dataviz` skill before writing any chart markup; charts are hand-rolled SVG bars/histograms using the token palette — no chart library.

- [ ] **Step 1: Write failing stats tests**

```typescript
import { describe, expect, it } from "vitest";
import { computeOverviewStats } from "./stats";

const bundle = {
  schemaVersion: 1,
  meta: { graph: "g", scope: "full", generatedAt: "", entityCount: 3, relationCount: 2, sections: ["analytics"] },
  entities: [
    { id: "a", name: "A", entityType: "concept", confidence: { score: 0.95 } },
    { id: "b", name: "B", entityType: "claim", confidence: { score: 0.15 } },
    { id: "c", name: "C", entityType: "claim" },
  ],
  relations: [
    { id: "r1", from: "a", to: "b", relationType: "contradicts" },
    { id: "r2", from: "a", to: "ghost", relationType: "supports" },
  ],
  analytics: { centrality: { degree: {}, betweenness: {}, pagerank: { a: 0.5, b: 0.3, c: 0.2 } }, components: [["a", "b", "c"]], loops: [], leveragePoints: [], bridges: [] },
} as never;

describe("computeOverviewStats", () => {
  it("counts types, contradictions, and dangling relations", () => {
    const stats = computeOverviewStats(bundle);
    expect(stats.typeCounts).toEqual({ concept: 1, claim: 2 });
    expect(stats.contradictionCount).toBe(1);
    expect(stats.danglingRelationCount).toBe(1);
    expect(stats.confidenceHistogram[9]).toBe(1); // 0.95 → last bin
    expect(stats.confidenceHistogram[1]).toBe(1); // 0.15 → second bin
    expect(stats.topCentral[0]).toMatchObject({ id: "a", name: "A", score: 0.5 });
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → FAIL

- [ ] **Step 3: Implement** — `stats.ts` pure function (histogram: `Math.min(9, Math.floor(score * 10))`; dangling = relation endpoint not in the entity id set; entities without confidence are excluded from the histogram, counted in an "unscored" tile). `Overview.tsx`: stat tiles (entities, relations, components, loops, leverage points, unscored %), horizontal bar charts for type distributions, confidence histogram, top-central table with inline bars — each central row clickable → switches to Explorer with that node selected (cross-view navigation via the store).

- [ ] **Step 4: Verify** — `npm test` → pass; manual dashboard check in both themes.

- [ ] **Step 5: Build + commit**

```bash
npm run build
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add Overview dashboard with health and centrality panels"
```

---

### Task 15: Exports (PNG/SVG) + saved views

**Files:**
- Create: `tapestry/src/lib/exportSvg.ts`, `exportSvg.test.ts`
- Create: `tapestry/src/lib/savedViews.ts`, `savedViews.test.ts`
- Modify: `tapestry/src/views/explorer/Explorer.tsx`, `tapestry/src/App.tsx` (export + saved-views toolbar)

**Interfaces:**
- Produces:
  - `graphToSvg(graph: Graph, visible: {visibleNodes: Set<string>, visibleEdges: Set<string>}, viewport: {x: number, y: number, ratio: number}): string` — serializes current positions/colors/sizes of visible elements to an SVG string (WYSIWYG of the WebGL state).
  - PNG export via sigma's canvas snapshot (draw sigma's layered canvases onto one off-screen canvas, `toBlob`, download).
  - `savedViews.ts`: `listViews(): SavedView[]`, `saveView(name: string, hash: string): void`, `deleteView(name: string): void` over `localStorage` key `tapestry:views:<graph>`; `SavedView = {name, hash, savedAt}`.

- [ ] **Step 1: Write failing tests**

```typescript
// exportSvg.test.ts
import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { graphToSvg } from "./exportSvg";

describe("graphToSvg", () => {
  it("emits only visible elements with positions", () => {
    const g = new Graph({ multi: true, type: "directed" });
    g.addNode("a", { x: 0, y: 0, size: 5, color: "#123456", label: "A" });
    g.addNode("b", { x: 10, y: 10, size: 5, color: "#654321", label: "B" });
    g.addEdgeWithKey("r1", "a", "b", { color: "#999999", size: 1 });
    const svg = graphToSvg(
      g,
      { visibleNodes: new Set(["a"]), visibleEdges: new Set() },
      { x: 0, y: 0, ratio: 1 },
    );
    expect(svg).toContain("<svg");
    expect(svg).toContain("#123456");
    expect(svg).not.toContain("#654321");
  });
});
```

```typescript
// savedViews.test.ts
import { beforeEach, describe, expect, it } from "vitest";
import { deleteView, listViews, saveView } from "./savedViews";

describe("saved views", () => {
  beforeEach(() => localStorage.clear());
  it("saves, lists, and deletes named views", () => {
    saveView("g", "my-view", "#s=abc");
    expect(listViews("g")).toHaveLength(1);
    expect(listViews("g")[0]).toMatchObject({ name: "my-view", hash: "#s=abc" });
    deleteView("g", "my-view");
    expect(listViews("g")).toHaveLength(0);
  });
});
```

(Adjust `saveView`/`listViews` signatures to include the graph key as the first
argument, as shown — update the Produces block usage accordingly.)

- [ ] **Step 2: Run to verify failure** — `npm test` → FAIL

- [ ] **Step 3: Implement** — per interfaces; toolbar buttons: Export PNG, Export SVG, Save view (name prompt), saved-view dropdown (select → apply hash). Download helper creates an `<a download>` with an object URL.

- [ ] **Step 4: Verify** — `npm test` → pass; manual: export a PNG and an SVG, save/restore a view across reloads.

- [ ] **Step 5: Build + commit**

```bash
npm run build
git add tapestry/ theloom/viz/static/tapestry.html
git commit -m "Add WYSIWYG PNG/SVG export and saved views"
```

---

### Task 16: End-to-end smoke tests, CI, docs

**Files:**
- Create: `tapestry/e2e/smoke.spec.ts`, `tapestry/playwright.config.ts`
- Create or Modify: `.github/workflows/` CI (inspect what exists first; add a `tapestry` job)
- Modify: `README.md` (Visualization section), `CLAUDE.md` layout block (+ `theloom/viz/`, `tapestry/`)

**Interfaces:**
- Consumes: everything shipped in Tasks 1–15.

- [ ] **Step 1: Write the e2e smoke test**

`tapestry/playwright.config.ts`: chromium only, `testDir: "e2e"`.

`tapestry/e2e/smoke.spec.ts`:

```typescript
import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test.beforeAll(() => {
  // Inject the dev fixture into the committed template — same path as loom visualize.
  const template = readFileSync("../theloom/viz/static/tapestry.html", "utf8");
  const bundle = readFileSync("fixtures/dev-bundle.json", "utf8");
  const html = template.replace("__TAPESTRY_BUNDLE__", bundle.replaceAll("</", "<\\/"));
  writeFileSync(join(tmpdir(), "tapestry-e2e.html"), html);
});

test("explorer renders nodes and the overview shows counts", async ({ page }) => {
  await page.goto("file://" + join(tmpdir(), "tapestry-e2e.html"));
  await expect(page.locator("canvas").first()).toBeVisible(); // sigma mounted
  await page.getByRole("tab", { name: "Overview" }).click();
  await expect(page.getByText(/entities/i)).toBeVisible();
});

test("path mode highlights a path", async ({ page }) => {
  await page.goto("file://" + join(tmpdir(), "tapestry-e2e.html"));
  await page.keyboard.press("p");
  await expect(page.getByText(/path mode/i)).toBeVisible();
});
```

Run: `cd tapestry && npx playwright install chromium && npm run e2e`
Expected: 2 passed

- [ ] **Step 2: CI**

Inspect `.github/workflows/` first and extend the existing pipeline. Add a job:

```yaml
  tapestry:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: tapestry/package-lock.json }
      - run: npm ci
        working-directory: tapestry
      - run: npm test
        working-directory: tapestry
      - run: npm run build
        working-directory: tapestry
      - run: git diff --exit-code theloom/viz/static/tapestry.html
        # committed template must match the source build
      - run: npx playwright install --with-deps chromium && npm run e2e
        working-directory: tapestry
```

- [ ] **Step 3: Docs**

README: a "Visualization" section — `uv run loom visualize` → open the HTML; scope/include examples; screenshot placeholder is NOT allowed — capture a real screenshot of the dev fixture (`npm run dev`, browser screenshot) and commit it under `docs/images/tapestry-explorer.png`. CLAUDE.md layout block gains `theloom/viz/` and `tapestry/` lines. Verify `COMMANDS.md` is current (`uv run pytest tests/test_generate_docs.py`).

- [ ] **Step 4: Full gates**

Run: `uv run mypy --strict theloom && uv run ruff check . && uv run pytest && cd tapestry && npm test && npm run e2e`
Expected: everything passes

- [ ] **Step 5: Commit**

```bash
git add tapestry/ .github/ README.md CLAUDE.md docs/images/
git commit -m "Add visualization smoke tests, CI job, and documentation"
```

---

## Plan self-review notes

- **Spec coverage (phase 1 scope):** bundle contract ✓ (T1, T6, T9), scope modes full/ego/causal/typed ✓ (T2; `search` deferred to phase 3 per roadmap), analytics ✓ (T3), temporal data ✓ (T4 — the Chronicle *view* is phase 2 by design), semantic projection data ✓ (T5 — the Semantic Map *view* is phase 3), CLI commands + JSON-out + typed errors ✓ (T7), offline single-file SPA ✓ (T8), design system/themes ✓ (T10), Explorer encodings/filters/search/detail ✓ (T11–12), path mode/minimap/keyboard ✓ (T13), Overview ✓ (T14), WYSIWYG exports + saved views + deep links ✓ (T10, T15), testing conventions + CI ✓ (throughout, T16).
- **Louvain communities** are computed client-side (T11), not in the bundle — the spec's analytics table is amended by this plan (bundle carries components/centrality/loops/leverage/bridges; communities derive in the SPA where they also drive color).
- **50k-node hardening** is phase 5; phase 1 targets correct behavior at fixture scale with the WebGL foundation that makes phase 5 achievable.
