# Command Catalog

Generated from the registry (`theloom/cli/registry.py`) — never hand-edit.

**164 registry commands** across 23 categories, plus the special `init` command.

Each command lists its input fields below its summary: dotted paths (`confidence.score`) descend into nested objects, `[]` (`relations[].from`) marks an array of objects. `required`/`optional` is scoped to the field's immediate parent — a required field of an optional object only applies once that object is supplied at all. Run `loom <command> --schema` for the raw JSON Schema (with full `$defs`) behind any entry.

## Adaptive Routing

- **`adaptive-distances`** — Distances with an auto-routed plan.
  - `source` — string; required
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `maxDepth` — integer | null; optional
  - `pathMode` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`adaptive-traverse`** — Traverse with an auto-routed plan.
  - `source` — string; required
  - `target` — string | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `maxDepth` — integer | null; optional
  - `pathMode` — string | null; optional
  - `productMode` — boolean | null; optional
  - `graph` — string | null; optional

- **`cross-type-query`** — Cross-category query with morphisms.
  - `source` — string; required
  - `target` — string | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `maxDepth` — integer | null; optional
  - `pathMode` — string | null; optional
  - `graph` — string | null; optional

- **`metapath-traverse`** — Typed step-sequence traversal.
  - `source` — string; required
  - `metapath` — string | object; required
  - `maxDepth` — integer | null; optional
  - `sourceEntityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `target` — string | null; optional
  - `graph` — string | null; optional

- **`type-analyze`** — Analyze a query into a routing plan.
  - `source` — string | null; optional
  - `target` — string | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `metapath` — string | object | null; optional
  - `graph` — string | null; optional

## Composites

- **`analogy-transfer`** — Generate novel entities via CWSG analogy transfer from cross-domain mappings (composite).
  - `sourceDomain` — object; required
  - `targetDomain` — object; required
  - `temperature` — number | null; optional
  - `graph` — string | null; optional

- **`creativity-loop`** — Run the autonomous creativity loop: explore, retrieve, transfer, score, accept/reject, learn (composite). Read-only and deterministic — no LLM; it stops early on consecutive empty cycles or a plateau. The analogy trigger queue is reported per cycle, never drained.
  - `graph` — string | null; optional
  - `maxCycles` — integer | null; optional
  - `maxEmptyCycles` — integer | null; optional
  - `acceptanceThreshold` — number | null; optional
  - `slippageTemperature` — number | null; optional
  - `retrieveMaxCandidates` — integer | null; optional
  - `maxProposalsPerCycle` — integer | null; optional
  - `exploreTopK` — integer | null; optional
  - `detectPlateau` — boolean | null; optional
  - `purpose` — string | null; optional
  - `generalizationBias` — number | null; optional

- **`enrichment-crawl`** — Crawl under-described frontier nodes and propose enrichment relations (composite). Needs no LLM: candidates come from structural closure plus semantic neighbours, so CISC N-sample voting is not applied and numSamples spends nothing (reported as a boundary). WRITES when dryRun is false (default true): each surviving candidate is created via create-relation.
  - `maxNodes` — integer | null; optional
  - `maxCandidates` — integer | null; optional
  - `numSamples` — integer | null; optional
  - `minConfidence` — number | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`entity-deep-dive`** — Comprehensive analysis of a single entity (composite).
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `graph` — string | null; optional
  - `full` — boolean | null; optional

- **`explore-frontier`** — Rank frontier regions by foraging signals with MVT advice and anti-pattern guards (composite).
  - `graph` — string | null; optional
  - `topK` — integer | null; optional
  - `includeMvt` — boolean | null; optional
  - `includeAntiPatterns` — boolean | null; optional
  - `purpose` — string | null; optional

- **`far-analogy-retrieval`** — Run the full far-analogy retrieval pipeline: fingerprint, match, slip, transfer, score (composite).
  - `graph` — string | null; optional
  - `maxCandidates` — integer | null; optional
  - `minStructuralSimilarity` — number | null; optional
  - `slippageTemperature` — number | null; optional
  - `maxProposals` — integer | null; optional
  - `useSemanticFingerprint` — boolean | null; optional
  - `purpose` — string | null; optional
  - `explorationBoosted` — boolean | null; optional
  - `bridgingBoost` — number | null; optional

- **`gap-fill-cycle`** — Automated gap-filling with validation (composite). WRITES: a suggestion that clears the structural gate and the commitThreshold is created, so commitThreshold is a real mutation switch, not a report-only score.
  - `seedEntity` — string | null; optional
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `autoCreate` — boolean | null; optional
  - `commitThreshold` — number | null; optional
  - `graph` — string | null; optional

- **`graph-reconnaissance`** — Comprehensive structural overview of a graph (composite).
  - `graph` — string | null; optional
  - `centralityLimit` — integer | null; optional

- **`hypothesis-engine`** — Generate and rank hypotheses from semantic gaps (composite).
  - `graph` — string | null; optional
  - `maxResults` — integer | null; optional
  - `minConfidence` — number | null; optional
  - `gapLimit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `strategies` — array<enum(pattern_completion, llm_reasoning)> | null; optional
  - `dedupThreshold` — number | null; optional
  - `dedupMode` — enum(reject, flag, merge) | null; optional
  - `dedupEnabled` — boolean | null; optional
  - `siWeight` — number | null; optional
  - `structuralWeight` — number | null; optional
  - `compressionWeight` — number | null; optional
  - `embeddingsAvailable` — boolean | null; optional
  - `simulate` — boolean | null; optional

