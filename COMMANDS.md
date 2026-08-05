# Command Catalog

Generated from the registry (`theloom/cli/registry.py`) — never hand-edit.

**163 registry commands** across 23 categories, plus the special `init` command.

## Adaptive Routing

- **`adaptive-distances`** — Distances with an auto-routed plan.
- **`adaptive-traverse`** — Traverse with an auto-routed plan.
- **`cross-type-query`** — Cross-category query with morphisms.
- **`metapath-traverse`** — Typed step-sequence traversal.
- **`type-analyze`** — Analyze a query into a routing plan.

## Composites

- **`analogy-transfer`** — Generate novel entities via CWSG analogy transfer from cross-domain mappings (composite).
- **`creativity-loop`** — Run the autonomous creativity loop: explore, retrieve, transfer, verify, learn (composite). UNAVAILABLE: the multi-cycle orchestration is not implemented, so every call returns OPERATION_ERROR.
- **`enrichment-crawl`** — Crawl frontier nodes and propose enrichment relations (composite). UNAVAILABLE with an LLM configured: the CISC N-sample crawl is not implemented and returns OPERATION_ERROR; only the no-LLM template-mode envelope works.
- **`entity-deep-dive`** — Comprehensive analysis of a single entity (composite).
- **`explore-frontier`** — Rank frontier regions by foraging signals with MVT advice and anti-pattern guards (composite).
- **`far-analogy-retrieval`** — Run the full far-analogy retrieval pipeline: fingerprint, match, slip, transfer, score (composite).
- **`gap-fill-cycle`** — Automated gap-filling with validation (composite). WRITES: a suggestion that clears the structural gate and the commitThreshold is created, so commitThreshold is a real mutation switch, not a report-only score.
- **`graph-reconnaissance`** — Comprehensive structural overview of a graph (composite).
- **`hypothesis-engine`** — Generate and rank hypotheses from semantic gaps (composite).
- **`influence-map`** — Map an entity's influence via semiring distances and bottlenecks (composite).
- **`multi-graph-landscape`** — Ecosystem-level overview of all graphs (composite).
- **`propose-entities`** — Propose new entities that should exist in the knowledge graph (composite).
- **`provenance-audit`** — Full provenance audit for an entity (composite).
- **`reflect`** — Distil recorded outcomes into standing lessons: time-decayed usage scores, preferred/contested/dead-end statuses, and staleness against changed files (composite).
- **`self-improve`** — Autonomous self-improvement cycle: reconnaissance, capability check, propose, simulate, rank, apply (composite).
- **`semantic-landscape`** — Semantic analysis overview of a graph (composite).
- **`simulate-change`** — Simulate graph mutations and preview structural impact (composite).
- **`structural-survey`** — Structural analysis around an entity: ego subgraph, cycles, paths (composite).
- **`verified-extract`** — Extract from documents then verify graph integrity (composite).

## Consumption

- **`blast-radius`** — Reverse dependency reach of a symbol over calls/requires/instance_of, grouped by module, with hub suppression.
- **`explore`** — Everything about one symbol in one call: definition, callers, callees, imports, containment, inheritance and the semantic layer, within a token budget.
- **`find-callees`** — Ranked list of the symbols this one calls, each anchored at its call site.
- **`find-callers`** — Ranked list of the symbols that call this one, each anchored at its call site.

## Documents

- **`analyze-category`** — Discover prevalent semantic themes in a document category.
- **`delete-document`** — Delete a document and all its chunks from the vector store.
- **`ingest-content`** — Ingest string content directly.
- **`ingest-directory`** — Batch-ingest all matching documents in a directory.
- **`ingest-document`** — Ingest a document file into the vector store.
- **`ingest-url`** — Fetch and ingest web content from a URL.
- **`list-documents`** — List all ingested documents with chunk counts.
- **`reingest-document`** — Re-ingest a document, comparing content and updating changed chunks.

## Embeddings

- **`embed-entities`** — Embed all entities in a graph.
- **`embed-entity`** — Embed a single entity by ID.
- **`embedding-reconcile`** — Reconcile entity status vs vector store.
- **`embedding-status`** — Embedding status counts for a graph.
- **`find-clusters`** — Discover semantic clusters.
- **`flush-pending-embeddings`** — Flush the pending embedding queue.
- **`list-dead-letters`** — List dead-letter queue entries.
- **`resolve-gaps`** — Create relations for semantic gaps.
- **`retry-failed-embeddings`** — Retry dead-lettered embeddings.
- **`semantic-gaps`** — Similar but unconnected entity pairs.
- **`suggest-relations`** — Suggest relations from patterns.
- **`warm-embedder`** — Pre-download and warm the embedding model.

