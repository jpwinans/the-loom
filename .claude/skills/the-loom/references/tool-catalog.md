# Tool Catalog

All Loom commands grouped by function. Commands use kebab-case (e.g., `create-entity`).

**CLI invocation:** `uv run loom <command> '<json>'`

---

## Entity & Relation CRUD (12 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `create-entity` | Create entity | `name`, `entityType`, `observations`, `confidence?`, `provenance?`, `graph?` |
| `read-entity` | Read by ID or name | `id?`, `name?`, `compact?`, `graph?` |
| `update-entity` | Update fields | `id`, `name?`, `observations?`, `confidence?`, `status?`, `graph?` |
| `delete-entity` | Delete entity | `id`, `graph?` |
| `list-entities` | Filter entities | `entityType?`, `query?`, `name?`, `includeSuperseded?`/`includeDeprecated?`/`includeRetracted?`/`includeInvestigating?`, `limit?`, `compact?`, `graph?` |
| `create-relation` | Create edge | `from`, `to`, `relationType`, `polarity`, `strength`, `evidence?`, `graph?` |
| `read-relation` | Read relation | `id`, `graph?` |
| `update-relation` | Update relation | `id`, `relationType?`, `strength?`, `graph?` |
| `delete-relation` | Delete relation | `id`, `graph?` |
| `list-relations` | Filter relations | `entityId?`, `relationType?`, `graph?` |
| `get-relations` | Relations for entity | `entityId?`, `name?`, `direction?`, `relationType?`, `compact?`, `graph?` |
| `get-neighbors` | Connected entities (each annotated with the connecting `relationType`/direction) | `entityId?`, `name?`, `direction?`, `relationType?`, `compact?`, `graph?` |

`read-entity`, `get-relations`, `get-neighbors`, `entity-deep-dive`, `find-shortest-path`,
`explain-path`, `explore`, `find-callers`, `find-callees`, and `blast-radius` are all
addressed by `id`/`entityId` **or** `name` — exactly one required; name resolution is
exact-match first, then unique substring, else `VALIDATION_ERROR`.

`compact: true` (on `read-entity`, `list-entities`, `get-neighbors`) projects to
`{id, name, entityType, status, observations}`, off by default (byte-identical
response when omitted). `list-entities` with `limit` returns
`{items, truncated: {shown, total, hint}}` instead of the legacy bare array; without
`limit` the bare-array shape is unchanged.

---

## Graph Management (7 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `graph-stats` | Entity/relation counts + type distribution | `graph?` |
| `list-graphs` | List all graphs | — |
| `create-graph` | Create new graph | `name` |
| `delete-graph` | Delete graph | `name` |
| `list-bridges` | Cross-graph bridge relations | `graph?` |
| `find-related-graphs` | Find connected graphs | `graph?` |
| `graph-connections` | Cross-graph connection map | `graph?` |

---

## Semantic Operations (8 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `hybrid-search` | Vector + keyword + graph search | `query`, `entityType?`, `limit?`, `category?`, `graph?` |
| `semantic-search` | Pure vector similarity | `query`, `limit?`, `graph?` |
| `semantic-neighbors` | Similar entities | `entityId`, `limit?`, `graph?` |
| `embed-entities` | Embed all entities | `graph?` |
| `embed-entity` | Embed single entity | `entityId`, `graph?` |
| `embedding-status` | Check coverage | `graph?` |
| `flush-pending-embeddings` | Force pending | `graph?` |
| `retry-failed-embeddings` | Retry failures | `graph?` |
| `warm-embedder` | Pre-download the embedding model and run one query, ahead of a first `embed-entities`/search call | — |

---

## Semantic Discovery (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `semantic-gaps` | Similar but unconnected entities | `minSimilarity?`, `limit?`, `graph?` |
| `suggest-relations` | Propose missing relations | `limit?`, `graph?` |
| `propose-entities` | Suggest missing entities | `graph?` |

---