- **`influence-map`** — Map an entity's influence via semiring distances and bottlenecks (composite).
  - `entityId` — string; required
  - `maxDepth` — integer | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`multi-graph-landscape`** — Ecosystem-level overview of all graphs (composite).
  - `graph` — string | null; optional

- **`propose-entities`** — Propose new entities that should exist in the knowledge graph (composite).
  - `limit` — integer | null; optional, default: 10
  - `simulate` — boolean | null; optional, default: false
  - `strategies` — array<enum(pattern_completion, llm_reasoning)> | null; optional
  - `graph` — string | null; optional
  - `minPatternOccurrences` — integer | null; optional, default: 2
  - `maxPatterns` — integer | null; optional, default: 20

- **`provenance-audit`** — Full provenance audit for an entity (composite).
  - `entityId` — string; required
  - `maxDepth` — integer | null; optional
  - `delta` — number | null; optional
  - `graph` — string | null; optional

- **`reflect`** — Distil recorded outcomes into standing lessons: time-decayed usage scores, preferred/contested/dead-end statuses, and staleness against changed files (composite).
  - `graph` — string | null; optional
  - `halfLifeDays` — number | null; optional
  - `minCorroboration` — integer | null; optional
  - `projectPath` — string | null; optional
  - `asOf` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`self-improve`** — Autonomous self-improvement cycle: reconnaissance, capability check, propose, simulate, rank, apply (composite).
  - `graph` — string | null; optional
  - `autoApply` — boolean | null; optional, default: false
  - `maxProposals` — integer | null; optional
  - `applyTopN` — integer | null; optional

- **`semantic-landscape`** — Semantic analysis overview of a graph (composite).
  - `seedEntity` — string | null; optional
  - `category` — string | null; optional
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `graph` — string | null; optional

- **`simulate-change`** — Simulate graph mutations and preview structural impact (composite).
  - `mutations` — array<object>; required
  - `mutations[].type` — enum(createEntity, updateEntity, deleteEntity, createRelation, deleteRelation); required
  - `mutations[].payload` — object; required
  - `graph` — string | null; optional

- **`structural-survey`** — Structural analysis around an entity: ego subgraph, cycles, paths (composite).
  - `entityId` — string; required
  - `depth` — integer | null; optional
  - `target` — string | null; optional
  - `maxDepth` — integer | null; optional
  - `metapathPatterns` — array<string> | null; optional
  - `graph` — string | null; optional

- **`verified-extract`** — Extract from documents then verify graph integrity (composite).
  - `category` — string | null; optional
  - `documentId` — string | null; optional
  - `query` — string | null; optional
  - `entityTypes` — array<string> | null; optional
  - `maxChunks` — integer | null; optional
  - `model` — string | null; optional
  - `sectionSynthesis` — string | null; optional
  - `contextWindowSize` — integer | null; optional
  - `focus` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `creditDelta` — number | null; optional
  - `creditDampingFactor` — number | null; optional
  - `creditMaxDepth` — integer | null; optional
  - `creditDryRun` — boolean | null; optional
  - `graph` — string | null; optional

## Consumption

- **`blast-radius`** — Reverse dependency reach of a symbol over calls/requires/instance_of, grouped by module, with hub suppression.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `depth` — integer | null; optional
  - `limit` — integer | null; optional
  - `hubPercentile` — number | null; optional
  - `graph` — string | null; optional

- **`explore`** — Everything about one symbol in one call: definition, callers, callees, imports, containment, inheritance and the semantic layer, within a token budget.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `budget` — integer | null; optional
  - `graph` — string | null; optional

- **`find-callees`** — Ranked list of the symbols this one calls, each anchored at its call site.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`find-callers`** — Ranked list of the symbols that call this one, each anchored at its call site.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

## Documents

- **`analyze-category`** — Discover prevalent semantic themes in a document category.
  - `category` — string; required
  - `topK` — integer | null; optional
  - `similarityThreshold` — number | null; optional
  - `minClusterSize` — integer | null; optional
  - `maxChunks` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`delete-document`** — Delete a document and all its chunks from the vector store.
  - `source_id` — string; required
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-content`** — Ingest string content directly.
  - `content` — string; required
  - `source_id` — string; required
  - `format` — string; required
  - `category` — string | null; optional
  - `title` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-directory`** — Batch-ingest all matching documents in a directory.
  - `dir_path` — string; required
  - `pattern` — string | null; optional
  - `category` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-document`** — Ingest a document file into the vector store.
  - `file_path` — string; required
  - `category` — string | null; optional
  - `title` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-url`** — Fetch and ingest web content from a URL.
  - `url` — string; required
  - `category` — string | null; optional
  - `title` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`list-documents`** — List all ingested documents with chunk counts.
  - `category` — string | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`reingest-document`** — Re-ingest a document, comparing content and updating changed chunks.
  - `source_id` — string; required
  - `file_path` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

## Embeddings