## Entity Management

- **`bulk-import`** — Bulk import entities and relations into the knowledge graph.
- **`create-entity`** — Create a new entity in the knowledge graph.
- **`delete-entity`** — Retract an entity and its relations, preserving history (erase outright with "hard": true).
- **`list-entities`** — List entities with optional filtering.
- **`merge-entities`** — Merge a secondary entity into a primary one: union observations, redirect relations, supersede the secondary.
- **`read-entities-by-name`** — Resolve a batch of entity names to UUIDs.
- **`read-entity`** — Read an entity by its ID.
- **`update-entity`** — Update an existing entity.

## Epistemic Queries

- **`answered-questions`** — Find resolved questions.
- **`blocking-questions`** — Find questions blocking other work.
- **`claims-from-source`** — Find entities sourced from a source.
- **`contested-claims`** — Find claims with conflicting evidence.
- **`cross-session-contradictions`** — Contradictions across sessions.
- **`inferred-claims`** — Find inference-based entities.
- **`most-certain`** — Find the highest-confidence entities.
- **`needs-evidence`** — Find claims lacking supporting evidence.
- **`open-questions`** — Find active unanswered questions.
- **`postmortem-evaluate`** — Evaluate postmortem output utility.
- **`process-triggers`** — Dequeue pending analogy trigger candidates.
- **`propagate-credit`** — Propagate confidence through epistemic chains.
- **`provenance-chain`** — Trace the source chain from an entity.
- **`session-changelog`** — What changed since a timestamp.
- **`single-source-claims`** — Find claims depending on one source.
- **`stale-beliefs`** — Find entities not recently evaluated.
- **`trigger-status`** — Status of the analogy trigger queue.
- **`uncertain-claims`** — Find entities with low confidence scores.
- **`unprovenanced`** — Find entities without provenance.

## Extraction

- **`extract-codebase`** — Extract a codebase into a Loom knowledge graph via tree-sitter.
- **`extract-from-documents`** — Extract entities and relations from ingested documents using the LLM.
- **`extract-preview`** — Preview extraction results for the first chunks (dry run).
- **`extraction-rollback`** — Roll back an extraction run by deleting its created entities and relations.
- **`extraction-status`** — Show the status and progress of extraction runs.
- **`self-model-update`** — Update The Loom's self-referential codebase graph.
- **`update-codebase`** — Incrementally update an existing codebase graph from a git diff.

## Graph Analytics

- **`analyze-centrality`** — Analyze entity centrality.
- **`detect-components`** — Detect connected components.
- **`find-frequent-subgraphs`** — Find frequent subgraph motifs.
- **`graph-stats`** — Get statistics about the knowledge graph.
- **`reify-patterns`** — Reify recurring structural motifs as pattern entities.

## Graph Synthesis

- **`decompose-query`** — Decompose a complex query into ordered sub-questions based on graph structure.
- **`explain-leverage-point`** — Generate a natural language explanation of a leverage point — why it matters, what it affects, and its Meadows level context.
- **`explain-loop`** — Generate a natural language explanation of a feedback loop's dynamics — what reinforces or balances, entry points, and likely behavior.
- **`explain-path`** — Generate a step-by-step natural language explanation of a path between two entities.
- **`plan-synthesis`** — Plan a synthesis without executing.
- **`synthesize`** — Synthesize a coherent text output from the knowledge graph.
- **`synthesize-and-ingest`** — Synthesize text from the knowledge graph and ingest the output as new entities.
- **`traverse-synthesis`** — Plan and traverse a synthesis subgraph, returning evidence units and provenance.
- **`verify-fidelity`** — Check structural fidelity of text against the knowledge graph.

## Inference

- **`explain-inference`** — Explain a derived fact by walking its inference trace.
- **`inference-rule-create`** — Create a declarative inference rule.
- **`inference-rule-delete`** — Delete an inference rule by its entity id.
- **`inference-rule-list`** — List all inference rules stored in the graph.
- **`inference-trace-for-fact`** — Find the inference trace that produced a relation.
- **`inference-trace-get`** — Get full details of a specific inference trace.
- **`inference-trace-list`** — List inference traces.
- **`run-inference`** — Run the inference engine: evaluate enabled rules.

