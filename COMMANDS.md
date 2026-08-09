# Command Catalog

Generated from the registry (`theloom/cli/registry.py`) — never hand-edit.

**178 registry commands** across 28 categories, plus the special `init` command.

Each command lists its input fields below its summary: dotted paths (`confidence.score`) descend into nested objects, `[]` (`relations[].from`) marks an array of objects. `required`/`optional` is scoped to the field's immediate parent — a required field of an optional object only applies once that object is supplied at all. Run `loom <command> --schema` for the raw JSON Schema (with full `$defs`) behind any entry.

## Adaptive Routing

- **`adaptive-distances`** — Distances with an auto-routed plan.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `maxDepth` — integer | null; optional
  - `pathMode` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`adaptive-traverse`** — Traverse with an auto-routed plan.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `maxDepth` — integer | null; optional
  - `pathMode` — string | null; optional
  - `productMode` — boolean | null; optional
  - `graph` — string | null; optional

- **`cross-type-query`** — Cross-category query with morphisms.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `maxDepth` — integer | null; optional
  - `pathMode` — string | null; optional
  - `graph` — string | null; optional

- **`metapath-traverse`** — Typed step-sequence traversal.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `metapath` — string | object; required
  - `maxDepth` — integer | null; optional
  - `sourceEntityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `target` — string | null; optional
  - `graph` — string | null; optional

- **`type-analyze`** — Analyze a query into a routing plan.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string | null; optional
  - `target` — string | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `metapath` — string | object | null; optional
  - `graph` — string | null; optional

## Calibration

- **`calibration-profile`** — Fold every resolved claim into per-bucket count, mean asserted confidence, empirical hit rate, Brier score, and the asserted-vs-empirical gap, using each claim's assertion-time confidence.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `by` — enum(basis, domain, author) | null; optional — Which dimension to bucket resolved claims by. Omitted falls back to 'author' -- the dimension propagate-credit's calibrated damping and the assertion-time feedback check both key reliability by.
  - `window` — object | null; optional — Restricts the fold to claims RESOLVED (not created) in ``[since, until)`` -- open-ended on either side when omitted. This is what ``theloom.operations.calibration_alerts`` uses to ask "what resolved since I last looked".
  - `window.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `window.since` — string | null; optional
  - `window.until` — string | null; optional
  - `minBucketN` — integer | null; optional