- **`embed-entities`** — Embed all entities in a graph.
  - `entityType` — string | null; optional
  - `forceReembed` — boolean | null; optional
  - `graph` — string | null; optional

- **`embed-entity`** — Embed a single entity by ID.
  - `id` — string; required
  - `graph` — string | null; optional

- **`embedding-reconcile`** — Reconcile entity status vs vector store.
  - `dryRun` — boolean | null; optional
  - `cleanOrphans` — boolean | null; optional
  - `graph` — string | null; optional

- **`embedding-status`** — Embedding status counts for a graph.
  - `graph` — string | null; optional

- **`find-clusters`** — Discover semantic clusters.
  - `similarityThreshold` — number | null; optional
  - `minClusterSize` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | null; optional

- **`flush-pending-embeddings`** — Flush the pending embedding queue.
  - `graph` — string | null; optional

- **`list-dead-letters`** — List dead-letter queue entries.
  - `graph` — string | null; optional

- **`resolve-gaps`** — Create relations for semantic gaps.
  - `threshold` — number | null; optional
  - `maxResolutions` — integer | null; optional
  - `relationTypeHint` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`retry-failed-embeddings`** — Retry dead-lettered embeddings.
  - `graph` — string | null; optional

- **`semantic-gaps`** — Similar but unconnected entity pairs.
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `maxEntities` — integer | null; optional
  - `seed` — integer | null; optional
  - `graph` — string | null; optional

