# Tool Catalog

All Loom MCP tools grouped by function. CLI equivalents use kebab-case (e.g., `create_entity` → `create-entity`).

**CLI invocation:** `uv run loom <command> '<json>'`

---

## Entity & Relation CRUD (12 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `create_entity` | Create entity | `name`, `entityType`, `observations`, `confidence?`, `provenance?`, `graph?` |
| `read_entity` | Read by ID | `id`, `graph?` |
| `update_entity` | Update fields | `id`, `name?`, `observations?`, `confidence?`, `status?`, `graph?` |
| `delete_entity` | Delete entity | `id`, `graph?` |
| `list_entities` | Filter entities | `entityType?`, `query?`, `name?`, `statusFilter?`, `graph?` |
| `create_relation` | Create edge | `from`, `to`, `relationType`, `polarity`, `strength`, `evidence?`, `graph?` |
| `read_relation` | Read relation | `id`, `graph?` |
| `update_relation` | Update relation | `id`, `relationType?`, `strength?`, `graph?` |
| `delete_relation` | Delete relation | `id`, `graph?` |
| `list_relations` | Filter relations | `entityId?`, `relationType?`, `graph?` |
| `get_relations` | Relations for entity | `entityId`, `direction?`, `relationType?`, `graph?` |
| `get_neighbors` | Connected entities | `entityId`, `direction?`, `relationType?`, `graph?` |

---

## Graph Management (7 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `graph_stats` | Entity/relation counts + type distribution | `graph?` |
| `list_graphs` | List all graphs | — |
| `create_graph` | Create new graph | `name` |
| `delete_graph` | Delete graph | `name` |
| `list_bridges` | Cross-graph bridge relations | `graph?` |
| `find_related_graphs` | Find connected graphs | `graph?` |
| `graph_connections` | Cross-graph connection map | `graph?` |

---

## Semantic Operations (8 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `hybrid_search` | Vector + keyword + graph search | `query`, `entityType?`, `limit?`, `category?`, `graph?` |
| `semantic_search` | Pure vector similarity | `query`, `limit?`, `graph?` |
| `semantic_neighbors` | Similar entities | `entityId`, `limit?`, `graph?` |
| `embed_entities` | Embed all entities | `graph?` |
| `embed_entity` | Embed single entity | `entityId`, `graph?` |
| `embedding_status` | Check coverage | `graph?` |
| `flush_pending_embeddings` | Force pending | `graph?` |
| `retry_failed_embeddings` | Retry failures | `graph?` |

---

## Semantic Discovery (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `semantic_gaps` | Similar but unconnected entities | `minSimilarity?`, `limit?`, `graph?` |
| `suggest_relations` | Propose missing relations | `limit?`, `graph?` |
| `propose_entities` | Suggest missing entities | `graph?` |

---

## Document Operations (10 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `ingest_document` | Ingest file (PDF, DOCX, MD, TXT, JSON, HTML) | `filePath`, `category?`, `graph?` |
| `ingest_directory` | Batch ingest | `dir_path`, `pattern?`, `category?`, `graph?` |
| `ingest_content` | Ingest string content | `content`, `source`, `category?`, `graph?` |
| `ingest_url` | Ingest from URL | `url`, `category?`, `graph?` |
| `reingest_document` | Re-ingest with change detection | `documentId`, `graph?` |
| `list_documents` | List ingested documents | `category?`, `graph?` |
| `delete_document` | Delete document | `documentId`, `graph?` |
| `extract_from_documents` | LLM extraction from docs | `category?`, `documentId?`, `query?`, `focus?`, `dryRun?`, `graph?` |
| `extract_preview` | Preview extraction | `category?`, `maxChunks?`, `focus?`, `graph?` |
| `extraction_rollback` | Rollback extraction | `graph?` |

---