- **`resolve-claim`** — Resolve a claim/hypothesis: create the outcome entity, link it with a 'resolves' edge, and transition its status -- one atomic write.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `claimId` — string; required
  - `resolution` — enum(confirmed, refuted, expired); required — How a resolved claim/hypothesis actually turned out (desire 14, the closed calibration loop) -- distinct from ``UsageOutcome``, which grades whether a piece of *work* was useful, not whether a *belief* was true. ``confirmed``/``refuted`` are the judged, binary-scorable outcomes a Brier score is computed over; ``expired`` means the claim became moot before anyone could tell -- excluded from Brier/hit-rate (see ``theloom.operations.calibration``'s module docstring) rather than scored as either.
  - `evidence` — string; required
  - `session` — string | null; optional
  - `graph` — string | null; optional

## Composites

- **`analogy-transfer`** — Generate novel entities via CWSG analogy transfer from cross-domain mappings (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `sourceDomain` — object; required
  - `targetDomain` — object; required
  - `temperature` — number | null; optional
  - `graph` — string | null; optional

- **`belief-blast-radius`** — What would change if I stopped believing this? Fork, propagate-credit inside the fork with the hypothetical delta, diff-worlds, abandon -- read-only from main's perspective (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityIds` — array<string>; required — The evidence/claim entities to hypothetically revise -- same addressing as propagate-credit.
  - `delta` — number; required — The hypothetical confidence delta to propagate.
  - `graph` — string | null; optional
  - `dampingFactor` — number | null; optional
  - `maxDepth` — integer | null; optional
  - `minDelta` — number | null; optional
  - `relationTypes` — array<string> | null; optional
  - `propagationMode` — string | null; optional

- **`creativity-loop`** — Run the autonomous creativity loop: explore, retrieve, transfer, score, accept/reject, learn (composite). Read-only and deterministic — no LLM; it stops early on consecutive empty cycles or a plateau. The analogy trigger queue is reported per cycle, never drained.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `maxNodes` — integer | null; optional
  - `maxCandidates` — integer | null; optional
  - `numSamples` — integer | null; optional
  - `minConfidence` — number | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`entity-deep-dive`** — Comprehensive analysis of a single entity (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `graph` — string | null; optional
  - `full` — boolean | null; optional

- **`explore-frontier`** — Rank frontier regions by foraging signals with MVT advice and anti-pattern guards (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `topK` — integer | null; optional
  - `includeMvt` — boolean | null; optional
  - `includeAntiPatterns` — boolean | null; optional
  - `purpose` — string | null; optional

- **`far-analogy-retrieval`** — Run the full far-analogy retrieval pipeline: fingerprint, match, slip, transfer, score (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `seedEntity` — string | null; optional
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `autoCreate` — boolean | null; optional
  - `commitThreshold` — number | null; optional
  - `graph` — string | null; optional

- **`graph-reconnaissance`** — Comprehensive structural overview of a graph (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `centralityLimit` — integer | null; optional

- **`hypothesis-engine`** — Generate and rank hypotheses from semantic gaps (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string; required
  - `maxDepth` — integer | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`multi-graph-landscape`** — Ecosystem-level overview of all graphs (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`propose-entities`** — Propose new entities that should exist in the knowledge graph (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional, default: 10
  - `simulate` — boolean | null; optional, default: false
  - `strategies` — array<enum(pattern_completion, llm_reasoning)> | null; optional
  - `graph` — string | null; optional
  - `minPatternOccurrences` — integer | null; optional, default: 2
  - `maxPatterns` — integer | null; optional, default: 20

- **`provenance-audit`** — Full provenance audit for an entity (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string; required
  - `maxDepth` — integer | null; optional
  - `delta` — number | null; optional
  - `graph` — string | null; optional

- **`reflect`** — Distil recorded outcomes into standing lessons: time-decayed usage scores, preferred/contested/dead-end statuses, and staleness against changed files (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `halfLifeDays` — number | null; optional
  - `minCorroboration` — integer | null; optional
  - `projectPath` — string | null; optional
  - `asOf` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`self-improve`** — Autonomous self-improvement cycle: reconnaissance, capability check, propose, simulate, rank, apply (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `autoApply` — boolean | null; optional, default: false
  - `maxProposals` — integer | null; optional
  - `applyTopN` — integer | null; optional

- **`semantic-landscape`** — Semantic analysis overview of a graph (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `seedEntity` — string | null; optional
  - `category` — string | null; optional
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `graph` — string | null; optional

- **`simulate-change`** — Simulate graph mutations and preview structural impact (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `mutations` — array<object>; required
  - `mutations[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `mutations[].type` — enum(createEntity, updateEntity, deleteEntity, createRelation, deleteRelation); required
  - `mutations[].payload` — object; required
  - `graph` — string | null; optional

- **`structural-survey`** — Structural analysis around an entity: ego subgraph, cycles, paths (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string; required
  - `depth` — integer | null; optional
  - `target` — string | null; optional
  - `maxDepth` — integer | null; optional
  - `metapathPatterns` — array<string> | null; optional
  - `graph` — string | null; optional

- **`verified-extract`** — Extract from documents then verify graph integrity (composite).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `depth` — integer | null; optional
  - `limit` — integer | null; optional
  - `hubPercentile` — number | null; optional
  - `graph` — string | null; optional

- **`explore`** — Everything about one symbol in one call: definition, callers, callees, imports, containment, inheritance and the semantic layer, within a token budget.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `budget` — integer | null; optional
  - `graph` — string | null; optional

- **`find-callees`** — Ranked list of the symbols this one calls, each anchored at its call site.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`find-callers`** — Ranked list of the symbols that call this one, each anchored at its call site.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

## Contract

- **`notices-catalog`** — Enumerate every notice code, its meaning, and the commands that can emit it -- generated from source, never hand-maintained.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.

## Documents

- **`analyze-category`** — Discover prevalent semantic themes in a document category.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `category` — string; required
  - `topK` — integer | null; optional
  - `similarityThreshold` — number | null; optional
  - `minClusterSize` — integer | null; optional
  - `maxChunks` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`delete-document`** — Delete a document and all its chunks from the vector store.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source_id` — string; required
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-content`** — Ingest string content directly.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `dir_path` — string; required
  - `pattern` — string | null; optional
  - `category` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-document`** — Ingest a document file into the vector store.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `file_path` — string; required
  - `category` — string | null; optional
  - `title` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`ingest-url`** — Fetch and ingest web content from a URL.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `url` — string; required
  - `category` — string | null; optional
  - `title` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`list-documents`** — List all ingested documents with chunk counts.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `category` — string | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

- **`reingest-document`** — Re-ingest a document, comparing content and updating changed chunks.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source_id` — string; required
  - `file_path` — string | null; optional
  - `chunk_strategy` — string | null; optional
  - `target_chunk_size` — integer | null; optional
  - `overlap` — integer | null; optional
  - `graph` — string | null; optional — Ignored: documents are global, not graph-scoped, so this has no effect on where the document is stored or which chunks are returned. Graph scoping happens later, when entities are extracted from the document via extract-from-documents. Supplying this returns a PARAMETER_IGNORED notice in the response.

## Embeddings

- **`embed-entities`** — Embed all entities in a graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityType` — string | null; optional
  - `forceReembed` — boolean | null; optional
  - `graph` — string | null; optional

- **`embed-entity`** — Embed a single entity by ID.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `id` — string; required
  - `graph` — string | null; optional

- **`embedder-profile`** — The configured embedder's live-measured similarity landscape.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.

- **`embedding-reconcile`** — Reconcile entity status vs vector store.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `dryRun` — boolean | null; optional
  - `cleanOrphans` — boolean | null; optional
  - `graph` — string | null; optional

- **`embedding-status`** — Embedding status counts for a graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`find-clusters`** — Discover semantic clusters.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `similarityThreshold` — number | null; optional
  - `minClusterSize` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | null; optional

- **`flush-pending-embeddings`** — Flush the pending embedding queue.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`list-dead-letters`** — List dead-letter queue entries.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`resolve-gaps`** — Create relations for semantic gaps.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `threshold` — number | null; optional
  - `maxResolutions` — integer | null; optional
  - `relationTypeHint` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`retry-failed-embeddings`** — Retry dead-lettered embeddings.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`semantic-gaps`** — Similar but unconnected entity pairs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `maxEntities` — integer | null; optional
  - `seed` — integer | null; optional
  - `graph` — string | null; optional

- **`suggest-relations`** — Suggest relations from patterns.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string; required
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `targetEntityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `graph` — string | null; optional

- **`warm-embedder`** — Pre-download and warm the embedding model.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.

## Entity Management

- **`bulk-import`** — Bulk import entities and relations into the knowledge graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entities` — array<object> | null; optional
  - `relations` — array<object> | null; optional
  - `jsonlInput` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`create-entity`** — Create a new entity in the knowledge graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `name` — string; required
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session); required
  - `observations` — array<string>; required
  - `confidence` — object | null; optional
  - `confidence.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `confidence.score` — number; required
  - `confidence.basis` — enum(direct_observation, peer_reviewed, multiple_sources, single_source, inference, speculation, llm_extraction, calculated); required
  - `confidence.lastEvaluated` — string | null; optional
  - `provenance` — object | null; optional
  - `provenance.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `provenance.sourceType` — string; required
  - `provenance.sourceId` — string | null; required
  - `provenance.externalRef` — string | null; required
  - `provenance.extractionDate` — string | null; optional
  - `provenance.extractor` — string; required
  - `provenance.extractionMethod` — string | null; required
  - `session` — string | null; optional — The authoring identity attributed to this entity. When omitted, the server attributes a configured fallback identity (theloom/config.py's defaultSession) so every entity carries authorship -- never left absent.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `id` — string; required
  - `hard` — boolean | null; optional
  - `graph` — string | null; optional

- **`list-entities`** — List entities with optional filtering.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `primary` — string; required
  - `secondary` — string; required
  - `graph` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`read-entities-by-name`** — Resolve a batch of entity names to UUIDs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `names` — array<string>; required
  - `graph` — string | null; optional

- **`read-entity`** — Read an entity by its ID.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `id` — string | null; optional
  - `name` — string | null; optional
  - `graph` — string | null; optional
  - `compact` — boolean | null; optional

- **`update-entity`** — Update an existing entity.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `id` — string; required
  - `name` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `observations` — array<string> | null; optional
  - `confidence` — object | null; optional
  - `confidence.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `confidence.score` — number; required
  - `confidence.basis` — enum(direct_observation, peer_reviewed, multiple_sources, single_source, inference, speculation, llm_extraction, calculated); required
  - `confidence.lastEvaluated` — string | null; optional
  - `status` — enum(active, superseded, deprecated, retracted, investigating) | null; optional
  - `statusReason` — string | null; optional
  - `provenance` — object | null; optional
  - `provenance.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `since` — string | null; optional

- **`blocking-questions`** — Find questions blocking other work.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `domain` — string | null; optional

- **`claims-from-source`** — Find entities sourced from a source.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `sourceId` — string; required
  - `limit` — integer | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`contested-claims`** — Find claims with conflicting evidence.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`cross-session-contradictions`** — Contradictions across sessions.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `minConfidence` — number | null; optional
  - `sessionIds` — array<string> | null; optional
  - `maxDepth` — integer | null; optional

- **`inferred-claims`** — Find inference-based entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`most-certain`** — Find the highest-confidence entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `topK` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`needs-evidence`** — Find claims lacking supporting evidence.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `minSupports` — integer | null; optional
  - `claimId` — string | null; optional

- **`open-questions`** — Find active unanswered questions.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`postmortem-evaluate`** — Evaluate postmortem output utility.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`process-triggers`** — Dequeue pending analogy trigger candidates.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `limit` — integer | null; optional

- **`propagate-credit`** — Propagate confidence through epistemic chains.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityIds` — array<string>; required
  - `delta` — number; required
  - `dampingFactor` — number | string | null; optional — A plain number (0-1 inclusive) applies that constant at every hop, exactly as before. Pass 'calibrated' (desire 14) to resolve damping per hop from the SOURCE entity's author's own measured reliability (1 - their Brier score over resolved claims they've asserted -- see calibration-profile/resolve-claim) instead of a constant, so credit from a well-calibrated author propagates further than credit from a poorly-calibrated one. An author with too little resolved history falls back to the ordinary constant and the response carries an INSUFFICIENT_DATA notice naming them. Each change in the response carries the exact `dampingApplied` value used for its hop.
  - `maxDepth` — integer | null; optional
  - `minDelta` — number | null; optional
  - `dryRun` — boolean | null; optional — Preview the propagation without persisting anything. Defaults to false: a call with no dryRun (or dryRun: false) computes AND PERSISTS the propagated confidence changes immediately — this is a mutating command by default, consistent with the other mutating epistemic commands (postmortem-evaluate, session-changelog). Pass dryRun: true to compute the would-be newConfidence values without writing them. Either way the response carries an `applied` marker (true iff a write actually happened) and, on a simulated run, a DRY_RUN notice.
  - `relationTypes` — array<string> | null; optional
  - `propagationMode` — string | null; optional
  - `graph` — string | null; optional

- **`provenance-chain`** — Trace the source chain from an entity.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string; required
  - `maxDepth` — integer | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`session-changelog`** — What changed since a timestamp.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `since` — string | null; optional
  - `session` — string | null; optional
  - `graph` — string | null; optional
  - `includeRelations` — boolean | null; optional
  - `dryRun` — boolean | null; optional

- **`single-source-claims`** — Find claims depending on one source.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional

- **`stale-beliefs`** — Find entities not recently evaluated.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `daysOld` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`trigger-status`** — Status of the analogy trigger queue.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`uncertain-claims`** — Find entities with low confidence scores.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `threshold` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

- **`unprovenanced`** — Find entities without provenance.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `limit` — integer | null; optional
  - `includeAllStatuses` — boolean | null; optional
  - `graph` — string | null; optional
  - `session` — string | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional

## Event Log

- **`what-changed`** — Replay a span of the event log as a compact diff: entity/relation, field, old, new, and the command that caused it.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `eventIds` — array<string> | null; optional — Replay exactly these event ids (e.g. a prior mutating response's eventIds), in the order given. Mutually exclusive with fromEventId/toEventId — when set, those are ignored.
  - `fromEventId` — string | null; optional — Inclusive lower bound of the stream span to replay. Omit for the start of the log.
  - `toEventId` — string | null; optional — Inclusive upper bound of the stream span to replay. Omit for the end of the log.
  - `limit` — integer | null; optional — Caps how many raw events are read before diffing (default 500). Ignored when eventIds is given — an explicit id list is never truncated.

## Extraction

- **`extract-codebase`** — Extract a codebase into a Loom knowledge graph via tree-sitter.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `projectPath` — string; required
  - `graph` — string | null; optional
  - `includeTests` — boolean | null; optional
  - `include` — array<string> | null; optional — Only collect files whose project-relative path matches one of these fnmatch globs (e.g. "src/*", "**/*.py"); unset or empty means no restriction.
  - `exclude` — array<string> | null; optional — Never collect files whose project-relative path matches one of these fnmatch globs; takes priority over `include` when a path matches both.
  - `dryRun` — boolean | null; optional

- **`extract-from-documents`** — Extract entities and relations from ingested documents using the LLM.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `category` — string | null; optional
  - `documentId` — string | null; optional
  - `query` — string | null; optional
  - `maxChunks` — integer | null; optional
  - `model` — string | null; optional
  - `focus` — string | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

- **`extract-preview`** — Preview extraction results for the first chunks (dry run).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `category` — string | null; optional
  - `documentId` — string | null; optional
  - `query` — string | null; optional
  - `maxChunks` — integer | null; optional
  - `model` — string | null; optional
  - `focus` — string | null; optional
  - `graph` — string | null; optional

- **`extraction-rollback`** — Roll back an extraction run by deleting its created entities and relations.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `runId` — string; required
  - `graph` — string | null; optional

- **`extraction-status`** — Show the status and progress of extraction runs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `runId` — string | null; optional

- **`self-model-update`** — Update The Loom's self-referential codebase graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `projectPath` — string | null; optional
  - `graphName` — string | null; optional
  - `dryRun` — boolean | null; optional

- **`update-codebase`** — Incrementally update an existing codebase graph from a git diff.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `algorithm` — string | null; optional
  - `metric` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`detect-components`** — Detect connected components.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `strong` — boolean | null; optional
  - `graph` — string | null; optional

- **`find-frequent-subgraphs`** — Find frequent subgraph motifs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`reify-patterns`** — Reify recurring structural motifs as pattern entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `minOccurrences` — integer | null; optional
  - `maxDepth` — integer | null; optional
  - `maxPatterns` — integer | null; optional
  - `dryRun` — boolean | null; optional
  - `graph` — string | null; optional

## Graph Synthesis

- **`decompose-query`** — Decompose a complex query into ordered sub-questions based on graph structure.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `graph` — string | array<string> | null; optional

- **`explain-leverage-point`** — Generate a natural language explanation of a leverage point — why it matters, what it affects, and its Meadows level context.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `leveragePointId` — string; required
  - `graph` — string | null; optional

- **`explain-loop`** — Generate a natural language explanation of a feedback loop's dynamics — what reinforces or balances, entry points, and likely behavior.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `loopId` — string; required
  - `graph` — string | null; optional

- **`explain-path`** — Generate a step-by-step natural language explanation of a path between two entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `sourceId` — string | null; optional
  - `targetId` — string | null; optional
  - `sourceName` — string | null; optional
  - `targetName` — string | null; optional
  - `path` — array<string> | null; optional
  - `graph` — string | array<string> | null; optional

- **`plan-synthesis`** — Plan a synthesis without executing.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `graph` — string | array<string> | null; optional

- **`synthesize`** — Synthesize a coherent text output from the knowledge graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `format` — enum(narrative, outline, evidence_map, causal_chain, raw, proposal) | null; optional
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `mode` — enum(systematic, adaptive) | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | array<string> | null; optional

- **`synthesize-and-ingest`** — Synthesize text from the knowledge graph and ingest the output as new entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `format` — enum(narrative, outline, evidence_map, causal_chain, raw, proposal) | null; optional
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `mode` — enum(systematic, adaptive) | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | array<string> | null; optional

- **`traverse-synthesis`** — Plan and traverse a synthesis subgraph, returning evidence units and provenance.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `mode` — enum(systematic, adaptive) | null; optional
  - `focus` — enum(narrow, balanced, broad) | null; optional
  - `orderingMetric` — enum(core-number, degree, pagerank, betweenness) | null; optional
  - `maxDepth` — integer | null; optional
  - `maxEntities` — integer | null; optional
  - `graph` — string | array<string> | null; optional

- **`verify-fidelity`** — Check structural fidelity of text against the knowledge graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `text` — string; required
  - `entityIds` — array<string> | null; optional — Which entities to check `text` against. Omitting this (or passing an empty list) does NOT grade against the whole graph — a real-sized graph makes that score meaningless (mostly-zero entity/relation coverage). Instead the command auto-scopes: it runs its own retrieval (hybrid vector search on `text`, falling back to keyword matching when entities lack embeddings) to select up to 10 relevant entities, grades against those, and reports the selection as an AUTO_SCOPED entry in the response's `notices`. If nothing in the graph matches `text` well enough to select, the command refuses (INPUT_REQUIRED) rather than silently scoring nothing. For predictable, reviewable scoping, run hybrid-search on `text` yourself first and pass the entity ids you judge relevant here.
  - `mode` — enum(structural, narrative) | null; optional
  - `graph` — string | array<string> | null; optional

## Inference

- **`explain-inference`** — Explain a derived fact by walking its inference trace.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `relationId` — string; required — The id of a derived relation to explain (from run-inference's `derivedRelations`, or an inference_trace's steps via inference-trace-get). Only relations created by run-inference have a trace to walk; a manually-created relation fails with NOT_FOUND.
  - `graph` — string | null; optional

- **`inference-rule-create`** — Create a declarative inference rule.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `rule` — object; required — The rule specification: conditions to match and the relation to derive when they do. See the nested field descriptions (rule.conditions[].from, etc.) for the '?var' rule-variable syntax.
  - `rule.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `rule.name` — string; required — A human-readable name for the rule, used in derived-relation and trace output (e.g. `ruleName`) — not itself matched against anything.
  - `rule.description` — string; required — A human-readable description of what the rule captures; stored and returned as-is, not used for matching.
  - `rule.conditions` — array<object>; required — The AND-conjunction of relation patterns that must all match simultaneously, with consistent variable bindings across them, for the rule to fire. See each condition's `from`/`to` field descriptions for the rule-variable ('?var') syntax and a worked multi-hop example. Example: conditions [{"from": "?a", "relationType": "enables", "to": "?b"}, {"from": "?b", "relationType": "enables", "to": "?c"}] with conclusion {"from": "?a", "relationType": "enables", "to": "?c", ...} derives a new "enables" relation whenever two chained "enables" relations share a middle entity.
  - `rule.conditions[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `rule.conditions[].from` — string; required — The pattern this condition matches against each existing relation's `from` (source) endpoint. Either a rule variable — a string starting with '?' (e.g. '?a') — bound at match time to whatever entity id satisfies the pattern, or a literal entity id, matched only against relations whose endpoint is exactly that id. IMPORTANT: a bare string that is not a real entity id (a display name, or a variable typo missing the '?', e.g. 'a' instead of '?a') validates with no error and creates the rule, but the rule can then never match anything — a silently inert rule, not a rejected one (TL-495 tracks warning about this case; not enforced here). The same variable name must bind to the same entity everywhere it appears across a rule's conditions, and any variable used in the conclusion must appear in at least one condition (checked at rule-creation time). Example: conditions [{"from": "?a", "relationType": "enables", "to": "?b"}, {"from": "?b", "relationType": "enables", "to": "?c"}] with conclusion {"from": "?a", "relationType": "enables", "to": "?c", ...} derives a new "enables" relation whenever two chained "enables" relations share a middle entity.
  - `rule.conditions[].to` — string; required — The pattern this condition matches against each existing relation's `to` (target) endpoint — same '?var' (rule variable, bound at match time) vs. literal-entity-id semantics as `from`: see that field's description for the full syntax, the inert-rule pitfall of a bare non-id string, and a worked example. Example: conditions [{"from": "?a", "relationType": "enables", "to": "?b"}, {"from": "?b", "relationType": "enables", "to": "?c"}] with conclusion {"from": "?a", "relationType": "enables", "to": "?c", ...} derives a new "enables" relation whenever two chained "enables" relations share a middle entity.
  - `rule.conditions[].relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — The relation type this condition must match among the graph's existing relations (ANDed together with every other condition in the rule).
  - `rule.conclusion` — object; required — The relation derived when every condition matches, with each `?var` replaced by its bound entity id. See conclusion.from's description for the variable-binding rules.
  - `rule.conclusion.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `rule.conclusion.from` — string; required — The `from` (source) endpoint of the relation to derive when every condition matches. Either a rule variable already bound by a condition (its bound entity id is substituted in) or a literal entity id used as-is. Every variable referenced here must appear in at least one condition — an unbound variable is rejected at rule-creation time. See RuleCondition's `from` field for the full '?var' syntax. Example: conditions [{"from": "?a", "relationType": "enables", "to": "?b"}, {"from": "?b", "relationType": "enables", "to": "?c"}] with conclusion {"from": "?a", "relationType": "enables", "to": "?c", ...} derives a new "enables" relation whenever two chained "enables" relations share a middle entity.
  - `rule.conclusion.to` — string; required — The `to` (target) endpoint of the relation to derive when every condition matches — same bound-variable-or-literal-id rule as `conclusion.from`; see RuleCondition's `from` field for the full '?var' syntax and a worked example.
  - `rule.conclusion.relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — The relation type to create for the derived relation.
  - `rule.conclusion.strength` — string; required
  - `rule.conclusion.evidence` — string; required
  - `rule.conclusion.polarity` — string | null; optional
  - `rule.enabled` — boolean | null; optional — Whether run-inference evaluates this rule at all; a rule created without `enabled: true` is stored but never fires.
  - `graph` — string | null; optional

- **`inference-rule-delete`** — Delete an inference rule by its entity id.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `ruleId` — string; required
  - `graph` — string | null; optional

- **`inference-rule-list`** — List all inference rules stored in the graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`inference-trace-for-fact`** — Find the inference trace that produced a relation.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `relationId` — string; required
  - `graph` — string | null; optional

- **`inference-trace-get`** — Get full details of a specific inference trace.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `traceId` — string; required
  - `graph` — string | null; optional

- **`inference-trace-list`** — List inference traces.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `ruleId` — string | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional

- **`run-inference`** — Run the inference engine: evaluate enabled rules.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `dryRun` — boolean | null; optional — Preview a run without persisting anything. Defaults to false: a call with no dryRun (or dryRun: false) matches rules AND PERSISTS the derived relations plus an inference_trace entity recording the run. Pass dryRun: true to preview the derived relations without writing an inference_trace entity or any derived relations — the would-be trace payload is still returned, unpersisted, as `tracePreview` (traceId stays null since nothing was written). Either way the response carries an `applied` marker (true only on a real, persisted run) and, on a simulated run, a DRY_RUN notice.
  - `ruleId` — string | null; optional — Restrict this run to one rule, by its inference_rule entity id (see inference-rule-create's `id` response field, or inference-rule-list). Omitted: evaluate every enabled rule.
  - `graph` — string | null; optional

## Leverage Points

- **`leverage-point-details`** — Get details about a leverage point.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `leveragePointId` — string; required
  - `includeTargets` — boolean | null; optional
  - `graph` — string | null; optional

- **`list-leverage-points`** — List leverage point entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `level` — integer | null; optional
  - `minLevel` — integer | null; optional
  - `maxLevel` — integer | null; optional
  - `depthCategory` — string | null; optional
  - `targetEntity` — string | null; optional
  - `graph` — string | null; optional

## Loop Analysis

- **`detect-cycles`** — Detect cycles in the knowledge graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `includePaths` — boolean | null; optional
  - `causalOnly` — boolean | null; optional

- **`detect-loops`** — Detect and classify feedback loops.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `persist` — boolean | null; optional — Persist detected loops as loop entities (plus part_of member relations) so list-loops and loop-details can find them afterward. Defaults to false: a detect-loops call with no persist key still returns full loop data (each loop has id: null and persisted: false) but writes nothing to the graph -- a following list-loops call will NOT see these loops until you re-run detect-loops with "persist": true. When results are not persisted, the response carries applied: false and a NOT_PERSISTED notice naming this flag as the fix.
  - `minSize` — integer | null; optional
  - `maxSize` — integer | null; optional

- **`list-loops`** — List loop entities with metadata.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `classification` — string | null; optional
  - `throughEntity` — string | null; optional
  - `minSize` — integer | null; optional
  - `maxSize` — integer | null; optional
  - `graph` — string | null; optional

- **`loop-details`** — Get details about a loop entity.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `loopId` — string; required
  - `includeMembers` — boolean | null; optional
  - `graph` — string | null; optional

## Multi-Graph

- **`create-graph`** — Create a new empty graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `name` — string; required

- **`delete-graph`** — Delete an existing graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `name` — string; required

- **`find-related-graphs`** — Find graphs connected to a graph via bridge relations.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string; required

- **`graph-connections`** — Get bridge counts between all connected graph pairs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.

- **`list-bridges`** — List cross-graph bridge relations.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from_graph` — string | null; optional
  - `to_graph` — string | null; optional
  - `entity_id` — string | null; optional

- **`list-graphs`** — List all available graphs with their loaded status and stats.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.

## Path Finding

- **`find-all-paths`** — Find all simple paths between entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `maxPaths` — integer | null; optional
  - `timeout` — integer | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional

- **`find-shortest-path`** — Find the shortest path between entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string | null; optional
  - `target` — string | null; optional
  - `sourceName` — string | null; optional
  - `targetName` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional

## Relation Management

- **`create-relation`** — Create a relation between two entities.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string; required
  - `to` — string; required
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `polarity` — enum(+, -) | null; required
  - `strength` — enum(weak, moderate, strong, foundational); required
  - `evidence` — string | null; required
  - `session` — string | null; optional
  - `graph` — string | null; optional

- **`create-relations`** — Create multiple relations in a single invocation.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `relations` — array<object>; required
  - `relations[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `relations[].from` — string; required
  - `relations[].to` — string; required
  - `relations[].relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `relations[].polarity` — enum(+, -) | null; required
  - `relations[].strength` — enum(weak, moderate, strong, foundational); required
  - `relations[].evidence` — string | null; required
  - `relations[].session` — string | null; optional
  - `relations[].graph` — string | null; optional
  - `continueOnError` — boolean | null; optional
  - `graph` — string | null; optional — Default graph for any item that omits its own `graph` — an item's own `graph` always wins. Without this, a top-level `graph` on create-relations was silently ignored (extra fields are dropped) and the batch fell through to each item's own graph, usually the default graph.

- **`delete-relation`** — Retract a relation, preserving history (erase outright with "hard": true).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string; required
  - `to` — string; required
  - `relationId` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `hard` — boolean | null; optional
  - `graph` — string | null; optional

- **`get-neighbors`** — Get all entities connected to an entity.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `direction` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional
  - `follow_bridges` — boolean | null; optional
  - `compact` — boolean | null; optional

- **`get-relations`** — Get all relations connected to an entity.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string | null; optional
  - `name` — string | null; optional
  - `direction` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional
  - `follow_bridges` — boolean | null; optional
  - `compact` — boolean | null; optional

- **`list-relations`** — List relations with optional AND filters.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string | null; optional
  - `to` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `polarity` — enum(+, -) | null; optional
  - `session` — string | null; optional
  - `graph` — string | null; optional

- **`read-relation`** — Read a relation by source and target entity IDs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string; required
  - `to` — string; required
  - `graph` — string | null; optional

- **`read-relations`** — Read all relations between source and target entity IDs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string; required
  - `to` — string; required
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `graph` — string | null; optional

- **`update-relation`** — Update an existing relation.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string; required
  - `to` — string; required
  - `relationId` — string | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `polarity` — enum(+, -) | null; optional
  - `strength` — enum(weak, moderate, strong, foundational) | null; optional
  - `evidence` — string | null; optional
  - `graph` — string | null; optional

## Search

- **`hybrid-search`** — Hybrid vector+keyword+graph search.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `limit` — integer | null; optional
  - `minScore` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `category` — string | array<string> | null; optional
  - `graph` — string | null; optional
  - `weights` — object | null; optional
  - `weights.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `entityId` — string; required
  - `limit` — integer | null; optional
  - `minSimilarity` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `graph` — string | null; optional

- **`semantic-search`** — Vector-only semantic search.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `query` — string; required
  - `limit` — integer | null; optional
  - `minScore` — number | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `category` — string | array<string> | null; optional
  - `graph` — string | null; optional

## Semiring Composition

- **`semiring-bottleneck`** — Widest-path bottleneck.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`semiring-count-paths`** — Count acyclic paths.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `graph` — string | null; optional

- **`semiring-distances`** — Single-source semiring distances.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `semiring` — enum(boolean, tropical, tropical-uniform, viterbi, counting, capacity); required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `limit` — integer | null; optional
  - `graph` — string | null; optional
  - `direction` — enum(out, in, both) | null; optional — Which edges to traverse from `source`. 'out' follows outgoing edges (source is the cause; the default when omitted). 'in' follows incoming edges (source is the effect — walked backward, so results are predecessors, not successors). 'both' unions outgoing and incoming. If the traversal touches zero edges from `source` in the searched direction, the response carries an EMPTY_TRAVERSAL notice with the real edge counts in each direction and, when the other direction has edges, a hint to retry with it — an empty `distances` list alone never distinguishes 'no causal reach' from 'wrong direction'.

- **`semiring-most-confident`** — Max-product confidence path.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`semiring-reachable`** — Boolean reachability with path.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string; required
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`semiring-traverse`** — Traverse with a named semiring.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `source` — string; required
  - `target` — string; required
  - `semiring` — enum(boolean, tropical, tropical-uniform, viterbi, counting, capacity) | null; optional
  - `maxDepth` — integer | null; optional
  - `relationTypes` — array<enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from)> | null; optional
  - `mode` — enum(WALK, TRAIL, ACYCLIC, SIMPLE) | null; optional
  - `graph` — string | null; optional

- **`transitive-closure`** — Boolean transitive closure pairs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `maxDepth` — integer | null; optional
  - `graph` — string | null; optional

## Subgraph

- **`concept-slippage`** — Find concept slippage candidates.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `conceptId` — string; required
  - `temperature` — number | null; optional
  - `limit` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `structuralWeight` — number | null; optional
  - `proximityWeight` — number | null; optional
  - `contextWeight` — number | null; optional
  - `graph` — string | null; optional

- **`cross-domain-mapping`** — Map concepts between domains by structure.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `sourceDomain` — object; required
  - `targetDomain` — object; required
  - `degreeWeight` — number | null; optional
  - `relationProfileWeight` — number | null; optional
  - `neighborProfileWeight` — number | null; optional
  - `entityTypeWeight` — number | null; optional
  - `pairMinSimilarity` — number | null; optional
  - `graph` — string | null; optional

- **`extract-subgraph`** — Extract a subgraph (causal/ego/typed).
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `mode` — string; required
  - `entityId` — string | null; optional
  - `depth` — integer | null; optional
  - `entityType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session) | null; optional
  - `relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from) | null; optional — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `output_mode` — string | null; optional
  - `graph` — string | null; optional

- **`find-subgraph-matches`** — Find approximate subgraph matches.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `pattern` — object; required
  - `nodeTypeWeight` — number | null; optional
  - `edgeTypeWeight` — number | null; optional
  - `topologyWeight` — number | null; optional
  - `minSimilarity` — number | null; optional
  - `maxResults` — integer | null; optional
  - `graph` — string | null; optional

## Symbolic Mathematics

- **`solve-problem`** — Solve a natural-language math problem via classify → translate → SymPy, with LLM fallback.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `question` — string; required

- **`symbolic-evaluate`** — Numerically evaluate an expression, optionally with substitutions.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `expression` — string; required
  - `substitutions` — object | null; optional

- **`symbolic-expand`** — Expand a product or power expression using SymPy.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `expression` — string; required

- **`symbolic-factor`** — Factor a polynomial expression using SymPy.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `expression` — string; required

- **`symbolic-latex`** — Convert a mathematical expression to LaTeX notation.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `expression` — string; required

- **`symbolic-simplify`** — Simplify a mathematical expression using SymPy.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `expression` — string; required

- **`symbolic-solve`** — Solve an equation or system for variables using SymPy.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `equation` — string | null; optional
  - `equations` — array<string> | null; optional
  - `variable` — string | null; optional
  - `variables` — array<string> | null; optional

- **`symbolic-verify`** — Verify whether a proposed solution satisfies an equation.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `equation` — string; required
  - `variable` — string; required
  - `value` — string | number; required

## Verification

- **`cegis-synthesize`** — Counterexample-guided synthesis of a graph satisfying property specs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `properties` — array<object>; required
  - `properties[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional

- **`check-invariants`** — Check specific named invariants against the graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `invariants` — array<string> | null; optional
  - `graph` — string | null; optional

- **`constrained-generate`** — Generate graph structure satisfying type constraints.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `maxEntities` — integer; required
  - `maxRelations` — integer; required
  - `requiredTypes` — array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `commit` — boolean | null; optional
  - `graph` — string | null; optional
  - `seed` — integer | null; optional

- **`list-guard-violations`** — Run all guards over every entity and relation.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `guards` — array<string> | null; optional
  - `graph` — string | null; optional

- **`propagate-constraints`** — Run constraint propagation (AC-3) on type constraints.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `constraints` — array<object>; required
  - `constraints[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `constraints[].sourceType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session); required
  - `constraints[].relationType` — enum(related_to, instance_of, part_of, sources, calls, references, supports, contradicts, questions, supersedes, resolves, causes, enables, requires, inhibits, amplifies, dampens, crystallized_from); required — Structural (no polarity): related_to, instance_of, part_of, sources, calls, references (the last two are code structure: invocation and non-invoking mention). Epistemic (no polarity): supports, contradicts, questions, supersedes, resolves (the calibration outcome link — an outcome entity resolves a claim/hypothesis; see ``theloom.operations.calibration``). Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens. Plus crystallized_from (reification lineage).
  - `constraints[].targetType` — enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session); required
  - `graph` — string | null; optional

- **`validate-mutation-trace`** — Replay a mutation trace and check invariants at each step.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `mutations` — array<object>; required
  - `mutations[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `mutations[].type` — enum(createEntity, updateEntity, deleteEntity, createRelation, deleteRelation); required
  - `mutations[].payload` — object; required
  - `invariants` — array<string> | null; optional
  - `graph` — string | null; optional

- **`validate-spec`** — Validate the graph against property specifications.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `properties` — array<object>; required
  - `properties[].world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `properties[].name` — string; required
  - `properties[].type` — enum(forAllNodes, forAllEdges, invariant, default); required
  - `properties[].level` — enum(node, edge, subgraph, graph) | null; optional
  - `properties[].invariantName` — string | null; optional
  - `properties[].field` — string | null; optional
  - `properties[].condition` — string | null; optional
  - `properties[].value` — any; optional
  - `graph` — string | null; optional

- **`verify-graph`** — Verify the graph against guards and spec properties.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `includeDefaults` — boolean | null; optional
  - `graph` — string | null; optional

## Visualization

- **`export-bundle`** — Assemble the TapestryBundle JSON for a graph scope.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `scope` — object; optional
  - `scope.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `scope.mode` — string; optional, default: "full"
  - `scope.center` — string | null; optional
  - `scope.depth` — integer; optional, default: 1
  - `scope.entityType` — string | null; optional
  - `scope.relationType` — string | null; optional
  - `scope.query` — string | null; optional
  - `include` — object; optional
  - `include.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `include.analytics` — boolean; optional, default: true
  - `include.temporal` — boolean; optional, default: true
  - `include.semantic` — boolean; optional, default: true
  - `title` — string | null; optional
  - `asOf` — string | null; optional
  - `maxEntities` — integer | null; optional

- **`export-graph`** — Write a compact, zero-infrastructure node-link JSON export of a graph.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `output` — string; required
  - `includeSuperseded` — boolean; optional, default: false
  - `entityTypes` — array<enum(concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis, inference_rule, inference_trace, research_session)> | null; optional
  - `force` — boolean; optional, default: false

- **`serve`** — Serve the interactive visualization live over a read-only REST API.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `host` — string; optional, default: "127.0.0.1"
  - `port` — integer; optional, default: 8000
  - `check` — boolean; optional, default: false

- **`visualize`** — Write a self-contained interactive HTML visualization of a graph scope.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `graph` — string | null; optional
  - `scope` — object; optional
  - `scope.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `scope.mode` — string; optional, default: "full"
  - `scope.center` — string | null; optional
  - `scope.depth` — integer; optional, default: 1
  - `scope.entityType` — string | null; optional
  - `scope.relationType` — string | null; optional
  - `scope.query` — string | null; optional
  - `include` — object; optional
  - `include.world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
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
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `question` — string; required
  - `answer` — string | null; optional
  - `entityIds` — array<string>; required
  - `outcome` — enum(useful, dead_end, corrected); required — How a recorded piece of work actually turned out (the experiential layer). ``useful`` is a positive citation of what it cited; ``dead_end`` and ``corrected`` are negative — the difference is that a correction says the graph was wrong, a dead end says it led nowhere.
  - `correction` — string | null; optional
  - `graph` — string | null; optional

## Workspaces

- **`begin-session`** — Start a namespaced, TTL-bearing session workspace for scratch graphs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `name` — string | null; optional — Optional human label for the session; purely descriptive, shown back by list-sessions and end-session but never used for addressing (sessionId is).
  - `ttlSeconds` — integer | null; optional — How long the session is expected to live, in seconds. Informational: past this point the session shows expired=true in list-sessions, but nothing reaps it automatically — end-session is always the one call that actually deletes its graphs.

- **`end-session`** — Reap a session in one call: delete every graph registered under its namespace and mark the session reaped.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `sessionId` — string; required — The sessionId returned by begin-session.

- **`list-sessions`** — List session workspaces with their namespace, TTL, and current member graphs.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.

## Worlds

- **`abandon-world`** — Mark a world's ref dead and delete its segment in one call.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `worldId` — string; required — The worldId returned by fork-world.

- **`diff-worlds`** — Semantic diff between two worlds: entities added/invalidated, confidences changed, relations added/removed, contested claims -- each with event ids.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `a` — string; required — A worldId, or 'main'.
  - `b` — string; required — A worldId, or 'main'.
  - `scope` — string | null; optional — Restrict the diff to 'entities' or 'relations'; omitted (default) reports both.

- **`fork-world`** — Fork a new belief world at a graph's (or another world's) current tip, or a historical moment via asOf. Writes no entity data -- O(1). The response's forkedAtEventId is informational/for-audit only (which event was live at fork time); the projection itself is anchored by forkedAt's wall-clock instant (compared against tx_from, never against stream position), so it stays well-defined even if that event's own append was later repaired out of order.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `name` — string | null; optional — Optional human label for the world; purely descriptive, shown back by list-worlds.
  - `graph` — string | null; optional — The base graph to fork from. Only meaningful when forming a fresh fork off 'main' (fromWorld omitted); ignored (inferred from the parent) when forkWorld names an existing world.
  - `fromWorld` — string | null; optional — The world to fork from — a worldId, or omitted/'main' for the graph's live state.
  - `asOf` — string | null; optional — Fork at this historical instant (ISO 8601, the wire format) instead of the parent's current tip — a bi-temporal fork.
  - `ttlSeconds` — integer | null; optional — Informational TTL, like a session's — nothing reaps a world automatically; abandon-world always does the deleting.

- **`list-worlds`** — List belief worlds with their parent, fork point, and status.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `includeReaped` — boolean | null; optional — Include abandoned/merged worlds. Defaults to false: a reaped world is never gone (list-worlds' history is still there, same as list-sessions'), but the default view does not grow monotonically as forks are abandoned/merged over a build's lifetime. Pass true for the full history.

- **`merge-world`** — Merge a world's changes into another (default 'main'). 'endorse-all' applies every uncontested change and notices the rest as CONTESTED_ON_MERGE; 'select' grafts exactly the named entityIds/eventIds.
  - `world` — string | null; optional — The belief world to read/write in (a worldId from fork-world, or omitted for 'main'). Reads project the fork point plus the world's own writes; writes land only in the world's own segment -- main is never mutable from inside a fork.
  - `from` — string; required — The worldId to merge from.
  - `into` — string | null; optional — The worldId (or 'main', the default) to merge into.
  - `strategy` — string; optional, default: "endorse-all" — 'endorse-all' applies every uncontested change 'from' made; 'select' applies only the named entityIds/eventIds, regardless of contest status (selecting IS the manual resolution).
  - `entityIds` — array<string> | null; optional
  - `eventIds` — array<string> | null; optional