- **`suggest-relations`** — Suggest relations from patterns.
  - `entityId` — string; required
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `targetEntityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `graph` — string | null; optional

- **`warm-embedder`** — Pre-download and warm the embedding model.

## Entity Management

- **`bulk-import`** — Bulk import entities and relations into the knowledge graph.
  - `entities` — array<object> | null; optional
  - `relations` — array<object> | null; optional
  - `jsonlInput` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`create-entity`** — Create a new entity in the knowledge graph.
  - `name` — string; required
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session); required
  - `observations` — array<string>; required
  - `confidence` — object | null; optional
  - `confidence.score` — number; required
  - `confidence.basis` — enum(direct_observation, peer_reviewed, multiple_sources, single_source, inference, speculation, llm_extraction, calculated); required
  - `confidence.lastEvaluated` — string | null; optional
  - `provenance` — object | null; optional
  - `provenance.sourceType` — string; required
  - `provenance.sourceId` — string | null; required
  - `provenance.externalRef` — string | null; required
  - `provenance.extractionDate` — string | null; optional
  - `provenance.extractor` — string; required
  - `provenance.extractionMethod` — string | null; required
  - `session` — string | null; optional
  - `version` — integer | null; optional
  - `previousVersionId` — string | null; optional
  - `changeType` — string | null; optional
  - `changeReason` — string | null; optional
  - `memoryType` — enum(experience, knowledge, technique, decision, insight, principle, intention, encounter) | null; optional — 3D Memory Machine axis 1: what cognitive function does this serve?
  - `domain` — enum(engineering, practice, research, relationship, operations, creative) | null; optional — 3D Memory Machine axis 2: what area of life does this belong to?
  - `durability` — enum(permanent, stable, current, volatile) | null; optional — 3D Memory Machine axis 3: how long will this remain valid?
  - `expiresAt` — string | null; optional
  - `graph` — string | null; optional

- **`delete-entity`** — Retract an entity and its relations, preserving history (erase outright with "hard": true).
  - `id` — string; required
  - `hard` — boolean | null; optional
  - `graph` — string | null; optional

- **`list-entities`** — List entities with optional filtering.
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `name` — string | null; optional
  - `query` — string | null; optional
  - `sourcedFrom` — array<string> | null; optional
  - `excludeSourcedFrom` — array<string> | null; optional
  - `includeSuperseded` — boolean | null; optional
  - `includeDeprecated` — boolean | null; optional
  - `includeRetracted` — boolean | null; optional
  - `includeInvestigating` — boolean | null; optional
  - `version` — integer | null; optional
  - `minVersion` — integer | null; optional
  - `session` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional
  - `compact` — boolean | null; optional

- **`merge-entities`** — Merge a secondary entity into a primary one: union observations, redirect relations, supersede the secondary.
  - `primary` — string; required
  - `secondary` — string; required
  - `graph` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`read-entities-by-name`** — Resolve a batch of entity names to UUIDs.
  - `names` — array<string>; required
  - `graph` — string | null; optional

- **`read-entity`** — Read an entity by its ID.
  - `id` — string | null; optional
  - `name` — string | null; optional
  - `graph` — string | null; optional
  - `compact` — boolean | null; optional

- **`update-entity`** — Update an existing entity.
  - `id` — string; required
  - `name` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `observations` — array<string> | null; optional
  - `confidence` — object | null; optional
  - `confidence.score` — number; required
  - `confidence.basis` — enum(direct_observation, peer_reviewed, multiple_sources, single_source, inference, speculation, llm_extraction, calculated); required
  - `confidence.lastEvaluated` — string | null; optional
  - `status` — enum(active, superseded, deprecated, retracted, investigating) | null; optional
  - `statusReason` — string | null; optional
  - `provenance` — object | null; optional
  - `provenance.sourceType` — string; required
  - `provenance.sourceId` — string | null; required
  - `provenance.externalRef` — string | null; required
  - `provenance.extractionDate` — string | null; optional
  - `provenance.extractor` — string; required
  - `provenance.extractionMethod` — string | null; required
  - `changeType` — string | null; optional
  - `changeReason` — string | null; optional
  - `replacedById` — string | null; optional
  - `graph` — string | null; optional

## Epistemic Queries

- **`answered-questions`** — Find resolved questions.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `since` — string | null; optional

- **`blocking-questions`** — Find questions blocking other work.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `domain` — string | null; optional

- **`claims-from-source`** — Find entities sourced from a source.
  - `sourceId` — string; required
  - `limit` — integer | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`contested-claims`** — Find claims with conflicting evidence.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`cross-session-contradictions`** — Contradictions across sessions.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `minConfidence` — number | null; optional
  - `sessionIds` — array<string> | null; optional
  - `maxDepth` — integer | null; optional

- **`inferred-claims`** — Find inference-based entities.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`most-certain`** — Find the highest-confidence entities.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `topK` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`needs-evidence`** — Find claims lacking supporting evidence.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `minSupports` — integer | null; optional
  - `claimId` — string | null; optional

- **`open-questions`** — Find active unanswered questions.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`postmortem-evaluate`** — Evaluate postmortem output utility.
  - `graph` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`process-triggers`** — Dequeue pending analogy trigger candidates.
  - `graph` — string | null; optional
  - `limit` — integer | null; optional

- **`propagate-credit`** — Propagate confidence through epistemic chains.
  - `entityIds` — array<string>; required
  - `delta` — number; required
  - `dampingFactor` — number | null; optional
  - `maxDepth` — integer | null; optional
  - `minDelta` — number | null; optional
  - `dryRun` — boolean | null; optional — Preview the propagation without persisting anything. Defaults to false: a call with no dryRun (or dryRun: false) computes AND PERSISTS the propagated confidence changes immediately — this is a mutating command by default, consistent with the other mutating epistemic commands (postmortem-evaluate, session-changelog). Pass dryRun: true to compute the would-be newConfidence values without writing them. Either way the response carries an `applied` marker (true iff a write actually happened) and, on a simulated run, a DRY_RUN notice.
  - `relationTypes` — array<string> | null; optional
  - `propagationMode` — string | null; optional
  - `graph` — string | null; optional

- **`provenance-chain`** — Trace the source chain from an entity.
  - `entityId` — string; required
  - `maxDepth` — integer | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`session-changelog`** — What changed since a timestamp.
  - `since` — string | null; optional
  - `session` — string | null; optional
  - `graph` — string | null; optional
  - `includeRelations` — boolean | null; optional
  - `dryRun` — boolean | null; optional

- **`single-source-claims`** — Find claims depending on one source.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`stale-beliefs`** — Find entities not recently evaluated.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `daysOld` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`trigger-status`** — Status of the analogy trigger queue.
  - `graph` — string | null; optional

- **`uncertain-claims`** — Find entities with low confidence scores.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `threshold` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`unprovenanced`** — Find entities without provenance.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

## Extraction

- **`extract-codebase`** — Extract a codebase into a Loom knowledge graph via tree-sitter.
  - `projectPath` — string; required
  - `graph` — string | null; optional
  - `includeTests` — boolean | null; optional
  - `include` — array<string> | null; optional — Only collect files whose project-relative path matches one of these fnmatch globs (e.g. "src/*", "**/*.py"); unset or empty means no restriction.
  - `exclude` — array<string> | null; optional — Never collect files whose project-relative path matches one of these fnmatch globs; takes priority over `include` when a path matches both.
  - `dryRun` — boolean | null; optional

- **`extract-from-documents`** — Extract entities and relations from ingested documents using the LLM.
  - `category` — string | null; optional
  - `documentId` — string | null; optional
  - `query` — string | null; optional
  - `maxChunks` — integer | null; optional
  - `model` — string | null; optional
  - `focus` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`extract-preview`** — Preview extraction results for the first chunks (dry run).
  - `category` — string | null; optional
  - `documentId` — string | null; optional
  - `query` — string | null; optional
  - `maxChunks` — integer | null; optional
  - `model` — string | null; optional
  - `focus` — string | null; optional
  - `graph` — string | null; optional

- **`extraction-rollback`** — Roll back an extraction run by deleting its created entities and relations.
  - `runId` — string; required
  - `graph` — string | null; optional

- **`extraction-status`** — Show the status and progress of extraction runs.
  - `runId` — string | null; optional

- **`self-model-update`** — Update The Loom's self-referential codebase graph.
  - `projectPath` — string | null; optional
  - `graphName` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`update-codebase`** — Incrementally update an existing codebase graph from a git diff.
  - `projectPath` — string; required
  - `graphName` — string; required
  - `gitRef` — string | null; optional
  - `includeTests` — boolean | null; optional
  - `include` — array<string> | null; optional — Only collect files whose project-relative path matches one of these fnmatch globs (e.g. "src/*", "**/*.py"); unset or empty means no restriction.
  - `exclude` — array<string> | null; optional — Never collect files whose project-relative path matches one of these fnmatch globs; takes priority over `include` when a path matches both.
  - `dryRun` — boolean | null; optional
  - `force` — boolean | null; optional

## Graph Analytics

- **`analyze-centrality`** — Analyze entity centrality.
  - `algorithm` — string | null; optional
  - `metric` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`detect-components`** — Detect connected components.
  - `strong` — boolean | null; optional
  - `graph` — string | null; optional

- **`find-frequent-subgraphs`** — Find frequent subgraph motifs.
  - `frequencyThreshold` — integer | null; optional
  - `maxMotifSize` — integer | null; optional
  - `useNodeTypes` — boolean | null; optional
  - `useEdgeTypes` — boolean | null; optional
  - `nodeTypeFilter` — array<string> | null; optional
  - `edgeTypeFilter` — array<string> | null; optional
  - `timeout` — integer | null; optional
  - `maxInstances` — integer | null; optional
  - `graph` — string | null; optional

- **`graph-stats`** — Get statistics about the knowledge graph.
  - `graph` — string | null; optional

- **`reify-patterns`** — Reify recurring structural motifs as pattern entities.
  - `minOccurrences` — integer | null; optional
  - `maxDepth` — integer | null; optional
  - `maxPatterns` — integer | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

## Graph Synthesis

- **`decompose-query`** — Decompose a complex query into ordered sub-questions based on graph structure.
  - `query` — string; required
  - `graph` — string | array<string> | null; optional

- **`explain-leverage-point`** — Generate a natural language explanation of a leverage point — why it matters, what it affects, and its Meadows level context.
  - `leveragePointId` — string; required
  - `graph` — string | null; optional

- **`explain-loop`** — Generate a natural language explanation of a feedback loop's dynamics — what reinforces or balances, entry points, and likely behavior.
  - `loopId` — string; required
  - `graph` — string | null; optional

- **`explain-path`** — Generate a step-by-step natural language explanation of a path between two entities.
  - `sourceId` — string | null; optional
  - `targetId` — string | null; optional
  - `sourceName` — string | null; optional
  - `targetName` — string | null; optional
  - `path` — array<string> | null; optional
  - `graph` — string | array<string> | null; optional

- **`plan-synthesis`** — Plan a synthesis without executing.
  - `query` — string; required
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `graph` — string | array<string> | null; optional

- **`synthesize`** — Synthesize a coherent text output from the knowledge graph.
  - `query` — string; required
  - `format` — enum(narrative, outline, evidence_map, causal_chain, raw, proposal) | null; optional
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `mode` — enum(systematic, adaptive) | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | array<string> | null; optional

- **`synthesize-and-ingest`** — Synthesize text from the knowledge graph and ingest the output as new entities.
  - `query` — string; required
  - `format` — enum(narrative, outline, evidence_map, causal_chain, raw, proposal) | null; optional
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `mode` — enum(systematic, adaptive) | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | array<string> | null; optional

- **`traverse-synthesis`** — Plan and traverse a synthesis subgraph, returning evidence units and provenance.
  - `query` — string; required
  - `mode` — enum(systematic, adaptive) | null; optional
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | array<string> | null; optional

- **`verify-fidelity`** — Check structural fidelity of text against the knowledge graph.
  - `text` — string; required
  - `entityIds` — array<string> | null; optional
  - `mode` — enum(structural, narrative) | null; optional
  - `graph` — string | array<string> | null; optional

## Inference

- **`explain-inference`** — Explain a derived fact by walking its inference trace.
  - `relationId` — string; required
  - `graph` — string | null; optional

- **`inference-rule-create`** — Create a declarative inference rule.
  - `rule` — object; required
  - `rule.name` — string; required
  - `rule.description` — string; required
  - `rule.conditions` — array<object>; required
  - `rule.conditions[].from` — string; required
  - `rule.conditions[].to` — string; required
  - `rule.conditions[].relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `rule.conclusion` — object; required
  - `rule.conclusion.from` — string; required
  - `rule.conclusion.to` — string; required
  - `rule.conclusion.relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `rule.conclusion.strength` — string; required
  - `rule.conclusion.evidence` — string; required
  - `rule.conclusion.polarity` — string | null; optional
  - `rule.enabled` — boolean | null; optional
  - `graph` — string | null; optional

- **`inference-rule-delete`** — Delete an inference rule by its entity id.
  - `ruleId` — string; required
  - `graph` — string | null; optional

- **`inference-rule-list`** — List all inference rules stored in the graph.
  - `graph` — string | null; optional

- **`inference-trace-for-fact`** — Find the inference trace that produced a relation.
  - `relationId` — string; required
  - `graph` — string | null; optional

- **`inference-trace-get`** — Get full details of a specific inference trace.
  - `traceId` — string; required
  - `graph` — string | null; optional

- **`inference-trace-list`** — List inference traces.
  - `ruleId` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`run-inference`** — Run the inference engine: evaluate enabled rules.
  - `dryRun` — boolean | null; optional — Preview a run without persisting anything. Defaults to false: a call with no dryRun (or dryRun: false) matches rules AND PERSISTS the derived relations plus an inference_trace entity recording the run. Pass dryRun: true to preview the derived relations without writing an inference_trace entity or any derived relations — the would-be trace payload is still returned, unpersisted, as `tracePreview` (traceId stays null since nothing was written). Either way the response carries an `applied` marker (true only on a real, persisted run) and, on a simulated run, a DRY_RUN notice.
  - `ruleId` — string | null; optional
  - `graph` — string | null; optional

## Leverage Points

- **`leverage-point-details`** — Get details about a leverage point.
  - `leveragePointId` — string; required
  - `includeTargets` — boolean | null; optional
  - `graph` — string | null; optional

- **`list-leverage-points`** — List leverage point entities.
  - `level` — integer | null; optional
  - `minLevel` — integer | null; optional
  - `maxLevel` — integer | null; optional
  - `depthCategory` — string | null; optional
  - `targetEntity` — string | null; optional
  - `graph` — string | null; optional

## Loop Analysis

- **`detect-cycles`** — Detect cycles in the knowledge graph.
  - `graph` — string | null; optional
  - `includePaths` — boolean | null; optional
  - `causalOnly` — boolean | null; optional

- **`detect-loops`** — Detect and classify feedback loops.
  - `graph` — string | null; optional
  - `persist` — boolean | null; optional — Persist detected loops as loop entities (plus part_of member relations) so list-loops and loop-details can find them afterward. Defaults to false: a detect-loops call with no persist key still returns full loop data (each loop has id: null and persisted: false) but writes nothing to the graph -- a following list-loops call will NOT see these loops until you re-run detect-loops with "persist": true. When results are not persisted, the response carries applied: false and a NOT_PERSISTED notice naming this flag as the fix.
  - `minSize` — integer | null; optional
  - `maxSize` — integer | null; optional

- **`list-loops`** — List loop entities with metadata.
  - `classification` — string | null; optional
  - `throughEntity` — string | null; optional
  - `minSize` — integer | null; optional
  - `maxSize` — integer | null; optional
  - `graph` — string | null; optional

- **`loop-details`** — Get details about a loop entity.
  - `loopId` — string; required
  - `includeMembers` — boolean | null; optional
  - `graph` — string | null; optional

## Multi-Graph

- **`create-graph`** — Create a new empty graph.
  - `name` — string; required

- **`delete-graph`** — Delete an existing graph.
  - `name` — string; required

- **`find-related-graphs`** — Find graphs connected to a graph via bridge relations.
  - `graph` — string; required

- **`graph-connections`** — Get bridge counts between all connected graph pairs.

- **`list-bridges`** — List cross-graph bridge relations.
  - `from_graph` — string | null; optional
  - `to_graph` — string | null; optional
  - `entity_id` — string | null; optional

- **`list-graphs`** — List all available graphs with their loaded status and stats.

## Path Finding

- **`find-all-paths`** — Find all simple paths between entities.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `maxPaths` — integer | null; optional
  - `timeout` — integer | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional

- **`find-shortest-path`** — Find the shortest path between entities.
  - `source` — string | null; optional
  - `target` — string | null; optional
  - `sourceName` — string | null; optional
  - `targetName` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional

## Relation Management

- **`create-relation`** — Create a relation between two entities.
  - `from` — string; required
  - `to` — string; required
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `polarity` — enum(+, -) | null; required
  - `strength` — enum(weak, moderate, strong, foundational); required
  - `evidence` — string | null; required
  - `session` — string | null; optional
  - `graph` — string | null; optional

- **`create-relations`** — Create multiple relations in a single invocation.
  - `relations` — array<object>; required
  - `relations[].from` — string; required
  - `relations[].to` — string; required
  - `relations[].relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `relations[].polarity` — enum(+, -) | null; required
  - `relations[].strength` — enum(weak, moderate, strong, foundational); required
  - `relations[].evidence` — string | null; required
  - `relations[].session` — string | null; optional
  - `relations[].graph` — string | null; optional
  - `continueOnError` — boolean | null; optional

