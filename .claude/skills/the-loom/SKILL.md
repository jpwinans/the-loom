---
name: the-loom
description: "Knowledge graph substrate for externalized reasoning, persistent memory, and semantic search. The Loom provides 100+ MCP tools for building, querying, and analyzing knowledge graphs with typed entities, causal relations, epistemic metadata, semantic embeddings, and document ingestion. Use when: (1) Building or querying knowledge graphs, (2) Ingesting documents for semantic search, (3) Modeling causal systems with feedback loops and leverage points, (4) Tracking epistemic provenance and confidence, (5) Analyzing graph topology (centrality, cycles, clusters), (6) Extracting codebase structure into graphs, (7) Running semantic search across entities or documents, (8) Generating hypotheses from graph gaps, (9) Finding analogies via structural matching and concept slippage, (10) Mining recurring subgraph motifs, (11) Mapping concepts across domains, (12) Any task involving The Loom's MCP tools or CLI."
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

### Access Patterns

**MCP tools** (primary — used within Claude Code sessions):
Tools use `snake_case` naming. All accept optional `graph` parameter for multi-graph mode.

**CLI** (for scripts and automation):
```bash
uv run loom <command> '<json>'
```
Commands use `kebab-case` (e.g., `create-entity`, `hybrid-search`).

### First Steps with Any Graph

```
graph_stats                    → understand scale
list_entities (entityType)     → see what exists
analyze_centrality (degree)    → find hubs
```

Or use the `graph_reconnaissance` composite for all of the above in one call.

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

Required: `from`, `to`, `relationType`, `polarity` (null for non-causal), `strength`.

### Search

```json
// hybrid_search — combines vector + keyword + graph
{ "query": "feedback loops in organizational change", "limit": 10 }

// With category filter (for documents)
{ "query": "authentication flow", "category": "docs", "limit": 5 }

// With multiple categories
{ "query": "design notes", "category": ["docs", "specs"] }
```

### Ingest a Document

```json
// ingest_document
{ "filePath": "/path/to/document.pdf", "category": "research" }

// ingest_directory (batch)
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
list_graphs                     → see all graphs
graph_stats (graph: "name")     → inspect specific graph
```

## Tool Selection Guide

### "I want to..."

| Goal | Tool(s) |
|------|---------|
| Understand a graph's structure | `graph_reconnaissance` |
| Search for something | `hybrid_search` |
| Find similar entities | `semantic_neighbors` |
| Find what's connected to X | `get_neighbors`, `get_relations` |
| Find the path between A and B | `find_shortest_path`, `explain_path` |
| Find hub entities | `analyze_centrality` |
| Find feedback loops | `detect_loops`, `list_loops` |
| Find weak claims | `uncertain_claims`, `needs_evidence` |
| Find contradictions | `contested_claims` |
| Find stale knowledge | `stale_beliefs` |
| Trace where knowledge came from | `provenance_chain`, `provenance_audit` |
| Ingest documents | `ingest_document`, `ingest_directory` |
| Extract entities from documents | `extract_from_documents` |
| Extract code structure | `extract_codebase` |
| Get complete analysis of one entity | `entity_deep_dive` |
| Map influence from one entity | `influence_map` |
| Find gaps in the graph | `semantic_gaps`, `gap_fill_cycle` |
| Generate hypotheses from gaps | `hypothesis_engine` |
| Find analogies across domains | `cross_domain_mapping` |
| Find concept substitutions | `concept_slippage` |
| Find recurring structural motifs | `find_frequent_subgraphs` |
| Find structurally similar patterns | `find_subgraph_matches` |
| Generate synthesis | `synthesize`, `traverse_synthesis` |
| Batch create entities | `bulk_import` |
| Verify graph integrity | `check_consistency`, `check_invariants` |
| Simulate what-if changes | `simulate_change` |

### Composite vs Manual

**Prefer composites** for common multi-step analyses:
- `graph_reconnaissance` over manual stats + centrality + components
- `entity_deep_dive` over manual read + relations + neighbors
- `semantic_landscape` over manual clusters + gaps + suggestions
- `gap_fill_cycle` over manual gaps + suggest + create
- `hypothesis_engine` over manual gaps + propose + filter + dedup + rank

## References

- **[Tool Catalog](references/tool-catalog.md)** — Complete list of all tools with parameters
- **[Data Model](references/data-model.md)** — Entity types, relation types, epistemic metadata, storage format
- **[Workflows](references/workflows.md)** — Multi-step patterns for research, system modeling, codebase analysis, maintenance

## Common Pitfalls

1. **Forgetting polarity on causal relations** — `causes`, `enables`, `requires`, `inhibits`, `amplifies`, `dampens` need `+` or `-`
2. **Not embedding after bulk creation** — Run `embed_entities` after creating many entities to enable semantic search
3. **Searching non-active entities** — `list_entities` defaults to active only; pass `statusFilter` to include others
4. **Swapping from/to on relations** — Relations are directional; check which entity is source vs target
5. **Observations as single string** — Must be an array of strings, not one string
6. **Missing category on document ingestion** — Without category, documents aren't filterable in search
7. **Using delete instead of status change** — Prefer `status: "superseded"` over `delete_entity` to preserve history
