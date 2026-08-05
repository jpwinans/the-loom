---
name: the-loom
description: "Knowledge graph substrate for externalized reasoning, persistent memory, and semantic search. The Loom provides 156 CLI commands for building, querying, and analyzing knowledge graphs with typed entities, causal relations, epistemic metadata, semantic embeddings, and document ingestion. Use when: (1) Building or querying knowledge graphs, (2) Ingesting documents for semantic search, (3) Modeling causal systems with feedback loops and leverage points, (4) Tracking epistemic provenance and confidence, (5) Analyzing graph topology (centrality, cycles, clusters), (6) Extracting codebase structure into graphs, (7) Running semantic search across entities or documents, (8) Generating hypotheses from graph gaps, (9) Finding analogies via structural matching and concept slippage, (10) Mining recurring subgraph motifs, (11) Mapping concepts across domains, (12) Any task involving The Loom's CLI."
---

# The Loom

Externalized cognitive engine — knowledge graphs + semantic search + causal dynamics + epistemic rigor + creative discovery + analogical reasoning.

## Architecture

Seven layers, each building on the previous:

```
Layer 7: CREATIVITY — Hypothesis generation, analogical reasoning, concept slippage
Layer 6: REASONING  — Transitive closure, rule inference, explanation generation
Layer 5: STATE      — Confidence, provenance, versioning, status
Layer 4: EMBEDDINGS — Vector search, document ingestion, semantic discovery
Layer 3: DYNAMICS   — Feedback loops, leverage points, system modeling
Layer 2: RELATIONS  — 14 typed + polarized connection types
Layer 1: ENTITIES   — 16 entity types, observations, timestamps
```

## Quick Start

### Access Pattern

The Loom exposes a single JSON-in/JSON-out CLI. There is no MCP server in this
repository — every operation goes through:

```bash
uv run loom <command> '<json>'
```

Commands use `kebab-case` (e.g., `create-entity`, `hybrid-search`). All accept an
optional `graph` parameter for multi-graph mode.

### First Steps with Any Graph

```
graph-stats                    → understand scale
list-entities (entityType)     → see what exists
analyze-centrality (degree)    → find hubs
```

Or use the `graph-reconnaissance` composite for all of the above in one call.

### Create an Entity

```json
{
  "name": "Systems Thinking",
  "entityType": "concept",
  "observations": [
    "definition: A holistic approach to analysis",
    "domain: management, ecology, engineering"
  ]
}
```

Required fields: `name`, `entityType`, `observations` (array of strings).
Optional: `confidence`, `provenance`, `graph`.

### Create a Relation

```json
{
  "from": "source-entity-uuid",
  "to": "target-entity-uuid",
  "relationType": "causes",
  "polarity": "+",
  "strength": "strong",
  "evidence": "Observed in three independent studies"
}
```

Required: `from`, `to`, `relationType`, `polarity` (null for non-causal), `strength`
(`weak|moderate|strong|foundational`), and `evidence` (string, or null when there is
genuinely nothing to cite). The CLI rejects a relation missing any of these.

### Search

```json
// hybrid-search — combines vector + keyword + graph
{ "query": "feedback loops in organizational change", "limit": 10 }

// With category filter (for documents)
{ "query": "authentication flow", "category": "docs", "limit": 5 }

// With multiple categories
{ "query": "design notes", "category": ["docs", "specs"] }
```

### Ingest a Document

```json
// ingest-document
{ "file_path": "/path/to/document.pdf", "category": "research" }

// ingest-directory (batch)
{ "dir_path": "/path/to/docs/", "pattern": "**/*.md", "category": "docs" }
```

Supported formats: PDF, DOCX, MD, HTML, TXT, JSON.

## Core Principles

### 1. Observations Are Atomic

One fact per observation string. Use `key: value` format:
```
"definition: A holistic approach to analysis"
"domain: management, ecology"
"severity: high"
```

### 2. Relations Are Directional

`from → to` matters. `A causes B` is different from `B causes A`.

- **Structural** (`related_to`, `instance_of`, `part_of`, `sources`): polarity = `null`
- **Epistemic** (`supports`, `contradicts`, `questions`, `supersedes`): polarity = `null`
- **Causal** (`causes`, `enables`, `requires`, `inhibits`, `amplifies`, `dampens`): polarity = `+` or `-`

### 3. Confidence Is Calibrated

Score 0.0-1.0 with a basis explaining how confidence was assessed:
- `direct_observation` (0.90-0.95) — measured or verified
- `peer_reviewed` (0.85-0.90) — published source
- `multiple_sources` (0.80-0.85) — corroborated
- `single_source` (0.70-0.80) — one reliable reference
- `inference` (0.60-0.70) — derived through reasoning
- `llm_extraction` (0.70-0.80) — LLM-extracted
- `speculation` (0.10-0.30) — hypothesis

### 4. Status Tracks Lifecycle

Entities default to `active`. Transitions:
- `active` → `superseded` (replaced by newer version)
- `active` → `deprecated` (outdated but kept)
- `active` → `retracted` (withdrawn due to error)
- `active` → `investigating` (under review)

When superseding: create new entity, then update old one with `status: "superseded"` and create `supersedes` relation.

### 5. Multi-Graph by Default

All tools accept a `graph` parameter. Without it, the default graph is used.
Cross-graph connections use bridge relations, held transactionally in FalkorDB
alongside the graphs themselves.