- **`delete-relation`** — Retract a relation, preserving history (erase outright with "hard": true).
  - `from` — string; required
  - `to` — string; required
  - `relationId` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `hard` — boolean | null; optional
  - `graph` — string | null; optional

- **`get-neighbors`** — Get all entities connected to an entity.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `direction` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional
  - `follow_bridges` — boolean | null; optional
  - `compact` — boolean | null; optional

- **`get-relations`** — Get all relations connected to an entity.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `direction` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional
  - `follow_bridges` — boolean | null; optional
  - `compact` — boolean | null; optional

- **`list-relations`** — List relations with optional AND filters.
  - `from` — string | null; optional
  - `to` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `polarity` — enum(+, -) | null; optional
  - `session` — string | null; optional
  - `graph` — string | null; optional

- **`read-relation`** — Read a relation by source and target entity IDs.
  - `from` — string; required
  - `to` — string; required
  - `graph` — string | null; optional

- **`read-relations`** — Read all relations between source and target entity IDs.
  - `from` — string; required
  - `to` — string; required
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional

- **`update-relation`** — Update an existing relation.
  - `from` — string; required
  - `to` — string; required
  - `relationId` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `polarity` — enum(+, -) | null; optional
  - `strength` — enum(weak, moderate, strong, foundational) | null; optional
  - `evidence` — string | null; optional
  - `graph` — string | null; optional