## Graph Topology (8 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `analyze_centrality` | Hub detection (degree/betweenness/pagerank) | `algorithm`, `limit?`, `graph?` |
| `detect_cycles` | Find circular dependencies | `includePaths?`, `causalOnly?`, `graph?` |
| `detect_loops` | Find causal feedback loops | `graph?` |
| `detect_components` | Find connected components | `graph?` |
| `find_clusters` | Community detection | `graph?` |
| `list_loops` | List detected loops | `graph?` |
| `loop_details` | Detail for specific loop | `loopId`, `graph?` |
| `list_leverage_points` | Find intervention points | `level?`, `minLevel?`, `maxLevel?`, `depthCategory?`, `graph?` |

---

## Path Finding (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `find_shortest_path` | Shortest path A→B | `sourceId`, `targetId`, `graph?` |
| `find_all_paths` | All paths A→B | `sourceId`, `targetId`, `maxDepth?`, `graph?` |
| `explain_path` | Natural language explanation | `sourceId`, `targetId`, `graph?` |

---

## Epistemic Operations (11 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `uncertain_claims` | Low-confidence claims | `threshold?`, `graph?` |
| `contested_claims` | Claims with contradictions | `graph?` |
| `single_source_claims` | Single-source claims | `graph?` |
| `stale_beliefs` | Not updated recently | `daysOld?`, `graph?` |
| `unprovenanced` | Missing provenance | `graph?` |
| `most_certain` | Highest confidence | `limit?`, `graph?` |
| `needs_evidence` | Claims needing support | `graph?` |
| `inferred_claims` | Derived by inference | `graph?` |
| `claims_from_source` | Claims from a source | `sourceId`, `graph?` |
| `provenance_chain` | Trace lineage | `entityId`, `maxDepth?`, `graph?` |
| `propagate_credit` | Cascade confidence changes | `entityId`, `delta`, `maxDepth?`, `graph?` |

---

## Question Tracking (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `open_questions` | Unanswered questions | `graph?` |
| `answered_questions` | Answered questions | `graph?` |
| `blocking_questions` | Questions blocking other entities | `graph?` |

---

## Algebraic Traversal (10 tools)

Semiring-based graph computation for advanced path analysis.

| Tool | Description | Key Params |
|------|-------------|------------|
| `semiring_distances` | Shortest distances (tropical) | `sourceId`, `graph?` |
| `semiring_most_confident` | Most confident paths (Viterbi) | `sourceId`, `graph?` |
| `semiring_count_paths` | Count all paths | `sourceId`, `targetId?`, `graph?` |
| `semiring_reachable` | Boolean reachability | `sourceId`, `graph?` |
| `semiring_bottleneck` | Widest path (capacity) | `sourceId`, `graph?` |
| `semiring_traverse` | Generic semiring traversal | `sourceId`, `semiring`, `graph?` |
| `adaptive_traverse` | Adaptive traversal | `sourceId`, `graph?` |
| `adaptive_distances` | Adaptive distances | `sourceId`, `graph?` |
| `metapath_traverse` | Type-constrained traversal | `sourceId`, `pattern`, `graph?` |
| `cross_type_query` | Query across entity types | `sourceId`, `targetType?`, `graph?` |

**Semiring types:** `boolean` (reachability), `tropical` (shortest path), `viterbi` (most confident), `counting` (path count), `capacity` (widest bottleneck)

---

## Verification (8 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `verify_graph` | Full verification | `graph?` |
| `verify_fidelity` | Data integrity | `graph?` |
| `check_consistency` | Relation consistency | `graph?` |
| `check_invariants` | Graph invariants | `graph?` |
| `validate_spec` | Validate against spec | `spec`, `graph?` |
| `validate_mutation_trace` | Mutation history | `trace`, `graph?` |
| `propagate_constraints` | Constraint propagation | `graph?` |
| `list_guard_violations` | List violations | `graph?` |

---

## Composite Operations (11 tools)

Bundled multi-step analyses. **Prefer these over manual multi-step workflows.**