## Leverage Points

- **`leverage-point-details`** — Get details about a leverage point.
- **`list-leverage-points`** — List leverage point entities.

## Loop Analysis

- **`detect-cycles`** — Detect cycles in the knowledge graph.
- **`detect-loops`** — Detect and classify feedback loops.
- **`list-loops`** — List loop entities with metadata.
- **`loop-details`** — Get details about a loop entity.

## Multi-Graph

- **`create-graph`** — Create a new empty graph.
- **`delete-graph`** — Delete an existing graph.
- **`find-related-graphs`** — Find graphs connected to a graph via bridge relations.
- **`graph-connections`** — Get bridge counts between all connected graph pairs.
- **`list-bridges`** — List cross-graph bridge relations.
- **`list-graphs`** — List all available graphs with their loaded status and stats.

## Path Finding

- **`find-all-paths`** — Find all simple paths between entities.
- **`find-shortest-path`** — Find the shortest path between entities.

## Relation Management

- **`create-relation`** — Create a relation between two entities.
- **`create-relations`** — Create multiple relations in a single invocation.
- **`delete-relation`** — Retract a relation, preserving history (erase outright with "hard": true).
- **`get-neighbors`** — Get all entities connected to an entity.
- **`get-relations`** — Get all relations connected to an entity.
- **`list-relations`** — List relations with optional AND filters.
- **`read-relation`** — Read a relation by source and target entity IDs.
- **`read-relations`** — Read all relations between source and target entity IDs.
- **`update-relation`** — Update an existing relation.

## Search

- **`hybrid-search`** — Hybrid vector+keyword+graph search.
- **`semantic-neighbors`** — Similar but unconnected entities.
- **`semantic-search`** — Vector-only semantic search.

## Semiring Composition

- **`semiring-bottleneck`** — Widest-path bottleneck.
- **`semiring-count-paths`** — Count acyclic paths.
- **`semiring-distances`** — Single-source semiring distances.
- **`semiring-most-confident`** — Max-product confidence path.
- **`semiring-reachable`** — Boolean reachability with path.
- **`semiring-traverse`** — Traverse with a named semiring.
- **`transitive-closure`** — Boolean transitive closure pairs.

## Subgraph

- **`concept-slippage`** — Find concept slippage candidates.
- **`cross-domain-mapping`** — Map concepts between domains by structure.
- **`extract-subgraph`** — Extract a subgraph (causal/ego/typed).
- **`find-subgraph-matches`** — Find approximate subgraph matches.

## Symbolic Mathematics

- **`solve-problem`** — Solve a natural-language math problem via classify → translate → SymPy, with LLM fallback.
- **`symbolic-evaluate`** — Numerically evaluate an expression, optionally with substitutions.
- **`symbolic-expand`** — Expand a product or power expression using SymPy.
- **`symbolic-factor`** — Factor a polynomial expression using SymPy.
- **`symbolic-latex`** — Convert a mathematical expression to LaTeX notation.
- **`symbolic-simplify`** — Simplify a mathematical expression using SymPy.
- **`symbolic-solve`** — Solve an equation or system for variables using SymPy.
- **`symbolic-verify`** — Verify whether a proposed solution satisfies an equation.

## Verification

- **`cegis-synthesize`** — Counterexample-guided synthesis of a graph satisfying property specs.
- **`check-capabilities`** — Check capability invariants against the graph.
- **`check-consistency`** — Run Tier 1 consistency checks on the graph.
- **`check-invariants`** — Check specific named invariants against the graph.
- **`constrained-generate`** — Generate graph structure satisfying type constraints.
- **`list-guard-violations`** — Run all guards over every entity and relation.
- **`propagate-constraints`** — Run constraint propagation (AC-3) on type constraints.
- **`validate-mutation-trace`** — Replay a mutation trace and check invariants at each step.
- **`validate-spec`** — Validate the graph against property specifications.
- **`verify-graph`** — Verify the graph against guards and spec properties.

## Visualization

- **`export-bundle`** — Assemble the TapestryBundle JSON for a graph scope.
- **`serve`** — Serve the interactive visualization live over a read-only REST API.
- **`visualize`** — Write a self-contained interactive HTML visualization of a graph scope.

## Work Memory

- **`record-outcome`** — Record how a piece of work turned out as usage evidence citing the entities it leaned on (supports when useful, questions when not).