## Search

- **`hybrid-search`** — Hybrid vector+keyword+graph search.
  - `query` — string; required
  - `limit` — integer | null; optional
  - `minScore` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `category` — string | array<string> | null; optional
  - `graph` — string | null; optional
  - `weights` — object | null; optional
  - `weights.vector` — number | null; optional
  - `weights.keyword` — number | null; optional
  - `weights.graph` — number | null; optional
  - `graphHops` — integer | null; optional
  - `qualityGrouping` — boolean | null; optional
  - `groupingStrategy` — string | null; optional
  - `mmrLambda` — number | null; optional
  - `recencyBoost` — boolean | null; optional
  - `recencyMaxBoost` — number | null; optional
  - `recencyHalfLifeDays` — number | null; optional
  - `memoryType` — string | array<string> | null; optional
  - `domain` — string | array<string> | null; optional
  - `durability` — string | array<string> | null; optional

- **`semantic-neighbors`** — Similar but unconnected entities.
  - `entityId` — string; required
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `graph` — string | null; optional

- **`semantic-search`** — Vector-only semantic search.
  - `query` — string; required
  - `limit` — integer | null; optional
  - `minScore` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `category` — string | array<string> | null; optional
  - `graph` — string | null; optional

## Semiring Composition

- **`semiring-bottleneck`** — Widest-path bottleneck.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`semiring-count-paths`** — Count acyclic paths.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `graph` — string | null; optional