```
list-graphs                     → see all graphs
graph-stats (graph: "name")     → inspect specific graph
```

## Tool Selection Guide

### "I want to..."

| Goal | Tool(s) |
|------|---------|
| Understand a graph's structure | `graph-reconnaissance` |
| Search for something | `hybrid-search` |
| Find similar entities | `semantic-neighbors` |
| Find what's connected to X | `get-neighbors`, `get-relations` |
| Find the path between A and B | `find-shortest-path`, `explain-path` |
| Find hub entities | `analyze-centrality` |
| Find feedback loops | `detect-loops`, `list-loops` |
| Find weak claims | `uncertain-claims`, `needs-evidence` |
| Find contradictions | `contested-claims` |
| Find stale knowledge | `stale-beliefs` |
| Trace where knowledge came from | `provenance-chain`, `provenance-audit` |
| Ingest documents | `ingest-document`, `ingest-directory` |
| Extract entities from documents | `extract-from-documents` |
| Extract code structure | `extract-codebase` |
| Get complete analysis of one entity | `entity-deep-dive` |
| Map influence from one entity | `influence-map` |
| Find gaps in the graph | `semantic-gaps`, `gap-fill-cycle` |
| Generate hypotheses from gaps | `hypothesis-engine` |
| Find analogies across domains | `cross-domain-mapping` |
| Find concept substitutions | `concept-slippage` |
| Find recurring structural motifs | `find-frequent-subgraphs` |
| Find structurally similar patterns | `find-subgraph-matches` |
| Generate synthesis | `synthesize`, `traverse-synthesis` |
| Batch create entities | `bulk-import` |
| Verify graph integrity | `check-consistency`, `check-invariants` |
| Simulate what-if changes | `simulate-change` |
| Get everything about one code symbol in one call | `explore` |
| Find who calls / is called by a symbol | `find-callers`, `find-callees` |
| Find the reverse dependency reach of a symbol | `blast-radius` |
| Record how a piece of work turned out | `record-outcome` |
| Distil recorded outcomes into standing lessons | `reflect` |
| Pre-download the embedding model | `warm-embedder` |
| Export a graph as a portable JSON file (no FalkorDB/CLI to read it back) | `export-graph` |

### Composite vs Manual

**Prefer composites** for common multi-step analyses:
- `graph-reconnaissance` over manual stats + centrality + components
- `entity-deep-dive` over manual read + relations + neighbors
- `semantic-landscape` over manual clusters + gaps + suggestions
- `gap-fill-cycle` over manual gaps + suggest + create
- `hypothesis-engine` over manual gaps + propose + filter + dedup + rank
- `explore` over manually chaining `read-entity` + `get-relations` + `get-neighbors` +
  `entity-deep-dive` for one code symbol — one budgeted call returns definition,
  callers/callees, imports, containment, inheritance, and the attached semantic layer
- `find-callers`/`find-callees` over filtering `get-relations` by `relationType: "calls"`
  by hand — already ranked and anchored at the call site
- `blast-radius` over manually walking `get-neighbors` — caps depth, suppresses hubs,
  groups the result by module

## Cheap Reads

Keep responses small instead of fetching everything and filtering client-side:

- **Name addressing.** `read-entity`, `get-relations`, `get-neighbors`,
  `entity-deep-dive`, `find-shortest-path`, `explain-path`, `explore`, `find-callers`,
  `find-callees`, and `blast-radius` all take a `name` in place of an id — exactly one
  of `id`/`entityId` or `name` is required. Resolution is exact case-insensitive first,
  then unique case-insensitive substring; an ambiguous name is a `VALIDATION_ERROR`
  listing every candidate.
- **`compact: true`** on `read-entity`, `list-entities`, and `get-neighbors` narrows the
  response to `{id, name, entityType, status, observations}` — use it for any listing
  you're about to filter or skim rather than read in full.
- **`limit: N`** on `list-entities` (and on `find-callers`/`find-callees`, default 30)
  caps how much comes back; `list-entities` with a `limit` returns
  `{items, truncated: {shown, total, hint}}` instead of the legacy bare array, so check
  for `truncated` when you pass one.

## References

- **[Tool Catalog](references/tool-catalog.md)** — read when you need a command's exact parameters, or to scan what exists in an unfamiliar category
- **[Data Model](references/data-model.md)** — read before creating entities/relations of a type you haven't used yet: entity types, relation types, epistemic metadata, storage format
- **[Workflows](references/workflows.md)** — read at the start of a multi-step task (research, system modeling, codebase analysis, maintenance) to follow the established pattern instead of improvising one

## Common Pitfalls

1. **Forgetting polarity on causal relations** — `causes`, `enables`, `requires`, `inhibits`, `amplifies`, `dampens` need `+` or `-`
2. **Not embedding after bulk creation** — Run `embed-entities` after creating many entities to enable semantic search
3. **Searching non-active entities** — `list-entities` defaults to active only; pass `includeSuperseded` / `includeDeprecated` / `includeRetracted` / `includeInvestigating` to widen it
4. **Swapping from/to on relations** — Relations are directional; check which entity is source vs target
5. **Observations as single string** — Must be an array of strings, not one string
6. **Missing category on document ingestion** — Without category, documents aren't filterable in search
7. **Using delete instead of status change** — Prefer `status: "superseded"` over `delete-entity` to preserve history