## Document Operations (10 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `ingest-document` | Ingest file (PDF, DOCX, MD, TXT, JSON, HTML) | `filePath`, `category?`, `graph?` |
| `ingest-directory` | Batch ingest | `dir_path`, `pattern?`, `category?`, `graph?` |
| `ingest-content` | Ingest string content | `content`, `source`, `category?`, `graph?` |
| `ingest-url` | Ingest from URL | `url`, `category?`, `graph?` |
| `reingest-document` | Re-ingest with change detection | `documentId`, `graph?` |
| `list-documents` | List ingested documents | `category?`, `graph?` |
| `delete-document` | Delete document | `documentId`, `graph?` |
| `extract-from-documents` | LLM extraction from docs | `category?`, `documentId?`, `query?`, `focus?`, `dryRun?`, `graph?` |
| `extract-preview` | Preview extraction | `category?`, `maxChunks?`, `focus?`, `graph?` |
| `extraction-rollback` | Rollback extraction | `graph?` |

---

## Graph Topology (8 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `analyze-centrality` | Hub detection (degree/betweenness/pagerank); returns ranked `[{id, name, entityType, score}]` | `algorithm`, `limit?`, `graph?` |
| `detect-cycles` | Find circular dependencies | `includePaths?`, `causalOnly?`, `graph?` |
| `detect-loops` | Find causal feedback loops | `graph?` |
| `detect-components` | Find connected components | `graph?` |
| `find-clusters` | Community detection | `graph?` |
| `list-loops` | List detected loops | `graph?` |
| `loop-details` | Detail for specific loop | `loopId`, `graph?` |
| `list-leverage-points` | Find intervention points | `level?`, `minLevel?`, `maxLevel?`, `depthCategory?`, `graph?` |

---

## Path Finding (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `find-shortest-path` | Shortest path A→B | `sourceId`, `targetId`, `graph?` |
| `find-all-paths` | All paths A→B | `sourceId`, `targetId`, `maxDepth?`, `graph?` |
| `explain-path` | Natural language explanation | `sourceId`, `targetId`, `graph?` |

---

## Epistemic Operations (11 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `uncertain-claims` | Low-confidence claims | `threshold?`, `graph?` |
| `contested-claims` | Claims with contradictions | `graph?` |
| `single-source-claims` | Single-source claims | `graph?` |
| `stale-beliefs` | Not updated recently | `daysOld?`, `graph?` |
| `unprovenanced` | Missing provenance | `graph?` |
| `most-certain` | Highest confidence | `limit?`, `graph?` |
| `needs-evidence` | Claims needing support | `graph?` |
| `inferred-claims` | Derived by inference | `graph?` |
| `claims-from-source` | Claims from a source | `sourceId`, `graph?` |
| `provenance-chain` | Trace lineage | `entityId`, `maxDepth?`, `graph?` |
| `propagate-credit` | Cascade confidence changes | `entityId`, `delta`, `maxDepth?`, `graph?` |

---

## Question Tracking (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `open-questions` | Unanswered questions | `graph?` |
| `answered-questions` | Answered questions | `graph?` |
| `blocking-questions` | Questions blocking other entities | `graph?` |

---

## Algebraic Traversal (10 tools)

Semiring-based graph computation for advanced path analysis.

| Tool | Description | Key Params |
|------|-------------|------------|
| `semiring-distances` | Shortest distances (tropical) | `sourceId`, `graph?` |
| `semiring-most-confident` | Most confident paths (Viterbi) | `sourceId`, `graph?` |
| `semiring-count-paths` | Count all paths | `sourceId`, `targetId?`, `graph?` |
| `semiring-reachable` | Boolean reachability | `sourceId`, `graph?` |
| `semiring-bottleneck` | Widest path (capacity) | `sourceId`, `graph?` |
| `semiring-traverse` | Generic semiring traversal | `sourceId`, `semiring`, `graph?` |
| `adaptive-traverse` | Adaptive traversal | `sourceId`, `graph?` |
| `adaptive-distances` | Adaptive distances | `sourceId`, `graph?` |
| `metapath-traverse` | Type-constrained traversal | `sourceId`, `pattern`, `graph?` |
| `cross-type-query` | Query across entity types | `sourceId`, `targetType?`, `graph?` |