- **`semiring-distances`** — Single-source semiring distances.
  - `source` — string; required
  - `semiring` — enum(boolean, tropical, tropical-uniform, viterbi, counting, capacity); required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional
  - `direction` — string | null; optional

- **`semiring-most-confident`** — Max-product confidence path.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`semiring-reachable`** — Boolean reachability with path.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`semiring-traverse`** — Traverse with a named semiring.
  - `source` — string; required
  - `target` — string; required
  - `semiring` — enum(boolean, tropical, tropical-uniform, viterbi, counting, capacity) | null; optional
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`transitive-closure`** — Boolean transitive closure pairs.
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `maxDepth` — integer | null; optional
  - `graph` — string | null; optional

## Subgraph

- **`concept-slippage`** — Find concept slippage candidates.
  - `conceptId` — string; required
  - `temperature` — number | null; optional
  - `limit` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `structuralWeight` — number | null; optional
  - `proximityWeight` — number | null; optional
  - `contextWeight` — number | null; optional
  - `graph` — string | null; optional

- **`cross-domain-mapping`** — Map concepts between domains by structure.
  - `sourceDomain` — object; required
  - `targetDomain` — object; required
  - `degreeWeight` — number | null; optional
  - `relationProfileWeight` — number | null; optional
  - `neighborProfileWeight` — number | null; optional
  - `entityTypeWeight` — number | null; optional
  - `pairMinSimilarity` — number | null; optional
  - `graph` — string | null; optional

- **`extract-subgraph`** — Extract a subgraph (causal/ego/typed).
  - `mode` — string; required
  - `entityId` — string | null; optional
  - `depth` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `output_mode` — string | null; optional
  - `graph` — string | null; optional

- **`find-subgraph-matches`** — Find approximate subgraph matches.
  - `pattern` — object; required
  - `nodeTypeWeight` — number | null; optional
  - `edgeTypeWeight` — number | null; optional
  - `topologyWeight` — number | null; optional
  - `minSimilarity` — number | null; optional
  - `maxResults` — integer | null; optional
  - `graph` — string | null; optional

## Symbolic Mathematics

- **`solve-problem`** — Solve a natural-language math problem via classify → translate → SymPy, with LLM fallback.
  - `question` — string; required

- **`symbolic-evaluate`** — Numerically evaluate an expression, optionally with substitutions.
  - `expression` — string; required
  - `substitutions` — object | null; optional

- **`symbolic-expand`** — Expand a product or power expression using SymPy.
  - `expression` — string; required

- **`symbolic-factor`** — Factor a polynomial expression using SymPy.
  - `expression` — string; required

- **`symbolic-latex`** — Convert a mathematical expression to LaTeX notation.
  - `expression` — string; required

- **`symbolic-simplify`** — Simplify a mathematical expression using SymPy.
  - `expression` — string; required

- **`symbolic-solve`** — Solve an equation or system for variables using SymPy.
  - `equation` — string | null; optional
  - `equations` — array<string> | null; optional
  - `variable` — string | null; optional
  - `variables` — array<string> | null; optional

- **`symbolic-verify`** — Verify whether a proposed solution satisfies an equation.
  - `equation` — string; required
  - `variable` — string; required
  - `value` — string | number; required

## Verification

- **`cegis-synthesize`** — Counterexample-guided synthesis of a graph satisfying property specs.
  - `properties` — array<object>; required
  - `properties[].name` — string; required
  - `properties[].type` — enum(forAllNodes, forAllEdges, invariant, default); required
  - `properties[].level` — enum(node, edge, subgraph, graph) | null; optional
  - `properties[].invariantName` — string | null; optional
  - `properties[].field` — string | null; optional
  - `properties[].condition` — string | null; optional
  - `properties[].value` — any; optional
  - `maxEntities` — integer; required
  - `maxRelations` — integer; required
  - `maxIterations` — integer; optional, default: 10
  - `timeoutMs` — integer; optional, default: 30000
  - `commit` — boolean; optional, default: false
  - `graph` — string | null; optional