| Tool | Description | Key Params |
|------|-------------|------------|
| `graph_reconnaissance` | Full structural overview | `graph?` |
| `entity_deep_dive` | Complete entity analysis | `entityId`, `graph?` |
| `influence_map` | Multi-metric influence from seed | `entityId`, `maxDepth?`, `graph?` |
| `structural_survey` | Subgraph analysis | `entityId`, `depth?`, `target?`, `graph?` |
| `semantic_landscape` | Semantic overview | `seedEntity?`, `graph?` |
| `provenance_audit` | Full provenance audit | `entityId`, `graph?` |
| `multi_graph_landscape` | Cross-graph view | `graph?` |
| `verified_extract` | Extract + verify | `category?`, `graph?` |
| `gap_fill_cycle` | Detect + suggest + validate | `autoCreate?`, `graph?` |
| `simulate_change` | Simulate mutation effects | `entityId`, `mutation?`, `graph?` |
| `hypothesis_engine` | Gaps → propose → filter → dedup → rank | `maxResults?`, `minConfidence?`, `gapLimit?`, `strategies?`, `dedupThreshold?`, `dedupMode?`, `dedupEnabled?`, `graph?` |

---

## Synthesis (6 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `synthesize` | Generate synthesis from entities | `entityIds`, `graph?` |
| `plan_synthesis` | Plan synthesis operation | `goal`, `graph?` |
| `traverse_synthesis` | Synthesis along a path | `sourceId`, `targetId`, `graph?` |
| `synthesize_and_ingest` | Synthesize + ingest | `entityIds`, `graph?` |
| `decompose_query` | Break into sub-questions | `query`, `graph?` |
| `cegis_synthesize` | Counter-example guided | `spec`, `graph?` |

---

## Codebase Extraction (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `extract_codebase` | SCIP-based TS/JS extraction | `projectPath`, `graph?`, `includeTests?`, `include?`, `exclude?`, `dryRun?` |
| `update_codebase` | Incremental git diff update | `projectPath`, `graphName`, `gitRef?`, `embedAfterUpdate?`, `dryRun?` |
| `extraction_status` | Check progress | `graph?` |

---

## Self-Improvement (3 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `self_improve` | Autonomous graph refinement | `graph?` |
| `self_model_update` | Update self-model | `graph?` |
| `postmortem_evaluate` | Evaluate session outcomes | `graph?` |

---

## Analogical Reasoning (4 tools)

Structural analogy, concept mapping, and creative substitution.

| Tool | Description | Key Params |
|------|-------------|------------|
| `find_frequent_subgraphs` | Mine recurring structural motifs | `frequencyThreshold?`, `maxMotifSize?` (2-5), `useNodeTypes?`, `useEdgeTypes?`, `nodeTypeFilter?`, `edgeTypeFilter?`, `timeout?`, `graph?` |
| `find_subgraph_matches` | Approximate subgraph isomorphism | `pattern` (nodes + edges), `nodeTypeWeight?`, `edgeTypeWeight?`, `topologyWeight?`, `minSimilarity?`, `maxResults?`, `graph?` |
| `cross_domain_mapping` | Map concepts between domains by structural role | `sourceDomain`, `targetDomain`, `degreeWeight?`, `relationProfileWeight?`, `neighborProfileWeight?`, `entityTypeWeight?`, `pairMinSimilarity?`, `graph?` |
| `concept_slippage` | Hofstadter-style concept substitution | `conceptId`, `temperature?` (0-1), `limit?`, `entityType?`, `relationType?`, `structuralWeight?`, `proximityWeight?`, `contextWeight?`, `graph?` |

**Temperature guide for `concept_slippage`:**
- 0.0–0.3: Strict — near-synonyms, same type
- 0.3–0.7: Role-based — structural analogues, different surface
- 0.7–1.0: Creative leaps — distant associations, cross-domain

---

## Bulk & Utility (5 tools)

| Tool | Description | Key Params |
|------|-------------|------------|
| `bulk_import` | Batch create entities + relations | `entities`, `relations`, `graph?` |
| `extract_subgraph` | Extract subgraph around entity | `entityId`, `depth?`, `graph?` |
| `leverage_point_details` | Leverage point details | `entityId`, `graph?` |
| `session_changelog` | Track session changes | `graph?` |
| `check_capabilities` | Check available features | — |