**Semiring types:** `boolean` (reachability), `tropical` (shortest path), `viterbi` (most confident), `counting` (path count), `capacity` (widest bottleneck)

---

## Verification (8 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `verify-graph` | Full verification | `graph?` |
| `verify-fidelity` | Data integrity | `graph?` |
| `check-consistency` | Relation consistency | `graph?` |
| `check-invariants` | Graph invariants | `graph?` |
| `validate-spec` | Validate against spec | `spec`, `graph?` |
| `validate-mutation-trace` | Mutation history | `trace`, `graph?` |
| `propagate-constraints` | Constraint propagation | `graph?` |
| `list-guard-violations` | List violations | `graph?` |

---

## Composite Operations (11 tools)

Bundled multi-step analyses. **Prefer these over manual multi-step workflows.**

| Tool | Description | Key Params |
|------|-------------|------------|
| `graph-reconnaissance` | Full structural overview | `graph?` |
| `entity-deep-dive` | Complete entity analysis | `entityId`, `graph?` |
| `influence-map` | Multi-metric influence from seed | `entityId`, `maxDepth?`, `graph?` |
| `structural-survey` | Subgraph analysis | `entityId`, `depth?`, `target?`, `graph?` |
| `semantic-landscape` | Semantic overview | `seedEntity?`, `graph?` |
| `provenance-audit` | Full provenance audit | `entityId`, `graph?` |
| `multi-graph-landscape` | Cross-graph view | `graph?` |
| `verified-extract` | Extract + verify | `category?`, `graph?` |
| `gap-fill-cycle` | Detect + suggest + validate | `autoCreate?`, `graph?` |
| `simulate-change` | Simulate mutation effects | `entityId`, `mutation?`, `graph?` |
| `hypothesis-engine` | Gaps → propose → filter → dedup → rank | `maxResults?`, `minConfidence?`, `gapLimit?`, `strategies?`, `dedupThreshold?`, `dedupMode?`, `dedupEnabled?`, `graph?` |

---

## Synthesis (6 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `synthesize` | Generate synthesis from entities | `entityIds`, `graph?` |
| `plan-synthesis` | Plan synthesis operation | `goal`, `graph?` |
| `traverse-synthesis` | Synthesis along a path | `sourceId`, `targetId`, `graph?` |
| `synthesize-and-ingest` | Synthesize + ingest | `entityIds`, `graph?` |
| `decompose-query` | Break into sub-questions | `query`, `graph?` |
| `cegis-synthesize` | Counter-example guided | `spec`, `graph?` |

---

## Codebase Extraction (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `extract-codebase` | SCIP-based TS/JS extraction | `projectPath`, `graph?`, `includeTests?`, `include?`, `exclude?`, `dryRun?` |
| `update-codebase` | Incremental git diff update | `projectPath`, `graphName`, `gitRef?`, `embedAfterUpdate?`, `dryRun?` |
| `extraction-status` | Check progress | `graph?` |

---

## Consumption (4 tools)

One-call, token-budgeted comprehension answers over an extracted code graph. All four
address their symbol by `entityId` **or** `name` (exactly one).

| Tool | Description | Key Params |
|------|-------------|------------|
| `explore` | Everything about one symbol: definition, callers/callees (call-site anchored), imports/importedBy, contains/partOf, inheritance, and semantic-layer claims/patterns/tensions attached to it or its file — spent round-robin against a token budget | `entityId?`, `name?`, `budget?`, `graph?` |
| `find-callers` | Ranked callers of a symbol, each anchored at its call site; rolls up by file past the cap | `entityId?`, `name?`, `limit?` (default 30), `graph?` |
| `find-callees` | Ranked callees of a symbol, each anchored at its call site | `entityId?`, `name?`, `limit?` (default 30), `graph?` |
| `blast-radius` | Reverse dependency reach over `calls`/`requires`/`instance_of`, grouped by module, with hub suppression (99th-percentile degree, floor 8) | `entityId?`, `name?`, `depth?` (max 10), `limit?`, `hubPercentile?`, `graph?` |