- **`check-capabilities`** — Check capability invariants against the graph.
  - `types` — array<string> | null; optional
  - `couplingMetric` — enum(degree, betweenness) | null; optional
  - `couplingThreshold` — number | null; optional
  - `coverageParentType` — string | null; optional
  - `coverageChildType` — string | null; optional
  - `coverageRelationType` — string | null; optional
  - `patternMinOccurrences` — number | null; optional
  - `deriveFromGraph` — boolean | null; optional
  - `graph` — string | null; optional

- **`check-consistency`** — Run Tier 1 consistency checks on the graph.
  - `graph` — string | null; optional

- **`check-invariants`** — Check specific named invariants against the graph.
  - `invariants` — array<string> | null; optional
  - `graph` — string | null; optional

- **`constrained-generate`** — Generate graph structure satisfying type constraints.
  - `maxEntities` — integer; required
  - `maxRelations` — integer; required
  - `requiredTypes` — array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `commit` — boolean | null; optional
  - `graph` — string | null; optional
  - `seed` — integer | null; optional

- **`list-guard-violations`** — Run all guards over every entity and relation.
  - `guards` — array<string> | null; optional
  - `graph` — string | null; optional

- **`propagate-constraints`** — Run constraint propagation (AC-3) on type constraints.
  - `constraints` — array<object>; required
  - `constraints[].sourceType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session); required
  - `constraints[].relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes. Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `constraints[].targetType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session); required
  - `graph` — string | null; optional

- **`validate-mutation-trace`** — Replay a mutation trace and check invariants at each step.
  - `mutations` — array<object>; required
  - `mutations[].type` — enum(createEntity, updateEntity, deleteEntity, createRelation, deleteRelation); required
  - `mutations[].payload` — object; required
  - `invariants` — array<string> | null; optional
  - `graph` — string | null; optional

- **`validate-spec`** — Validate the graph against property specifications.
  - `properties` — array<object>; required
  - `properties[].name` — string; required
  - `properties[].type` — enum(forAllNodes, forAllEdges, invariant, default); required
  - `properties[].level` — enum(node, edge, subgraph, graph) | null; optional
  - `properties[].invariantName` — string | null; optional
  - `properties[].field` — string | null; optional
  - `properties[].condition` — string | null; optional
  - `properties[].value` — any; optional
  - `graph` — string | null; optional

- **`verify-graph`** — Verify the graph against guards and spec properties.
  - `includeDefaults` — boolean | null; optional
  - `graph` — string | null; optional

## Visualization

- **`export-bundle`** — Assemble the TapestryBundle JSON for a graph scope.
  - `graph` — string | null; optional
  - `scope` — object; optional
  - `scope.mode` — string; optional, default: "full"
  - `scope.center` — string | null; optional
  - `scope.depth` — integer; optional, default: 1
  - `scope.entityType` — string | null; optional
  - `scope.relationType` — string | null; optional
  - `scope.query` — string | null; optional
  - `include` — object; optional
  - `include.analytics` — boolean; optional, default: true
  - `include.temporal` — boolean; optional, default: true
  - `include.semantic` — boolean; optional, default: true
  - `title` — string | null; optional
  - `asOf` — string | null; optional
  - `maxEntities` — integer | null; optional

- **`export-graph`** — Write a compact, zero-infrastructure node-link JSON export of a graph.
  - `graph` — string | null; optional
  - `output` — string; required
  - `includeSuperseded` — boolean; optional, default: false
  - `entityTypes` — array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `force` — boolean; optional, default: false

- **`serve`** — Serve the interactive visualization live over a read-only REST API.
  - `graph` — string | null; optional
  - `host` — string; optional, default: "127.0.0.1"
  - `port` — integer; optional, default: 8000
  - `check` — boolean; optional, default: false

- **`visualize`** — Write a self-contained interactive HTML visualization of a graph scope.
  - `graph` — string | null; optional
  - `scope` — object; optional
  - `scope.mode` — string; optional, default: "full"
  - `scope.center` — string | null; optional
  - `scope.depth` — integer; optional, default: 1
  - `scope.entityType` — string | null; optional
  - `scope.relationType` — string | null; optional
  - `scope.query` — string | null; optional
  - `include` — object; optional
  - `include.analytics` — boolean; optional, default: true
  - `include.temporal` — boolean; optional, default: true
  - `include.semantic` — boolean; optional, default: true
  - `title` — string | null; optional
  - `asOf` — string | null; optional
  - `maxEntities` — integer | null; optional
  - `output` — string | null; optional
  - `theme` — string; optional, default: "auto"

## Work Memory

- **`record-outcome`** — Record how a piece of work turned out as usage evidence citing the entities it leaned on (supports when useful, questions when not).
  - `question` — string; required
  - `answer` — string | null; optional
  - `entityIds` — array<string>; required
  - `outcome` — enum(useful, dead_end, corrected); required — How a recorded piece of work actually turned out (the experiential layer). ``useful`` is a positive citation of what it cited; ``dead_end`` and ``corrected`` are negative — the difference is that a correction says the graph was wrong, a dead end says it led nowhere.
  - `correction` — string | null; optional
  - `graph` — string | null; optional