`explore`'s response always keeps the queried entity and at least one row per populated
section; a `truncation` block reports what was cut (`applied`, `shown`/`total` per
section, `hint`). `blast-radius` reports `suppressedHubs` for any node it refused to
expand through.

---

## Work Memory (2 tools)

The experiential layer: what a piece of work concluded, which entities it leaned on,
and how that held up — recorded natively as graph evidence, then distilled with decay.

| Tool | Description | Key Params |
|------|-------------|------------|
| `record-outcome` | Record how a piece of work turned out, as a usage-tagged evidence entity citing the entities it used | `question`, `answer?`, `entityIds` (>=1), `outcome` (`useful`/`dead_end`/`corrected`), `correction?`, `graph?` |
| `reflect` | Aggregate recorded outcomes with exponential time decay, corroboration thresholds, and file-fingerprint staleness into standing `usage_status`/`usage_stale` observations (composite) | `halfLifeDays?`, `minCorroboration?`, `projectPath?`, `asOf?`, `dryRun?`, `graph?` |

`record-outcome` does not embed — run `embed-entities` afterward if the record needs to
be semantically searchable. `reflect` writes through the versioned update path, so its
standing observations are queryable history, not overwrites.

---

## Self-Improvement (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `self-improve` | Autonomous graph refinement | `graph?` |
| `self-model-update` | Update self-model | `graph?` |
| `postmortem-evaluate` | Evaluate session outcomes | `graph?` |

---

## Analogical Reasoning (4 tools)

Structural analogy, concept mapping, and creative substitution.

| Tool | Description | Key Params |
|------|-------------|------------|
| `find-frequent-subgraphs` | Mine recurring structural motifs | `frequencyThreshold?`, `maxMotifSize?` (2-5), `useNodeTypes?`, `useEdgeTypes?`, `nodeTypeFilter?`, `edgeTypeFilter?`, `timeout?`, `graph?` |
| `find-subgraph-matches` | Approximate subgraph isomorphism | `pattern` (nodes + edges), `nodeTypeWeight?`, `edgeTypeWeight?`, `topologyWeight?`, `minSimilarity?`, `maxResults?`, `graph?` |
| `cross-domain-mapping` | Map concepts between domains by structural role | `sourceDomain`, `targetDomain`, `degreeWeight?`, `relationProfileWeight?`, `neighborProfileWeight?`, `entityTypeWeight?`, `pairMinSimilarity?`, `graph?` |
| `concept-slippage` | Hofstadter-style concept substitution | `conceptId`, `temperature?` (0-1), `limit?`, `entityType?`, `relationType?`, `structuralWeight?`, `proximityWeight?`, `contextWeight?`, `graph?` |

**Temperature guide for `concept-slippage`:**
- 0.0–0.3: Strict — near-synonyms, same type
- 0.3–0.7: Role-based — structural analogues, different surface
- 0.7–1.0: Creative leaps — distant associations, cross-domain

---

## Bulk & Utility (5 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `bulk-import` | Batch create entities + relations | `entities`, `relations`, `graph?` |
| `extract-subgraph` | Extract subgraph around entity | `entityId`, `depth?`, `graph?` |
| `leverage-point-details` | Leverage point details | `entityId`, `graph?` |
| `session-changelog` | Track session changes | `graph?` |
| `check-capabilities` | Check available features | — |

---

## Visualization & Export (4 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `export-bundle` | Assemble the full TapestryBundle JSON (entities, relations, analytics, temporal, semantic) for a graph scope | `graph?`, `scope?`, `include?`, `title?`, `asOf?`, `maxEntities?` |
| `visualize` | Write a self-contained interactive HTML visualization of a graph scope | `output?`, `theme?` (`auto`/`dark`/`light`), plus `export-bundle`'s scoping params |
| `serve` | Serve the interactive visualization live over a read-only REST API | `host?`, `port?`, `graph?`, `check?` |
| `export-graph` | Write a compact, zero-infrastructure node-link JSON export — no FalkorDB or Loom CLI needed to read it back, unlike the bundle formats above | `output` (required), `graph?`, `includeSuperseded?` (default `false`), `entityTypes?`, `force?` |
