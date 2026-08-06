---
repo: the-loom
commit: 5b60a8b78c90634aeb8f99639acba9549bd3f9e5
graph: codebase-the-loom
generated: 2026-08-06
mode: incremental
---

# The Loom — Architecture Map

## 1. Executive overview

The Loom is a knowledge-graph substrate with exactly one user-facing surface: a
JSON-in / JSON-out command line of 164 commands across 23 categories, every one of them
generated from a single declarative registry. Underneath that registry sits a thin
command-operation layer that validates input, resolves which named graph to talk to, and
delegates: down to a FalkorDB-backed store that keeps graph topology, entity vectors,
document chunks and an append-only event log in one transactional place; sideways to a
set of deliberately pure computation libraries (graph algebra, semiring traversal,
symbolic math, computational-creativity scorers, exploration signals, verification
predicates); and into a semantic layer that owns the single definition of "nearest".
Above the commands sit composites — one-call bundles that chain many operations into a
single structured answer with per-section fault isolation, so one failing stage
degrades to a null section instead of taking the answer down. A separate visualization
arm turns a live graph into a bounded, versioned payload and ships it three ways: raw
JSON, a self-contained HTML page carrying a committed React/sigma.js build, or a
read-only REST service that the same page talks to in live mode. The shape that recurs
everywhere is *pure core, thin shell*: the algorithm has no store and no I/O, and the
module above it is a translation layer that shapes wire documents and typed errors.

### Stats

| Measure | Value |
|---|---|
| Source files in the map | 375 |
| External packages referenced | 65 |
| Symbols (classes, functions, methods, variables) | 4,656 |
| Written observations about the code (purposes, patterns, invariants, strains) | 1,126 |
| Records in the map, current state | 6,222 |
| Records including superseded revisions | 7,334 |
| Connections between them | 15,546 |
| Files not parsed | 42 |

Connections break down as: containment 5,318; general association 4,501; calls 3,593;
dependency 1,671; document-to-code references 238; type/instance links 199;
supersession 26.

Language mix, by file: Python 254, TypeScript 74, Markdown 19, JSON 14, CSS 9,
JavaScript 2, YAML 1, TOML 1, lockfile 1.

The working tree was clean at extraction time; this map describes commit `5b60a8b`
exactly. The 42 unparsed files are the formats tree-sitter has no grammar for in this
configuration — Markdown, JSON, CSS, YAML, TOML and the lockfile. They still appear as
files with their documentation links intact; only their internal symbols are absent.

---

## 2. Subsystem walkthrough

### 2.1 The core package — `theloom/`

**What it is.** The root package holds the cross-cutting contracts every other subsystem
depends on and which themselves depend on almost nothing. `model.py` is the single
source of truth for the domain: every enum value set in a stable wire order, the
Entity/Relation shapes, Confidence and Provenance, the paired `*Input` create schemas,
the filter shapes, the confidence-label scale and the five-state status lifecycle table.
`errors.py` defines the six structured error codes as an exception hierarchy. `config.py`
resolves configuration once, through one loader. `timeutil.py` is the only producer of
timestamps. `migrate.py` imports snapshot folders verbatim.

**Key files.** `theloom/model.py` (~575 lines), `theloom/config.py`, `theloom/errors.py`,
`theloom/timeutil.py`, `theloom/migrate.py`.

**How it is built.** Python attributes are snake_case and wire names are camelCase, and
the translation is done by Pydantic field aliases rather than by hand. Enums are the
stable wire contract, with runtime inventories derived from them. Each error subclass
carries its own CLI code. Retired enum members are coerced by a `BeforeValidator` instead
of being rejected.

**What must stay true.**

- Every wire timestamp is ISO 8601 UTC with a `Z` suffix — `theloom/model.py:38-49`,
  `theloom/timeutil.py:12-15`.
- Confidence scores are bounded to `[0.0, 1.0]` at both the field and the label boundary
  — `theloom/model.py:370`, `:475`, `:294-306`.
- `durability: volatile` requires `expiresAt` — `theloom/model.py:433-438`, `:511-515`.
- The five-state lifecycle holds: retracted is terminal, and only `investigating` returns
  to active — `theloom/model.py:314-338`, `:341-353`.
- Unknown fields are rejected; every wire model forbids extras —
  `theloom/model.py:361-364`.
- Errors carry their structured code from birth; the CLI never classifies by message text
  — `theloom/errors.py:12-19`, `:25`, `:32-53`.
- Configuration resolves once, with precedence flags > env > file > defaults —
  `theloom/config.py:150-219`, `:96-105`.
- Snapshot import is idempotent because it wipes the prefix first —
  `theloom/migrate.py:34-36`.

**Where it strains.** Config handling is fail-open for parse errors and fail-loud for
field errors (`theloom/config.py:125-147` versus `:114-122`). Relation requiredness is
split between the model and the command layer (`theloom/model.py:453-459`, `:524-528`).
The model enforces two invariants but only *advises* on the lifecycle — the transition
predicate is never referenced by the models themselves (`:341-353`, `:406`). Verbatim
snapshot import bypasses the event log by design (`theloom/migrate.py:11-19`, `:50-58`).
A process-global test seam for embedder injection lives inside the otherwise-pure config
module (`theloom/config.py:285-310`).

### 2.2 Persistence — `theloom/store/`

**What it is.** The place where the two hardest architecture promises stop being prose
and become Cypher. It maps the domain model onto one FalkorDB instance so graph
topology, entity vectors and the append-only event log share a single transactional
store, and it makes every mutation event-sourced and bi-temporal: a write is one Cypher
statement plus its stream append inside one Redis `MULTI`/`EXEC`; an update snapshots the
outgoing incarnation as a version node instead of overwriting it — for entities *and*, as
of the RelationVersion work, for edges; a delete invalidates rather than erases.

**Key files.** `theloom/store/falkor.py` (~1,144 lines), `theloom/store/space.py`
(the shared chassis: graph handle, event log, commit primitive, paged read, vector index),
`theloom/store/commit.py`, `theloom/store/read_port.py`, `theloom/store/multigraph.py`
and `theloom/store/bridges.py`.

**How it is built.** `GraphSpace` is the chassis both the knowledge store and the chunk
store sit on. Reads are prefiltered by a derived index on the node and then confirmed in
Python against the filter oracle. Every full-scan read is wrapped in SKIP/LIMIT paging.
The narrow read port exists so read-only consumers can be typed against a protocol rather
than a concrete store, and it has a second, non-throwaway in-memory implementation.

**What must stay true.**

- A mutation and its event are committed as one unit or neither reaches the server —
  `theloom/store/commit.py:91-103`, `theloom/store/events.py:75-82`.
- Exactly one half of a committed pair can fail at `EXEC`, and each is compensated —
  `theloom/store/commit.py:105-127`, `theloom/store/events.py:84-119`.
- Entity updates invalidate: the prior incarnation is snapshotted, never overwritten —
  `theloom/store/falkor.py:420-437`, called from `:414-417`, `:477`, `:565-571`.
- **Relation updates snapshot too**: an edge doc swap is preceded by a closed
  `:_RelationVersion` — `theloom/store/falkor.py:929-963`, mirrored in the in-memory
  adapter.
- Deletion invalidates by default; `hard=True` is the only path that destroys history —
  `theloom/store/falkor.py:439-500`, `:967-1026`.
- A retracted entity leaves the semantic reads because its vector is dropped —
  `theloom/store/falkor.py:478-480`, rationale at `:443-452`.
- `filters.py` is the semantics oracle; the Cypher pushdown may only be a superset —
  `theloom/store/falkor.py:154-178`, `:647-700`.
- Any full-scan read pages, or FalkorDB silently truncates it —
  `theloom/store/paging.py:1-45`, `theloom/store/space.py:113-118`.
- The vector index is write-once and sized from stored vectors, never from a query, and
  a create-then-query is only correct behind the OPERATIONAL barrier —
  `theloom/store/space.py:122-159`, `:159-187`, `:216-228`.
- Immutable fields survive every update, and status transitions are validated inside the
  write — `theloom/store/falkor.py:85-86`, `:400-408`, `:926-927`, `:207-216`.
- Legacy bridge migration is crash-safe and never drops an undrained document —
  `theloom/store/bridges.py:230-277`, `:300-309`.

**Where it strains.** `MULTI` is not a rollback boundary, so a multi-statement commit
owes a debt the module states in its own docstring (`theloom/store/space.py:59-101`), and
a committed mutation whose event repair fails leaves the log short or out of order — an
explicit non-promise (`theloom/store/commit.py:37-43`, `:157-170`). The derived read
index duplicates filter semantics that must be kept in sync by hand
(`theloom/store/falkor.py:101-114` versus `theloom/store/filters.py:69-100`). There are
two read surfaces and the abstract base class is not the one consumers use
(`theloom/store/base.py:44-161` versus `theloom/store/read_port.py:1-35`), and the read
port's own prose still describes relation updates as overwrite-in-place
(`read_port.py:106-109`) even though the code now snapshots. As-of reads reconstruct the
past by scanning the whole present (`theloom/store/falkor.py:320-393`). `get_neighbors`
does the per-id loop the module's own docstring forbids (`:1089-1095`). Bridges are graph
records that live outside every graph (`theloom/store/bridges.py:1-44`), and cross-graph
lookup scans every graph, building a store per graph to do it
(`theloom/store/multigraph.py:96-97`, `:132-137`).

### 2.3 The command line — `theloom/cli/`

**What it is.** The whole user-facing surface, with no domain behaviour of its own.
`registry.py` declares every command once, as a frozen descriptor built from a
declarative row: name, category, summary, Pydantic input model, handler, and an explicit
stance on empty stdin. `app.py` mechanically generates one Typer subcommand per
descriptor at import time. `io.py` owns the wire protocol. `docs.py` renders
`COMMANDS.md` as a pure projection of the registry.

**Key files.** `theloom/cli/registry.py` (1,676 lines; 164 descriptors across 23
categories built by 16 category factories), `theloom/cli/app.py` (119 lines),
`theloom/cli/io.py` (85 lines), `theloom/cli/docs.py` (37 lines).

**What must stay true.**

- Every command except `version` and `init` is generated from the registry —
  `theloom/cli/app.py:108-109`, with the two hand-written exceptions at `:54-57`.
- Input validation happens once, in `run_handler`, and Pydantic failures become
  `VALIDATION_ERROR` — `theloom/cli/registry.py:1666-1676`.
- stdout carries exactly the result document; diagnostics go to stderr and failures exit
  1 — `theloom/cli/io.py:79-80`, `:83-84`, `theloom/cli/app.py:96-102`.
- `COMMANDS.md` is byte-identical to generated output — `theloom/cli/docs.py:15-36`,
  pinned by `tests/test_generate_docs.py:35-40`.
- Input is JSON from the argument or stdin, must be an object, and stdin is capped at
  100 MB — `theloom/cli/io.py:17`, `:20-27`, `:38-39`.
- Non-finite floats serialize as `null`, keeping output valid JSON —
  `theloom/cli/io.py:56-64`, `:67-69`.
- Every command states its stdin stance explicitly; the flag carries no default —
  `theloom/cli/registry.py:112-123`.

**Where it strains.** The store client is imported lazily; the whole science stack is not
— one composite import pulls the optimal-transport library and Torch into every CLI
invocation (`theloom/cli/registry.py:33`). The one raw-handler escape hatch bypasses the
typed-error boundary (`:1669-1670` versus `:1671-1674`). Every registry command opens a
store connection before its handler runs (`theloom/cli/app.py:81-86`, `:98`).
Command-name uniqueness is assumed but never enforced (`registry.py:1659`) — verified
clean today at 164 descriptors with zero duplicates, so the hazard is latent.

### 2.4 Command semantics — `theloom/operations/` (three groups)

**What it is.** The seam between the CLI registry and everything below it. Each module
owns one command family and exposes one module-level function per command with the
uniform shape `(params, multi) -> dict`. The layer declares the wire schema, resolves
which graph store to talk to, adds the semantics the raw store deliberately does not
have, and delegates the rest.

**Group 1 — addressing, CRUD, consumption, analytics, algebra.** Key files:
`common.py` (the shared input base, UUID type and entity resolver), `entity.py`,
`consumption.py` (`explore` / `find-callers` / `find-callees` / `blast-radius`),
`analysis.py` (16 traversal and analytics commands), `algebra.py` (12 semiring and
routing commands).

- Entity addressing takes exactly one of id or name, and a blank name is not a name —
  `theloom/operations/common.py:112-117`.
- Consumption and blast-radius reads apply their own active-status filter —
  `theloom/operations/consumption.py:254-269`, applied at `:356` and `:339`.
- Truncation accounting is exact: shown plus cut equals total —
  `theloom/operations/consumption.py:400-416`.
- Bulk import enforces the same causal-only polarity partition as `create-relation` and is
  idempotent on the `name::entityType` key — `theloom/operations/bulk.py:208-217`,
  `:221-237`, `:319-330`.
- Blast radius counts the seed and its members as seeds, never as fallout —
  `theloom/operations/blast_radius_traversal.py:132`, `:139-144`.
- `update-entity` refuses illegal status transitions; `delete-entity` retracts by default
  — `theloom/operations/entity.py:249-252`, `:323-340`.

Strains: two error-classification policies inside one layer
(`documents.py:155-163` versus `analysis.py:518`); full-graph in-memory hydration where a
narrow read would do (`analysis.py:60-63`, duplicated at `algebra.py:51-54`); three
different name-resolution policies (`common.py:133-139`, `bulk.py:362-364`); retired
entities visible to semiring traversal but invisible to routing (`algebra.py:41-48`
versus `:51-54`); `previousVersionId` written as a self-reference
(`entity.py:271-272`); and `list-entities` returning two different response shapes
depending on whether a limit was supplied (`entity.py:346-355`, `:406-412`).

**Group 2 — relations, knowledge lifecycle, machinery.** Key files: `relations.py` (573
lines, the write gate), `epistemic.py` (943), `inference.py` (618), `reification.py`,
`extraction.py`.

- The causal/polarity partition is an invariant of the *stored* edge, not just of
  creation — `theloom/operations/relations.py:321-351`, create side at `:163-166`.
- A failing strict relation batch still persists its valid prefix —
  `theloom/operations/relations.py:280-282`.
- The endpoint gate is one verdict across both relation arities —
  `theloom/operations/relations.py:270-274`, `:169-184`.
- `merge-entities` supersedes the secondary rather than deleting it, and a completed
  merge is a no-op — `theloom/operations/merge.py:172-175`, `:186-195`.
- Inference rule conclusions may only reference variables bound by conditions —
  `theloom/operations/inference.py:188-194`; derived polarity comes only from the causal
  defaults table (`:381`); every derived relation carries provenance naming its rule and
  trace (`:391-398`).
- `reify-patterns` is idempotent through a fingerprint marker observation —
  `theloom/operations/reification.py:161-164`, `:174-177`.
- Credit propagation clamps confidence to `[0,1]`, halts below a minimum delta, and
  rewrites basis to `calculated` — `theloom/operations/epistemic.py:836`, `:854-858`.
- Re-extraction retires the legacy untyped twin of every typed call edge —
  `theloom/operations/extraction.py:116-155`.
- `init` writes a 0700 directory and a 0600 file, and is idempotent —
  `theloom/operations/init.py:22-32`, `:34-36`.

Strains: hard-delete escape hatches inside an event-sourced store
(`relations.py:377-396`, `inference.py:235-237`, `extraction.py:290-296`); `dryRun`
defaults that disagree across the mutating commands (`reification.py:102`,
`epistemic.py:807`, `inference.py:291`, `extraction.py:190`); `run-inference`'s dry run
still writing a trace entity (`inference.py:348-373`); rollback reporting counts that hide
the failures behind them (`extraction.py:290-315`); output fields advertising a bridge
capability the input schema cannot reach (`relations.py:229`, `:218-220`); machinery
decoded from observations vanishing silently when malformed (`inference.py:132-134`,
`:161-163`); and whole-graph scans in the analytical handlers beside the batch discipline
in `relations.py` (`epistemic.py:237-238`, `:377-380`).

**Group 3 — reasoning and assurance.** Key files: `semantic.py` (965 lines, the
largest), `verification.py` (621), `synthesis.py` (610), `solve.py` (387),
`work_memory.py` (175), `symbolic.py` (86).

- One retrieval binding backs every semantic read in the group —
  `theloom/operations/semantic.py:144-165`, used at `:554`, `:600`, `:687`, `:744`.
- Embedding is opt-in and content-hash idempotent —
  `theloom/operations/semantic.py:327-344`.
- An embedding failure is recorded on the entity, never raised to the caller —
  `theloom/operations/semantic.py:341-344`, counted at `:408-413`.
- Graph-mutating discovery and repair commands default to a dry run —
  `theloom/operations/semantic.py:492`, `:903`; `resolve-gaps` never duplicates an
  existing edge in either direction (`:940-945`).
- Anchor search never pays for the embedding model on a vectorless graph —
  `theloom/operations/synthesis.py:121-126`.
- Cross-graph synthesis is read-only and refuses to ingest —
  `theloom/operations/synthesis.py:334-340`, `:79-96`.
- Verification reads every entity status, not just active —
  `theloom/operations/verification.py:40`, `:120-126`.
- `validate-mutation-trace` never touches the target graph —
  `theloom/operations/verification.py:558-570`, `:594-596`.
- `record-outcome` writes nothing on a bad citation and cites each entity once —
  `theloom/operations/work_memory.py:103-123`, `:162-169`.

Strains: soft-fail commands opt out of the exit-code half of the error contract
(`solve.py:8-17`, `symbolic.py:3-7`); a hard delete inside an event-sourced store
(`work_memory.py:166-169`); an unknown invariant name is a hard error in one command and
a silent skip in another (`verification.py:202-208` versus `:294-297`); cross-graph
provenance is computed and then discarded (`synthesis.py:168-176` versus `:310-320`); the
embedding pipeline presents a queue-shaped API with no queue behind it
(`semantic.py:120-132`, `:469`); two disagreeing entity-type vocabularies live in
`embed-entities` (`semantic.py:321-324`, `:370-381`); and one upward import still points
from the engine layer back into operations (`verification.py:67-75`).

### 2.5 Meaning — `theloom/semantic/`

**What it is.** The meaning layer's engine room. It turns text into vectors, owns the
single definition of "nearest" and the single retrieval path, decides the order and
grouping of results, owns what "needs embedding" means and how a status/vector divergence
is repaired, and on top of that retrieval core decides whether a proposed entity already
exists and generates entities the graph is structurally missing.

**Key files.** `search.py` (the one retrieval core), `embed.py` (the embedding contract),
`ranking.py` (pure hybrid ranking stages), `embedding_state.py` (the state machine),
`deduplication_gate.py` and `entity_proposer.py` (the proposal pipeline).

**What must stay true.**

- Every vector is L2-normalized before it leaves the embedder —
  `theloom/semantic/embed.py:83-87`.
- Documents and queries are embedded with different prefixes, and no caller can bypass it
  — `theloom/semantic/embed.py:28-29`, `:90-99`.
- Embedding text is truncated at 30k characters, on a sentence boundary only within the
  last 20% — `theloom/semantic/embed.py:45-57`.
- Cosine similarity scores incomparable vectors 0.0 rather than raising —
  `theloom/semantic/embed.py:121-135`.
- One cosine-to-score conversion exists, and hits carry the raw cosine alongside it —
  `theloom/semantic/search.py:58-65`, `:141-151`.
- Vector search returns only active entities unless a caller explicitly opts out —
  `theloom/semantic/search.py:136`, `:102-108`.
- `needs_embedding` is the single skip predicate — `theloom/semantic/embedding_state.py:49-61`.
- The dedup gate matches within one entity type but across all five statuses —
  `theloom/semantic/deduplication_gate.py:117-125`; its threshold is clamped to
  `[0.5, 0.99]` and the clamped value is what gets reported (`:27-29`, `:86`).
- The proposer is read-only — `theloom/semantic/entity_proposer.py:96-152`.

**Where it strains.** Step 4 of the proposal pipeline filters nothing
(`entity_proposer.py:554-576` against the comment at `:128-131`). The LLM reasoning
strategy is on by default and unreachable in practice (`:10-13`, `:108`, `:121-122`).
Violation semantics travel as prose and are recovered by regex (`:63-66`, `:202-215`).
The gate's two paths do not agree on what a duplicate is
(`deduplication_gate.py:117-126` versus `:167-173`), and it fabricates `active=True` for
every candidate it resolves (`:103-106`). A strict minimum score escalates the retrieval
core to a full index scan (`search.py:132-134`, `:154-156`). Three hard-coded type lists
in the proposer shadow the domain model (`entity_proposer.py:42-61`, `:422`, `:431`).

### 2.6 Extraction — `theloom/extraction/`

**What it is.** The package that turns artefacts outside the graph into graph content.
Its dominant path is deterministic and LLM-free: tree-sitter parses each source file into
file/class/function/variable records plus containment, call, type and dependency edges; a
whole-project second pass joins the edges no single-file parse can resolve; a third pass
links Markdown documents into the code they name; and one module owns every name,
observation prefix and evidence string so writers and readers cannot drift.

**Key files.** `treesitter.py` (1,387 lines), `resolution.py`, `doclinks.py`,
`encoding.py`, `codebasediff.py`.

**What must stay true.**

- An incremental update supersedes entities; it never deletes them —
  `theloom/extraction/codebasediff.py:462-472`.
- A callee that does not resolve to exactly one reachable target produces no edge —
  `theloom/extraction/resolution.py:431-451`.
- A structural edge belongs to a changed file when either endpoint does —
  `theloom/extraction/codebasediff.py:298-320`.
- The structural diff never retracts an edge structural extraction did not emit —
  `theloom/extraction/codebasediff.py:78-88`, `:266-282`.
- Extraction output is deterministic for a given tree —
  `theloom/extraction/treesitter.py:1203-1211`.
- Only bare-identifier calls become call edges —
  `theloom/extraction/treesitter.py:384-398`.
- Line numbers are 0-based in code and 1-based in the graph, and the round trip is the
  identity — `theloom/extraction/encoding.py:17-23`, `:117-134`.
- Self-model update refuses any repository that is not The Loom —
  `theloom/extraction/selfmodel.py:30-52`, `:55-62`.
- A single document can contribute at most 50 references —
  `theloom/extraction/doclinks.py:74`, `:233-241`.

Note: Go and Rust files parse to a file record and nothing else — any language without
an extractor returns empty symbol, import and call lists
(`theloom/extraction/treesitter.py:830-836`).

**Where it strains.** One package holds two extraction philosophies that share no code —
the LLM pipeline reaches into synthesis, model and store, and imports nothing from the
tree-sitter half (`pipeline.py:12-26`). Inferred edges enter a graph whose consumers
treat every edge as fact (`resolution.py:437-448`, `doclinks.py:15`). The incremental
update is incremental only in its writes: it still extracts the whole tree first
(`codebasediff.py:517-520`). Extraction run records live outside the graph's bi-temporal
history, in Redis lists (`runstore.py:29`, `:84`). And a bare `assert` guards a runtime
precondition in a typed-error codebase (`pipeline.py:125`).

### 2.7 Graph algebra — `theloom/graph/`

**What it is.** The in-memory graph layer. It hydrates wire documents into a small
insertion-ordered directed multigraph and runs the pure structural analyses on top:
centrality and components, cycle detection and feedback-loop classification, shortest and
bounded all-simple paths, frequent-subgraph mining, subgraph extraction filters, and the
parsers that read structured facts back out of loop and leverage-point observations.

**Key files.** `hydrate.py`, `analytics.py`, `cycles.py`, `paths.py`, `motifs.py`.

**What must stay true.**

- Hydration drops dangling relations, so no edge can reference an absent node —
  `theloom/graph/hydrate.py:118`.
- Neighbor iteration is deduplicated and order-fixed at IN-then-OUT —
  `theloom/graph/hydrate.py:87-96`, `:73-85`.
- Loop polarity is the parity of negative edges, with missing polarity read as positive —
  `theloom/graph/cycles.py:258-267`.
- PageRank converges to the stated tolerance or raises rather than returning provisional
  scores — `theloom/graph/analytics.py:55-68`.
- Motif identity is the canonical signature; the pattern id is only a per-response
  ordinal — `theloom/graph/motifs.py:38-41`, `:164-167`.

**Where it strains.** The library-preference invariant sits against byte-stable output
ordering, and only one algorithm is actually delegated (`analytics.py:3-14`,
`cycles.py:3-12`). A known deviation from Johnson's algorithm is preserved deliberately
as bug-compatibility (`cycles.py:115-117`). Half the group recurses and half uses explicit
stacks, with no depth guard anywhere (`analytics.py:119-142`, `cycles.py:38-50`). The
pure algorithm layer writes to the store for loop persistence (`cycles.py:20`,
`:293-334`). And an untyped `RuntimeError` escapes a codebase built on typed error codes
(`analytics.py:68`).

### 2.8 Weighted traversal — `theloom/algebra/`

**What it is.** The pure computational core for weighted traversal: five semirings as
frozen operator records, weight extractors that turn a relation's strength label into a
semiring element, one shared DFS engine, and above that a registry sorting relation types
into structural/epistemic/causal categories with a table of six cross-category morphisms,
a query router, a segmented executor and a metapath walker.

**Key files.** `core.py`, `routing.py`.

**What must stay true.**

- Traversal is a backtracking DFS, not Bellman-Ford: value and path are decoupled —
  `theloom/algebra/core.py:191-207`.
- Adjacency emission order is part of the public contract; ties keep first discovery —
  `theloom/algebra/core.py:141-148`, `:5-7`.
- Relation categorization is total, with causal as the open-world default —
  `theloom/algebra/routing.py:31-49`.
- Approximate morphisms are exactly the tropical/viterbi pair and are labelled as such —
  `theloom/algebra/routing.py:64-68`, `:94-103`.
- A missing source entity yields an empty result map rather than an error —
  `theloom/algebra/core.py:234-235`, `:265-266`.

**Where it strains.** Two semiring resolvers with deliberately divergent semantics
(`core.py:85-92` versus `:95-105`). Missing-morphism handling is inconsistent across the
three consumers — two raise, one silently continues (`routing.py:218-220`, `:549-551`,
`:335-337`). Hand-rolled operator tables sit against the stated library-first invariant
(`core.py:9-11`). Metapath expansion has no cycle guard and no frontier cap
(`routing.py:525-528`, `:586-596`).

### 2.9 Computational creativity — `theloom/analysis/`

**What it is.** A store-free, I/O-free library of scoring and search algorithms that turn
an already-hydrated graph into cross-domain mappings, analogy transfers with novel-entity
proposals, concept slippages, approximate subgraph matches, Weisfeiler-Leman component
signatures, far-analogy candidate pairs, and interestingness/confidence/adaptability
scores. It is a direct implementation of a named literature stack.

**Key files.** `cwsg.py` (the transfer pipeline), `crossdomain.py`, `slippage.py`,
`absence_surprise.py`, `adaptability.py`.

**What must stay true.**

- Novel transfer endpoints are prefixed placeholders, never graph ids —
  `theloom/analysis/cwsg.py:31`, `:110-119`, `:143-145`.
- Cross-domain mapping is strictly one-to-one — `theloom/analysis/crossdomain.py:198-219`.
- Only relations attached to the matched relational core transfer —
  `theloom/analysis/cwsg.py:81-85`, `:370-393`.
- Temperature is clamped to `[0,1]` and lowers the slippage threshold monotonically —
  `theloom/analysis/slippage.py:54-56`, `:37`.
- Absence surprise reports the maximum absence as its overall score, not the average —
  `theloom/analysis/absence_surprise.py:359-361`.
- Component signatures are only comparable through a shared global hash ordering —
  `theloom/analysis/component_signatures.py:83-87`, `:113-125`.
- **The Weisfeiler-Leman hashing primitive is shared with `reify-patterns` to stay
  bit-identical** — `theloom/analysis/component_signatures.py:30-32`, with the requirement
  stated at `:19-21`. This is the resolution of what was previously two frozen copies of
  one hash.

**Where it strains.** Adaptability skips the weight normalization every sibling scorer
applies (`adaptability.py:124-128` versus `interestingness.py:154-163`). Timeout budgets
are advertised in three modules and enforced in one (`crossdomain.py:29`,
`slippage.py:45`). A timed-out subgraph search is indistinguishable from a complete one
(`isomorphism.py:193-194`, `:243-244`). Oversized input raises in one module and is
silently truncated in another (`crossdomain.py:167-174` versus `slippage.py:238`). Two
`farAnalogyScore` fields carry incomparable scales
(`component_signatures.py:217` versus `sliced_wasserstein.py:110`). And mismatched vector
widths raise in one comparator while scoring zero in its neighbour
(`component_signatures.py:141-142` against the shared cosine helper it imports at `:33`).

### 2.10 One-call bundles — `theloom/composites/` (two groups)

**What it is.** The high-level commands that chain many internal operations into a single
structured answer. Each module owns exactly one command: it declares an input model,
resolves a store, runs a fixed ordered list of named sections through the shared runner,
and returns an envelope carrying per-section data, wall-clock timing and error text.

**Group 1 — reconnaissance, proposal and the framework.** Key files: `framework.py` (the
shared envelope and runner), `far_analogy_retrieval.py` (five chained sections),
`creativity_loop.py` (the only module composing two other composites),
`enrichment_crawl.py` and `gap_fill_cycle.py` (the two graph-mutating composites).

- A timed section never raises: every outcome is a three-key result, and a non-null error
  always accompanies null data — `theloom/composites/framework.py:42-56`, `:52`, `:56`,
  `:61`.
- The framework imports nothing from `theloom`, preventing a layering leak —
  `theloom/composites/framework.py:15-22`.
- Exactly two composites in this group write to the graph, and both go through
  `create_relation` — `gap_fill_cycle.py:166-184`, `enrichment_crawl.py:395-408`.
- Enrichment-crawl never infers a causal relation type, and never a direction between
  same-type endpoints — `enrichment_crawl.py:82-84`, `:141-161`.
- Creativity-loop terminates on evidence, not on a fixed cycle count, and never mutates
  the graph — `creativity_loop.py:375-381`, `:301-304`.

**Group 2 — analysis bundles, simulation and the self-improvement capstone.** Key files:
`self_improve.py` (605 lines), `simulate_change.py`, `reflect.py`,
`verified_extract.py`, `structural_survey.py`.

- `simulate-change` never mutates the graph it is asked about: it snapshots, clones to a
  temporary graph, applies there, and diffs — `simulate_change.py:246-249`.
- The simulation clone copies entities across every status, not just active ones —
  `simulate_change.py:105-107`.
- `self-improve` writes nothing unless auto-apply is explicitly true —
  `self_improve.py:73`, `:214`, `:341-348`.
- A relation-batch failure hard-deletes the entity that was just applied — the
  compensating half of the saga — `self_improve.py:441-466`.
- Proposals that degrade or could not be simulated are dropped before ranking —
  `self_improve.py:320`, `:333-337`. (This is the fix for the earlier defect where an
  un-simulatable proposal ranked *higher*.)
- A reflection replaces the previous usage-status observation and retracts it when no
  verdict is reached — `reflect.py:276-285`; `preferred` requires both a positive decayed
  score and the corroboration floor (`:225-233`).
- `verified-extract` orders extraction before credit propagation so the cascade can target
  new entities — `verified_extract.py:184-192`.

**Where the package strains.** Three incompatible top-level result shapes across one
package (`analogy_transfer.py:8-13`, `propose_entities.py:2-6`,
`far_analogy_retrieval.py:492-501`). Blanket exception capture buys resilience and costs
diagnosability (`framework.py:53-56`). Input schemas declare knobs that are inert from the
CLI (`far_analogy_retrieval.py:88-89`, `enrichment_crawl.py:104`). PageRank scores are
published under the key `eigenvector` (`graph_reconnaissance.py:132-138`). Auto-apply's
write cycle spans five mutations with only partial compensation
(`self_improve.py:362-385`, `:441-466`, `:468-489`, `:492-544`). Full-graph cloning buys
perfect isolation at O(graph) cost per simulation (`simulate_change.py:100-110`), and
best-effort cleanup can leave `sim-<uuid>` graphs behind (`:310-313`). `reflect` reads
like a report but mutates entity observations by default (`reflect.py:97`, `:315`).

### 2.11 Documents — `theloom/documents/`

**What it is.** The subsystem that turns files, directories, raw strings and URLs into
embedded, searchable chunks living inside the same FalkorDB instance as the graph. It
owns format detection and parsing into one uniform block list, size-aware chunking with
sentence overlap, SSRF-hardened fetching, a declared chunk-metadata shape, and
persistence into a dedicated per-prefix chunk graph.

**Key files.** `ingestion.py`, `chunker.py`, `parsers.py`, `chunkstore.py`, `ssrf.py`,
`metadata.py`.

**What must stay true.**

- Chunk writes are event-sourced through the store's shared commit primitive —
  `theloom/documents/chunkstore.py:21-33`, `:103`.
- Chunks live in one per-prefix chunk graph, global across knowledge graphs —
  `theloom/documents/chunkstore.py:3-9`, `:56`, `:69-78`.
- `sourceId` is a deterministic hash prefix of the resolved path, URL or caller id —
  `theloom/documents/ingestion.py:51-57`.
- Reingest preserves chunk identity and skips unchanged chunks —
  `theloom/documents/ingestion.py:342-344`, `:350-351`.
- A chunk's content hash covers the overlap prefix, not just its own body —
  `theloom/documents/chunker.py:6-8`, `:205-209`.
- Every fetch hop requires all resolved addresses to be globally routable —
  `theloom/documents/ssrf.py:32-36`, `:58-71`.
- Embedding failure never blocks chunk persistence; the reason is stored on the chunk —
  `theloom/documents/ingestion.py:60-69`, `:143-145`.
- Directory ingest never follows symlinks and never re-ingests a known source —
  `theloom/documents/ingestion.py:429`, `:204`.

**Where it strains.** First ingest appends blindly while reingest diffs, so re-ingesting
a file duplicates its chunks (`ingestion.py:141-146`, `chunker.py:224`). Directory ingest
records per-file errors and then strips them before returning (`ingestion.py:209-228`).
Chunk queries apply the row limit before the category filter
(`chunkstore.py:110-125`). The SSRF guard resolves DNS separately from the connection it
protects — a documented residual TOCTOU window (`ssrf.py:7-9`, `:74-80`). And HTML and
JSON extraction drop content by allowlist and length threshold (`parsers.py:180`,
`:187-227`).

### 2.12 Exploration signals — `theloom/exploration/`

**What it is.** The foraging-signals foundation behind `explore-frontier`. It turns a
graph's connected components into ranked "where should I look next" recommendations from
four independent normalized signals — age staleness, bridging potential, coverage gap and
a UCB1 exploration bonus — fused by a renormalizing weighted average, with a
marginal-value-theorem patch-leaving policy on top and six anti-pattern guards.

**Key files.** `__init__.py` (a 42-name facade), `composite_signals.py`, `guards.py`
(486 lines), `exploration_state.py`, `coverage_gap.py`.

**What must stay true.**

- Every signal score is clamped to `[0, 1]` — `age_staleness.py:98-99`,
  `bridging_potential.py`.
- Absent signals are dropped and weights renormalized, never treated as zero —
  `composite_signals.py:70-89`.
- Region identity is the smallest entity id in sorted order —
  `exploration_state.py:89-97`.
- Exploration state is in-memory only and starts zeroed on every run — a stated design
  decision, with no sidecar file — `exploration_state.py:6-22`, `:100-113`.
- Coverage gap bounds its quadratic cost by capping vectors per region at 500 —
  `coverage_gap.py:33-34`, `:117-123`.
- Missing evidence scores as maximally explorable, not as zero —
  `age_staleness.py:92-93`.

**Where it strains.** Three incompatible region-identity schemes coexist
(`exploration_state.py:89-97` versus `guards.py:374` versus `guards.py:153-161`). The
stateless-by-design state store leaves the UCB bonus and the patch-leaving policy
informationless per run (`exploration_state.py:100-113`, `composite_signals.py:50-51`).
Bridging potential collapses to a binary constant under its documented usage
(`bridging_potential.py:12-16`). Fault isolation covers the signals but not the two
orchestrating entry points (`mvt.py:51-61`).

### 2.13 Verification — `theloom/verification/`

**What it is.** The rule layer: a store-agnostic library of predicates that decide whether
a graph, or a single proposed mutation, satisfies the model's structural promises. Four
faces — read-side guards and the five builtin invariants, the mutation gate that
`create-entity` and `create-relation` call before writing, the shared coverage/coupling
generators, and a capability DSL plus an AC-3 constraint propagator.

**Key files.** `checks.py`, `guards.py`, `metrics.py`, `capability_spec.py`,
`propagation.py`.

**What must stay true.**

- Polarity belongs to causal relation types only, enforced on write and mirrored on read
  — `theloom/verification/guards.py:64-71`, `theloom/verification/checks.py:92-110`.
- Retracted entities read back but cannot be relation endpoints —
  `guards.py:97-101`, `:104-107`.
- The endpoint verdict takes a status, not a store, so single and batch writes cannot
  diverge — `guards.py:81-101`.
- The entity gate warns without blocking; the relation gate errors and blocks —
  `guards.py:41-52`, `:55-78`.
- Read-side guards judge only fields that are present; requiredness is the model's job —
  `checks.py:41-45`, `:60-62`, `:78-80`.
- Causal cycle detection exempts edges pointing into loop entities —
  `checks.py:255-262`.
- Capability validation reads every lifecycle status, not just the active projection —
  `capability_spec.py:25`, `:36-40`.
- **The shared capability generators live here so the operations layer imports downward**
  — `metrics.py:1-9` states the placement rule verbatim. This is the resolution of the
  former upward dependency from the rule layer into the command layer.

**Where it strains.** The capability-name format string is duplicated on both sides of the
deduplicated generator (`capability_spec.py:78` versus `metrics.py:25`). Only two of the
five capability checks were deduplicated; three were copied
(`capability_spec.py:8-12`, `:60-70`, `:107-145`). Every capability check re-lists the
entire graph (`capability_spec.py:83-84`, `:98-99`, `:111-112`, `:152-153`). Cycle
detection recurses in Python on graph depth (`checks.py:184-210`). The duplicate-name
warning matches partially and case-insensitively, and is then written into the graph
(`guards.py:46-51`). An unrecognised coupling metric silently falls back to degree
centrality (`metrics.py:65`). And `checks.py`'s own module docstring describes a module
that no longer exists (`checks.py:1-8`).

### 2.14 Prose generation — `theloom/synthesis/`

**What it is.** The package that turns a graph into prose, and prose back into checkable
claims. Its spine is Plan-Traverse-Realize: the planner selects an anchored ego-subgraph,
decomposes the query and orders the regions; the traverser walks them, emitting per-entity
evidence with Viterbi-decayed confidence and a step-by-step provenance trail; the realizer
linearizes each region causally and renders it as narrative, outline, causal chain,
evidence map, proposal or raw text. A fidelity grader then scores the output against the
graph it came from, and a CEGIS loop refines candidate graphs against verification
counterexamples.

**Key files.** `planner.py`, `traverser.py`, `realizer.py`, `cegis.py`, `llm.py`.

**What must stay true.**

- Synthesis output is fully deterministic when no LLM is configured —
  `theloom/synthesis/llm.py:215-218`, `decomposer.py:70-71`, `realizer.py:314-317`.
- The seeded PRNG is bit-exact 32-bit, so a seed determines the candidate graph exactly —
  `generator.py:28-60`.
- CEGIS verification touches no store; only a successful commit does —
  `cegis.py:368`, `:129-163`.
- The CEGIS loop always terminates — `cegis.py:382-418`, `:73-74`.
- Generated graphs contain no self-loops and no duplicate endpoint/type triples —
  `generator.py:374-386`, `cegis.py:146-151`.
- The fidelity composite index is a weighted harmonic mean that collapses to zero if
  either component is zero — `fidelity.py:22-24`, `:351-359`.
- Provenance is append-only and sealed at finalize — `traverser.py:51-56`, `:77-86`.
- Adaptive traversal visits every entity at most once across all regions —
  `traverser.py:143`, `:156`, `:174`.

**Where it strains.** Source passages are structurally supported but permanently empty
(`links.py:13-14`). Quick verification falls back to regex-matching violation prose
(`cegis.py:278-303`). Two fidelity modes report the same score field with incomparable
semantics (`fidelity.py:220-274` versus `:277-348`). LLM and parse failures are swallowed
without a signal (`fidelity.py:152-153`, `decomposer.py:75-85`). And the pipeline works in
untyped wire dictionaries while the project holds the domain model as its single source of
truth (`selector.py:19`, `planner.py:26`, `traverser.py:19`).

### 2.15 Symbolic math — `theloom/symbolic/`

**What it is.** The in-process computer-algebra engine: one total function that maps a
string operation name onto one of 21 handlers, executes it under a signal-based watchdog,
and returns a JSON-serializable envelope instead of raising. It owns all expression
parsing, all formatting, and a small chain interpreter that pipes one step's result into
the next through a named namespace.

**Key files.** `core.py` (1,026 lines — the entire engine).

**What must stay true.**

- `run` returns an envelope rather than propagating handler exceptions —
  `theloom/symbolic/core.py:1017-1022`, `:1001-1006`.
- Every successful handler returns both a string result and a string LaTeX result —
  `core.py:70-75`, `:61-67`.
- The watchdog is clamped to 1-120 seconds and always restores prior signal state —
  `core.py:1008-1009`.
- Verification selects its mode by parameter shape, in a fixed precedence —
  `core.py:138-141`, `:200-202`.
- No module here imports any other Loom module; the dependency arrow points strictly
  inward.

**Where it strains.** LaTeX is an output format but never a working input format — the
parse branch is dead and the docstring admits it (`core.py:7-8`, `:36-43`). The
never-raises guarantee has a hole outside the main thread, where signal installation
itself can fail (`core.py:998-1000`, `:1014-1016`). Sympify on caller-controlled strings
assumes a trusted caller (`core.py:48`, `:772-776`). The dispatch table has grown to 21
operations while the module still describes seven (`core.py:3`, `:969-991`).

### 2.16 Structural fingerprints — `theloom/reification/`

**What it is.** The single, shared implementation of Weisfeiler-Leman ego fingerprinting
over a hydrated graph. Each node is reduced to a short hash of its rooted neighborhood up
to a bounded depth, so that nodes whose local structure is isomorphic collapse to the same
digest and can be bucketed into structural pattern groups. The package exists to
de-duplicate that hashing logic: `reify-patterns`, the entity proposer and component
signatures all consume it, so their fingerprints stay comparable.

**Key files.** `fingerprint.py` (the whole implementation).

**What must stay true.**

- Fingerprints are invariant to adjacency ordering —
  `theloom/reification/fingerprint.py:50-52`.
- Depth is clamped to `[0, 10]` at both public entry points — `:93`, `:133`.
- The module is pure: it reads a hydrated copy and never mutates or persists — `:10`,
  `:15-18`.
- Grouped output is deterministic in order and size — `:150-160`, `:161`.
- Memo keys carry depth, so one cache is safe across mixed-depth calls — `:57`, `:74-77`.

**Where it strains.** The hash is direction-aware at depth 1 and direction-blind beyond
it (`:63-72` versus `:73-79`). A group's description reports one arbitrary member rather
than the group (`:139-146`). The 64-bit truncated digest trades compact keys for silent
collision merging (`:26-27`, `:137-148`). And the module works in untyped dictionaries
inside a codebase whose model is the source of truth (`:20`, `:38-48`).

### 2.17 Visualization payload — `theloom/viz/`

**What it is.** The arm that turns a live graph into a shippable payload. It resolves
which slice to show, enriches it with three optional analysis sections, validates the
whole thing against a versioned wire contract, and emits it through one of three
transports: raw JSON, a self-contained HTML page carrying the committed React/sigma.js
build, or a read-only REST service the same page talks to in live mode.

**Key files.** `bundle.py` (the single assembler), `schema.py` (the versioned contract),
`scope.py`, `html.py`, `serve.py`.

**What must stay true.**

- Every bundle the assembler returns has passed contract validation —
  `theloom/viz/bundle.py:146-165`, `theloom/viz/schema.py:82-89`, `:13`.
- Injected JSON can never terminate the template's script block —
  `theloom/viz/html.py:33`, used by both emission paths.
- A missing or unbuilt frontend template fails as a typed configuration error —
  `theloom/viz/html.py:28-32`, `:37-44`.
- Live-mode HTTP status is a typed-code table lookup, never prose matching —
  `theloom/viz/serve.py:28-35`, `:96-103`.
- Degree truncation is deterministic and always disclosed —
  `theloom/viz/bundle.py:44-76`, wired at `:117-12x`.
- The bundle ships entities of every status, not just active ones —
  `theloom/viz/scope.py:38-43`, `:68`.
- Live mode is read-only: every registered route is a GET — `theloom/viz/serve.py:108`,
  `:140`, `:166`, `:181`, `:199`, `:208`, `:216`.
- A graph with fewer than three embedding vectors omits the semantic section entirely —
  `theloom/viz/semantic.py:19-21`, `:65-79`.
- Bi-temporal reconstruction is one store call, not a client-side approximation —
  `theloom/viz/scope.py:55-73`.

**Where it strains.** The `asOf` bound covers entities, relations and events but leaves
analytics and the semantic projection at the present, so a historical view shows
historical nodes with present-day centrality (`bundle.py:121-135`, `analytics.py:56-59`).
Scoped or truncated entity sets coexist with whole-graph analytics and projection
(`bundle.py:113-133`). `asOf` is validated by date parsing but applied by byte comparison
(`bundle.py:106-111` versus `temporal.py:15`). Search scope silently drops the non-active
entities the rest of the bundle ships (`scope.py:88-98`). Analytics ships every bridge in
the multigraph while the rest of the bundle is one graph's slice (`analytics.py:82`). The
static path writes to a caller-controlled filesystem location (`html.py:55-58`). And the
live server is unauthenticated with a caller-supplied bind host (`serve.py:43-48`, `:250`,
`:93`).

### 2.18 The Tapestry front end — `tapestry/`

**What it is.** A contributor-only Vite/React/sigma.js workspace that renders an exported
bundle of a Loom graph. Its build produces exactly one artifact that escapes into the
Python distribution: a single-file HTML template carrying a data sentinel the Python side
substitutes at render time.

**The shell and shared kernel.** `main.tsx` is the whole bootstrap — one root that wraps
the app in a bundle provider, so nothing below ever renders without a bundle in hand.
`App.tsx` is chrome and router in one: a fixed header with the brand mark, the bundle's
title and counts, an ARIA tablist of the five views, the live-server indicator with its
graph switcher, a help trigger and a theme radiogroup. `src/lib/` is the shared kernel:
bundle sourcing across three delivery modes (live REST, injected inline JSON, dev
fixture), the drag controller, label renderers, SVG/PNG export, keyboard handling, roving
focus and per-graph saved views.

- Live/static/dev mode is decided by parsed shape, never by comparison against the
  sentinel literal — `tapestry/src/lib/data.ts:33-45`, `tapestry/src/lib/live.ts:1-11`.
- Views never observe a null bundle or a null graph —
  `tapestry/src/lib/BundleContext.tsx:66-68`.
- **Every bundle-load failure is a typed error naming the branch that failed, and never
  strands the app on the loading gate** — `tapestry/src/lib/data.ts:82-101`, `:69-77`,
  `tapestry/src/lib/BundleContext.tsx:61-64`. (This closes the earlier defect where a
  fetch error left the front end loading forever.)
- Hash restore runs before the hash writer subscribes, and the URL hash is replaced,
  never pushed — `tapestry/src/App.tsx:233-245`, `:248-258`, `:252-254`.
- A drag's "moved" latch is sticky and the trailing click it suppresses is consumed
  exactly once — `tapestry/src/lib/dragState.ts:46-57`, `dragNodes.ts:85`, `:155-159`.
- The normalization bounding box is frozen for the gesture, so mid-drag re-renders cannot
  rescale the graph — `tapestry/src/lib/dragNodes.ts:95`, `:120`.
- Any view that wraps labels must also override the hover renderer, or labels double-draw
  — `tapestry/src/lib/nodeLabels.ts:193-207`.
- PNG export must refresh synchronously before reading the canvases —
  `tapestry/src/lib/exportSvg.ts:298-299`, rationale at `:284-292`.
- Saved-view reads and imports never throw — `tapestry/src/lib/savedViews.ts:25-34`,
  `:93-122`.
- Global shortcuts never fire while the reader is typing or holding a modifier —
  `tapestry/src/lib/keyboard.ts:20-24`, `:30-39`.
- The TypeScript bundle type is pinned to the committed JSON Schema in both directions —
  `tapestry/src/lib/schema.test.ts:171-228`.

**The five views.** Explorer is the primary weave: a WebGL force-directed canvas with
search, non-destructive facet filters, a shortest-path tool, legend, minimap, saved views
and export. Overview is a read-only dashboard rolling the bundle into headline tiles,
composition bars, health signals, a confidence histogram and a centrality table. Systems
re-reads the weave as a causal-loop diagram. Chronicle replays the bi-temporal event log
with a scrubber and a two-instant diff. Semantic Map plots the precomputed embedding
projection with cluster hulls and a lasso brush.

- Visibility and emphasis never mutate the shared graph — they are computed per frame —
  `tapestry/src/views/explorer/filters.ts:45-62`.
- Dangling relations are skipped when rendering but counted as a health signal —
  `tapestry/src/views/explorer/buildGraph.ts:197-198`.
- Pre-layout node positions are a deterministic function of entity id, so a variable keeps
  the same seeded position across Explorer and Systems —
  `buildGraph.ts:138-162`, `views/systems/systems.ts:79-88`.
- Path search is undirected for reachability, but every rendered hop keeps its true edge
  direction — `views/explorer/pathMode.ts:25-33`, `:37-43`.
- The Systems graph holds only causal-family relations and the entities they touch —
  `views/systems/systems.ts:63-72`, `:75-89`.
- Loop edge resolution is directed — out-edges, never undirected lookup —
  `views/systems/systems.ts:125-139`.
- Flow animation exists only while a loop is isolated, and never spins a frame loop under
  reduced motion — `views/systems/SystemsView.tsx:366-373`, `:375-378`.
- Retraction replays as a status change, never as a node removal —
  `views/chronicle/replay.ts:150-162`; an item with no creation event is present from the
  start (`:231-232`); an edge is visible only when both endpoints are
  (`:248-256`).
- A malformed URL fragment yields an empty patch — `src/state/urlHash.ts:15-22`.
- The projection *is* the layout in the Semantic Map; no force algorithm ever runs —
  `views/semantic/semanticMap.ts:50-64`.
- Every point stays visible; the brush dims rather than filters —
  `views/semantic/SemanticView.tsx:199-204`.
- Overview stats read the bundle arrays, never the rendered model, so dangling relations
  stay countable — `views/overview/stats.ts:5-9`, `:56`, `:64-65`.
- A historical bundle labels its analytics as current-only rather than mixing two times —
  `views/overview/Overview.tsx:140-144`, `views/systems/SystemsView.tsx:104-116`.
- Every Sigma instance, layout driver and listener is destroyed in its effect cleanup —
  `views/explorer/Explorer.tsx:299-307`, `views/systems/SystemsView.tsx:324-335`.

**Build, contract and acceptance.** `npm run build` is a three-stage gate — typecheck,
bundle, then emit — and no template is emitted unless the data sentinel survived bundling
(`tapestry/package.json:8`, `tapestry/scripts/emit-template.mjs:4-8`). The emitted HTML is
the only artifact crossing from the Node workspace into the Python package
(`emit-template.mjs:8`). The committed JSON Schema is a three-way contract: the Pydantic
model exports it, the TypeScript type is pinned against it, and a Python drift test fails
when they disagree. Seven Playwright specs re-create the shipped artifact from committed
inputs and drive it over `file://`, with a zero-serious-violations accessibility gate
(`tapestry/e2e/a11y.spec.ts:42-45`).

**Where it strains.** Exports are called WYSIWYG but omit every DOM-overlay decoration —
glyphs, badges and cluster hulls are absent from both PNG and SVG
(`src/lib/exportSvg.ts:24-30`, `views/systems/SystemsView.tsx:439-442`,
`views/semantic/SemanticView.tsx:400-405`). Four of the five tabs point `aria-controls` at
panels that are not in the DOM, because exactly one view is mounted at a time
(`src/App.tsx:307` against `:395-407`). The shortcut sheet is a hand-maintained copy of
bindings defined elsewhere (`views/HelpOverlay.tsx:27-60`). Node dragging is enabled in
the one view where position carries meaning (`views/semantic/SemanticView.tsx:211-218`).
Per-edge DOM overlays scale linearly against a view built for large graphs
(`views/systems/SystemsView.tsx:244-253`, `:277-310`). Theme tokens are duplicated as
hard-coded hex fallbacks across four modules with nothing verifying they still match
(`views/explorer/buildGraph.ts:63-69`). The impure Sigma edge of the pure/impure split is
entirely untested (`src/lib/dragNodes.ts:75-161`). Seven copies of the artifact-building
setup re-implement the Python renderer in TypeScript
(`tapestry/e2e/smoke.spec.ts:17-22` and six duplicates). And a dependency bump inside the
caret ranges can silently rewrite a committed Python-package artifact
(`tapestry/package.json:13-37`, `emit-template.mjs:8`).

### 2.19 The test suite — `tests/` (six groups)

**What it is.** The executable specification. It carries the shared infrastructure — a
namespaced live-FalkorDB fixture chain and one shared doubles module — plus the
behavioural contracts of every layer.

**Group 1 — infrastructure and the outer layers.** Every live-store test is namespaced and
leaves the store as it found it (`tests/conftest.py:35-45`). Documented CLI invocations
are harvested from the repository's own agent documentation and validated against the live
input models, with an anti-vacuity guard
(`tests/test_claude_examples_contract.py:146-160`, `:136-138`). Config resolution is one
loader with one precedence chain (`tests/test_config.py:51-76`). Error codes come from the
typed exception hierarchy, never from prose matching (`tests/test_cli_io.py:67-85`). Every
command is declared through the single construction path with an explicit stdin stance
(`tests/test_cli_registry.py:19-27`, `:30-46`). A composite section never throws
(`tests/test_composites_framework.py:28-41`).

**Group 2 — comprehension and proposal surfaces.** A truncated answer must balance: shown
plus cut equals total (`tests/test_consumption.py:267-271`,
`tests/test_consumption_budget.py:101-106`). Budget pressure degrades breadth evenly and
never cuts the queried entity (`tests/test_consumption.py:273-289`). Retired entities
leave every consumption read (`:158-171`). A suppressed hub means the answer is incomplete
and must say so (`:522-530`). The dedup gate asks the vector index rather than scanning
(`tests/test_dedup_gate_search.py:52-73`). A doc link is only made when the document
states or unambiguously writes its target (`tests/test_extraction_doclinks.py:59-79`,
`:98-217`).

**Group 3 — extraction and the store.** Structural extraction never emits an untyped
association (`tests/test_extraction_resolution.py:451-463`). No extracted edge points at
an entity the extraction does not also create (`:481-499`). An ambiguous symbol produces
no edge at all (`:205-241`, `:398-431`). Retirement is bi-temporal close-out, and version
intervals partition system time (`tests/test_falkor_store.py:602-636`, `:665-691`).
`update-codebase` supersedes structural symbols and leaves the written layer alone
(`tests/test_incremental_update.py:285-352`). The shrink guard refuses a collapsing update
and judges scope by git visibility (`:571-606`). Every store mutation appends exactly one
typed event, in order (`tests/test_falkor_store.py:498-543`).

**Group 4 — the write path and the domain model.** Polarity belongs to causal relation
types only, at every write seam (`tests/test_ops_relations.py:79-161`). Deletion retracts;
the record and its history survive (`tests/test_ops_entity.py:138-150`). A retracted entity
cannot be a relation endpoint, at either arity or direction
(`tests/test_ops_relations.py:184-201`, `:258-279`). `merge-entities` is idempotent,
atomic, and emits exactly one event (`tests/test_ops_merge.py:279-322`). Bulk import is
idempotent by composite key and reports per-item errors without failing the batch
(`tests/test_ops_bulk.py:78-101`). Errors are classified by exception class, never by
message text (`tests/test_ops_documents.py:119-126`). The wire format is
exclude-unset-by-alias: set nulls survive, unset optionals disappear
(`tests/test_model.py:231-264`).

**Group 5 — the storage contract and vector search.** A mutation and its event append are
one unit, fault-injected at four distinct seams
(`tests/test_store_atomicity.py:92-133`). An unrepairable log gap is a typed error naming
the missing events (`:384-416`). Server-side pushdown must be observationally identical to
the Python filter oracle across a 26-case matrix
(`tests/test_store_pushdown.py:219-232`). Every read-port adapter answers the same way,
including bi-temporally (`tests/test_read_port.py:71-89`, `:458-511`). Every search
surface reports the same score scale (`tests/test_semantic_search_core.py:36-49`), and the
approximate candidate window must be grown, not trusted (`:68-86`). SSRF rejection covers
IPv4-mapped IPv6 and CGNAT before any connection (`tests/test_ssrf.py:16-34`).

**Group 6 — the visualization pipeline and three leaf subsystems.** An `asOf` bound
reconstructs the graph as it stood, including edges retired since
(`tests/test_viz_asof.py:18-34`); analytics and semantic sections are never recomputed
as-of and self-label their temporal scope (`:107-129`). Truncation keeps the
highest-degree core and is reproducible (`tests/test_viz_bundle.py:57-110`). Optional
sections are omitted, never emitted empty (`:36-38`). The committed JSON Schema and dev
fixture must equal what the model emits (`tests/test_viz_schema_drift.py:21-26`). Typed
error codes surface as fixed HTTP statuses with the code in the body
(`tests/test_viz_serve.py:32-37`). `record-outcome` is all-or-nothing and never triggers
embedding (`tests/test_work_memory.py:184-193`, `:159-160`); citation weight decays by an
exact half-life (`:262-293`).

**Where the suite strains.** Pure-unit and live-FalkorDB tests share one unmarked suite
(`tests/conftest.py:24-26`), so nothing can run without Docker and git
(`tests/test_falkor_store.py:35-37`). Bi-temporal ordering is established with real sleeps
across at least four modules (`tests/test_read_port.py:458-476`,
`tests/test_ops_merge.py:396-403`, `tests/test_viz_asof.py:23-121`), and two modules assert
wall-clock durations in a project whose CI forbids performance gates
(`tests/test_enrichment_crawl.py:345-370`, `tests/test_gap_fill_cycle.py:111-123`). Exact
golden counts make one fixture repository a shared bottleneck
(`tests/test_extraction_units.py:473-493`). A shared doubles module exists, yet several
modules still write their own (`tests/test_entity_proposer_foundation.py:99-106`), and
nine copies of the same entity/relation builders live across the write-path group
(`tests/test_ops_relations.py:54-71`). Tests reach past the public surface into private
internals (`tests/test_viz_serve.py:32-37`, `tests/test_cli_registry.py:30-46`). The HTTP
surface contributes zero coverage on the default install path
(`tests/test_viz_serve.py:1-10`), the UMAP projection path is never exercised in CI
(`tests/test_viz_semantic.py:95-96`), and the served-template drift guard skips in exactly
the checkouts most likely to be stale (`tests/test_viz_html.py:42-53`).

**The fixtures.** `tests/fixtures/` holds two unrelated families: verbatim graph-snapshot
seed folders whose byte-identity *is* the serialization contract
(`tests/fixtures/small/default.json:57-65`), and a seven-file miniature polyglot project
that is the ground truth for extraction. In that project every cross-file resolution idiom
appears exactly once (`tests/fixtures/repo/src/service.py:3`,
`tests/fixtures/repo/lib/index.ts:1`), one symbol is defined twice on purpose so no
documentation mention of it may resolve (`lib/index.ts:22`, `lib/helper.js:5`), one
glossary must contribute zero relations (`docs/glossary.md:3-14`), and one module is a
deliberate orphan (`src/policy.py:3`). The tension the fixture carries is inherent: it is
both a growable negative-case corpus and a frozen count baseline
(`tests/fixtures/repo/lib/index.ts:20-24`).

### 2.20 Repository control plane and design records

**What it is.** The declaration surface — the files that state what The Loom is, what it
is built from, how it is run and gated, what its words mean, and what was designed before
it was written. None of them is imported by the package. `pyproject.toml` is the single
manifest: the runtime dependency set, the two console entry points, and the configuration
for all three quality tools. `docker-compose.yml` declares the one FalkorDB service the
first architecture invariant depends on. `CLAUDE.md`, `CONTRIBUTING.md`, `README.md`,
`STACK.md` and `COMMANDS.md` are the documentation tier; `CONTEXT.md` is the project
glossary. `uv.lock` is the resolved, hash-pinned closure that turns the loose floors in
`pyproject.toml` into an exact install.

**What must stay true.**

- `COMMANDS.md` is generated from the registry and a test fails when it drifts —
  `COMMANDS.md:3`, `:5`; `CLAUDE.md:49`, `:108-109`.
- The green-main gate is four commands across three CI jobs, and format-check is one of
  them — `CONTRIBUTING.md:29-31`, `:36-39`.
- FalkorDB persists to `/var/lib/falkordb/data`, with the incident that produced the mount
  recorded inline — `docker-compose.yml:9-13`.
- `scripts/` is outside both the type gate and the test suite —
  `pyproject.toml:67-70`, `:76-78`.
- Dev seed scripts cannot write to the caller's default graph; the target graph is bound
  at compile time — `scripts/gen_bench_graph.py:65-68`,
  `scripts/seed_live_dev.py:24-27`.
- The benchmark generator seeds no embeddings — `scripts/gen_bench_graph.py:15-18`,
  `:83-94`.
- Every locked artifact is digest-pinned to a single index —
  `uv.lock:26-27`, `:4214`.

**Where it strains.** The command count is hand-copied into two documents while the
catalog it summarizes is generated (`CLAUDE.md:8`, `README.md:12` against `COMMANDS.md:5`).
The repo layout is described three times and the copies have already drifted
(`CLAUDE.md:56-74`, `README.md:356-370`, `CONTRIBUTING.md:110-118`). An ISC-licensed
project's only supported store is source-available under SSPL (`pyproject.toml:7`,
`STACK.md:22-26`). The glossary bans the word four commands are named after
(`CONTEXT.md:100-104`) and is unreachable from every entry-point document
(`CONTEXT.md:1-6`). In the lockfile, the document-AI stack is non-optional — a default
sync installs Torch, Transformers and ONNX Runtime (`uv.lock:692-697`, `:739-755`); Python
3.14+ resolves the UMAP path onto 2021-era source-only builds (`uv.lock:2100-2119`,
`:1455-1466`); the tree-sitter grammar set has two independent owners
(`uv.lock:4232-4237`, `:726-734`); and the solver is declared twice with divergent floors
(`uv.lock:4284`, `:4294`).

**The design records.** `docs/superpowers/` holds matched pairs — an approved design spec
that fixes the contract, and an executable plan that turns it into numbered tasks with
literal file contents, gate lists and commit pathspecs. The five Tapestry phase plans and
the map-codebase pair are the two most recent efforts. Their recurring discipline is
failing-test-first with a per-task gate set, a constraint ledger carried forward verbatim
across phases, and plans that carry artifacts verbatim rather than describing them. Their
recurring strain is staleness: plans pin repo facts the repo keeps moving
(`2026-07-11-tapestry-phase-5.md:194-196` against `CLAUDE.md:8`), later phases correct
earlier phases by listing the errors while leaving the earlier document wrong on disk
(`2026-07-11-tapestry-phase-3.md` against `-phase-2.md:435-437`), shipped features still
carry pre-implementation status, and no plan is ever marked superseded. One spec/plan pair
disagrees outright on whether a full re-run always supersedes
(`2026-08-03-map-codebase-design.md:100-105` versus `2026-08-03-map-codebase.md:189-190`).

---

## 3. Load-bearing modules

Ranked by how many other things touch them (degree) and by how much traffic passes
*through* them (betweenness).

### Most connected

| # | Module | Why it is a hub |
|---|---|---|
| 1 | `theloom/store/falkor.py` | 202 direct connections — 62 functions and methods defined in it, 54 files on either side of an import, and 75 written notes attached to it. Every entity and relation row, and what it means, is defined here. |
| 2 | `CommandInput` (`theloom/operations/common.py`) | 158 connections, 156 of them the command input models that derive from it. Its "explicitly set versus absent" semantics are load-bearing for the entire CLI surface. |
| 3 | `typing` (stdlib) | Imported nearly everywhere; an artifact of a fully annotated codebase under `mypy --strict`, not an architectural fact. |
| 4 | `theloom/model.py` | 152 connections, 86 of them files. It imports almost nothing and is imported by most of the package — the purest sink, exactly as the "single source of truth" invariant intends. |
| 5 | `theloom/store/multigraph.py` | 145 connections, 116 of them files. The facade every command receives as its second argument. |
| 6 | `tapestry/src/views/explorer/Explorer.tsx` | 132 connections, 92 of them local bindings. The largest single component in the repository and the front end's primary surface. |
| 7 | `tapestry/src/views/chronicle/Chronicle.tsx` | The bi-temporal replay view; wide because it wires a pure replay engine into Sigma reducers, a scrubber, an event rail and a diff mode. |
| 8 | `tapestry/src/views/systems/SystemsView.tsx` | The causal-loop view, with polarity glyph overlays, loop isolation and flow animation. |
| 9 | `tapestry/src/views/semantic/SemanticView.tsx` | The embedding scatter with cluster hulls and lasso brushing (710 lines). |
| 10 | `tests/test_entity_proposer_foundation.py` | The widest single test module — it stands up a complete in-memory store double and hand-computes proposal scores. |
| 11 | `theloom/operations/semantic.py` | 108 connections; 965 lines. The single funnel through which every retrieval and embedding command passes. |
| 12 | `theloom/extraction/treesitter.py` | 92 connections; 1,387 lines. The whole parser and its public API in one module. |
| 13 | `theloom/cli/registry.py` | 96 connections. 164 command descriptors in one file — the declarative source of the entire CLI. |
| 14 | `tests/test_falkor_store.py` | 793 lines pinning store CRUD, status lifecycle, the event log, bi-temporal reads and vector-index readiness. |
| 15 | `theloom/operations/analysis.py` | The 16 traversal and analytics commands in one module. |

### Most traffic passes through

`theloom/store/multigraph.py` and `theloom/store/falkor.py` top betweenness by a wide
margin — every path from a command to data runs through the pair, which is precisely what
the "one transactional store" invariant asks for. `theloom/cli/registry.py` is third:
everything above the store enters through it. Then `theloom/viz/bundle.py` — the single
assembler behind all three visualization transports, so every visualization path narrows
to it. `theloom/operations/semantic.py` and `theloom/operations/analysis.py` follow as the
two widest command modules, then `theloom/config.py` and `theloom/semantic/embed.py` —
chokepoints by design: one config path, one embedding contract. `theloom/model.py` and
`tapestry/src/App.tsx` close the list.

Five of the top fifteen betweenness entries are documents — this map, `CLAUDE.md`,
`README.md`, and two Tapestry phase plans. They score high because documentation-to-code
links make prose a genuine bridge between otherwise separate parts of the repository. That
is a property of the map, not of the runtime.

---

## 4. Dependency cycles

Fifteen cycles exist. Eleven are single-function self-references — recursion, which is
normal — and four are multi-node. Notably, the previously reported *second* recursive
Weisfeiler-Leman hash inside `theloom/operations/reification.py` is gone: only the shared
implementation in `theloom/reification/fingerprint.py` remains.

| Members | Verdict | Reason |
|---|---|---|
| `theloom/store/falkor.py` ↔ `theloom/store/read_port.py` | **intentional** | The read port declares the narrow typed read surface and names both concrete adapters; the adapters name the port back for typing. Its docstring states exactly why it exists: so read-only consumers stop naming the concrete store or, worse, `Any`. |
| `theloom/store/read_port.py` ↔ `theloom/store/memory.py` | **intentional** | The same protocol/adapter pairing on the in-memory side. Worth noting that the port imports *both* adapters, so the module cannot be loaded without both. |
| `CLAUDE.md` → `README.md` → `docs/superpowers/plans/2026-07-11-tapestry-phase-5.md` → `CLAUDE.md` | **intentional** | Prose cross-references harvested by documentation-link extraction, not a build dependency. Nothing loads at runtime. |
| `CLAUDE.md` → `README.md` → `CONTRIBUTING.md` → `docs/architecture/ARCHITECTURE-MAP.md` → `CLAUDE.md` | **intentional** | The same, and this document is one of its members: `CLAUDE.md` points at the map and the map points back. |
| `_hash_at_depth` (`theloom/reification/fingerprint.py`) | **intentional** | Recursive Weisfeiler-Leman neighborhood hashing — now the single copy, consumed by `reify-patterns`, the entity proposer and component signatures. |
| `_extract_calls`, `_find_identifier`, `_comment_notes`, `_extract_require_calls`, `_string_literal_vocabulary` (`theloom/extraction/treesitter.py`) | **intentional** | Five recursive tree-walkers over parse trees; recursion is the natural shape. |
| `_jsonify` (`theloom/cli/io.py`) | **intentional** | Recursive JSON coercion that also enforces the non-finite-float invariant. |
| `_generic_json_to_blocks` (`theloom/documents/parsers.py`) | **intentional** | Recursive descent over nested JSON. |
| `_js_string` (`theloom/synthesis/prompts.py`) | **intentional** | Recursive serialization for prompt construction. |
| `_resolve_references` (`theloom/symbolic/core.py`) | **intentional** | Recursive `$reference` substitution in the chain interpreter. |
| `_substitute` (`tests/test_claude_examples_contract.py`) | **intentional** | Recursive placeholder substitution in the documentation-example harvester. |

The recursion cluster is individually benign but collectively worth one note: two recorded
strains flag unguarded Python recursion on input-controlled depth — `theloom/graph` uses
recursive DFS in half its modules with no depth guard (`analytics.py:119-142`,
`cycles.py:38-50`), and `theloom/verification/checks.py:184-210` recurses on graph depth.
Neither is in the tree-walker set above, but they are the same hazard class.

There is **no import cycle among the Python packages themselves at the file level**. The
two layering inversions the previous map recorded — `theloom/analysis` importing
`theloom/operations`, and `theloom/verification` reaching up into
`theloom/operations/verification.py` — have both been resolved: the analysis package now
imports the shared fingerprint kernel (`component_signatures.py:30-32`) and the shared
capability generators now live in the verification package with the placement rule stated
in prose (`theloom/verification/metrics.py:1-9`). One upward arrow remains, recorded as a
strain rather than a cycle: `theloom/synthesis/cegis.py` imports a model class from
`theloom/operations/verification.py` (`verification.py:67-75`).

---

## 5. Communities vs. directories

The clustering pass sampled 500 of 6,222 records, so it reads the neighbourhood structure
rather than the whole map. What it found is worth reporting precisely because of how
*little* it disagrees with the folder structure: eleven clusters, none larger than six
members, and every code cluster confined to a single file or a single view directory.

- **Six clusters are same-file local bindings** — Explorer, SystemsView, SemanticView,
  Chronicle, EventList, `buildGraph`. Cohesion inside a module is high enough that
  similarity finds nothing to say across module boundaries.
- **One cluster is three sibling stylesheets** — the Explorer, Overview and Systems view
  CSS files, which share a token vocabulary and a card idiom. Already siblings on disk.
- **Two clusters cross a directory boundary and both are structural coincidences** — the
  two empty package initialisers (`theloom/cli/__init__.py` and
  `theloom/composites/__init__.py`, both 0 bytes, both marking packages that deliberately
  export nothing), and the two functions named `traverse_synthesis` — the operations-layer
  adapter and the engine function it delegates to.
- **Two clusters are in the written layer, not the code** — a pair of matched tests in the
  work-memory suite, and, most tellingly, two invariant statements about the same live
  region in the Tapestry shell, written in two different enrichment passes under two
  different group labels.

**What this suggests about the real seams.** The directory tree is an honest
representation of this codebase's modularity; the clustering finds no hidden cross-cutting
concern that the folders conceal. Connectivity says the same thing: of five disconnected
islands, one holds 6,204 of the 6,222 records, one holds `uv.lock` and the fifteen notes
written about it, and three are single files (`tapestry/package-lock.json` and two
view stylesheets) that nothing links to and that link to nothing.

The genuine seam this pass reveals is in the map's own written layer rather than in the
code: the module-group partition changed between runs, so a handful of areas — the repo
root, and the Tapestry source tree — carry two overlapping descriptions written under two
different group labels. That is the source of the top open seam in §7 and is a
housekeeping matter for the next full re-run, not a property of the codebase.

---

## 6. Risks & tensions

274 strains are recorded. The ones below are the ones a reviewer should see first, ordered
by how much they threaten a stated architecture invariant.

1. **Auto-apply's write cycle spans five mutations with only partial compensation.**
   `self-improve` writes an entity, a relation batch, credit, a procedure entity and
   observations in separate store calls, against the one-atomic-mutation invariant. Only
   the relation batch has a compensating hard delete.
   `theloom/composites/self_improve.py:362-385`, `:441-466`, `:468-489`, `:492-544`.

2. **`MULTI` is not a rollback boundary, so multi-statement commits owe a debt.** The
   commit primitive guarantees atomicity for one statement plus its event append; anything
   larger is compensated after the fact, and a committed mutation whose event repair fails
   leaves the log short or out of order — an explicit non-promise.
   `theloom/store/space.py:59-101`, `theloom/store/commit.py:37-43`, `:157-170`.

3. **Hard-delete escape hatches inside an event-sourced store.** Present in relations,
   inference, extraction rollback, work memory and the store itself, and covered by tests,
   so "history is real" has documented exceptions.
   `theloom/operations/relations.py:377-396`, `theloom/operations/inference.py:235-237`,
   `theloom/operations/work_memory.py:166-169`, `theloom/store/falkor.py:439-500`.

4. **The derived read index duplicates filter semantics kept in sync by hand.** The
   server-side prefilter must remain a superset of the Python oracle; the only thing
   holding them together is a docstring that says "mirrors `filters.py` exactly" and a
   26-case equivalence test. `theloom/store/falkor.py:101-114`, `:132-151` against
   `theloom/store/filters.py:69-100`; the test is
   `tests/test_store_pushdown.py:219-232`.

5. **The live visualization server is unauthenticated and its bind host is
   caller-supplied.** No middleware, no auth, and a host parameter that reaches
   `uvicorn` directly — safe on loopback, exposed anywhere else.
   `theloom/viz/serve.py:43-48`, `:93`, `:250`.

6. **The SSRF guard resolves DNS separately from the connection it protects.** A
   documented residual TOCTOU window on every outbound fetch; the resolved address list is
   discarded rather than pinned. `theloom/documents/ssrf.py:7-9`, `:74-80`.

7. **Symbolic evaluation sympifies caller-controlled strings.** The final parse fallback
   is a bare `sympify`, and the differential-equation handler sympifies both sides with an
   injected namespace — a trusted-caller assumption inside a surface that takes arbitrary
   JSON. `theloom/symbolic/core.py:48`, `:772-776`.

8. **Soft-fail envelopes bypass the exit-code half of the error contract.**
   `solve-problem` and the symbolic commands always return an envelope and never raise, so
   their failures carry a typed code in the body but exit 0.
   `theloom/operations/solve.py:8-17`, `:382-387`; `theloom/operations/symbolic.py:3-7`.

9. **The bundle's `asOf` bound is partial.** Entities, relations and events honour it;
   analytics and the semantic projection stay at the present, so a historical
   visualization shows historical nodes with present-day centrality. The bundle now labels
   this rather than hiding it, and both consuming views say so on screen.
   `theloom/viz/bundle.py:121-135`, `theloom/viz/analytics.py:56-59`.

10. **The read port's prose still describes relation updates as overwrite-in-place.** The
    code now snapshots edges into closed version nodes; the documentation of the read
    surface has not caught up, which is exactly the kind of drift that re-introduces the
    defect. `theloom/store/read_port.py:106-109` against
    `theloom/store/falkor.py:929-963`.

11. **One upward import still points from an engine into the command layer.**
    `theloom/synthesis/cegis.py` imports a model class from
    `theloom/operations/verification.py`, the last remnant of a layering inversion whose
    other two instances are now fixed. `theloom/operations/verification.py:67-75`.

12. **Only two of the five capability checks were deduplicated; three were copied.** The
    shared generators moved into the verification package, but three checks kept their
    inline copies, and the capability-name format string is now duplicated on both sides
    of the deduplicated pair. `theloom/verification/capability_spec.py:8-12`, `:60-70`,
    `:107-145`; `:78` versus `theloom/verification/metrics.py:25`.

13. **Mismatched vector widths raise in one comparator and score zero in its neighbour.**
    The shared cosine helper was deliberately changed to score incomparable vectors zero;
    the component-signature comparator that imports it still raises on the same input.
    `theloom/analysis/component_signatures.py:33`, `:141-142` against
    `theloom/semantic/embed.py:121-135`.

14. **As-of reads reconstruct the past by scanning the whole present.** Every bi-temporal
    read walks all entities and all edges plus every covering version node — correct, and
    linear in the size of the graph rather than in the size of the answer.
    `theloom/store/falkor.py:320-393`.

15. **Exports are called WYSIWYG and are not.** Neither the PNG nor the SVG path captures
    DOM-overlay decoration, so polarity glyphs, leverage badges and cluster hulls — the
    channels the Systems and Semantic views are read through — are absent from every
    exported image. `tapestry/src/lib/exportSvg.ts:24-30`,
    `tapestry/src/views/systems/SystemsView.tsx:439-442`,
    `tapestry/src/views/semantic/SemanticView.tsx:400-405`.

Two risks the previous edition of this map ranked in the top three are now closed and are
recorded here so the delta is visible: relation updates snapshot into closed version nodes
exactly as entity updates do (`theloom/store/falkor.py:929-963`), and a bundle-load failure
in the front end now raises a typed error naming the failing branch instead of stranding
the app on the loading gate (`tapestry/src/lib/data.ts:82-101`).

---

## 7. Open seams

Areas that read as similar but are not connected. The pass compared 500 records; the
twenty closest unconnected pairs are below, grouped by what they mean.

**A map-layer duplicate, and the highest-scoring pair overall (0.80).** Two purpose
descriptions of the repository root exist under two different group labels — one written
when the group was called `root-1`, one when the partition renamed it `repo-root-1`. The
second was created with a duplicate-name warning already attached. Nothing in the codebase
is wrong here; the map is describing the same territory twice. The next full re-run should
supersede the older copy.

**Sibling functions that differ only by arity or by scope.** The single-item and batch
embedding entry points (`embed_entity` / `embed_entities`, 0.80); the single-relation and
multi-relation reads on the store base class (`read_relation` / `read_relations`, 0.79);
the public ingest entry point and the private implementation beneath it
(`ingest_content` / `_ingest`, 0.79); the two document-listing helpers in the entity
proposer (0.76). These are honest pairs, but each is a place where a future change must be
made twice.

**Constants that differ by one character.** `DEFAULT_TRANSFER_PRIOR` and
`DEFAULT_TRANSFER_PRIORS` in the same module (0.77), and `MAX_DEPTH_LIMIT` beside
`DEFAULT_MAX_DEPTH` in the component-signature module (0.76). The first pair is a genuine
readability hazard.

**A type and its instance sharing a name up to case.** `Facets` / `facets` in the filter
panel (0.76) and `Bounds` / `bounds` in the minimap (0.75) — a TypeScript idiom, listed
for completeness.

**A pattern and a claim saying the same thing.** "Mode detection by parsed shape, never by
sentinel literal" was recorded once as a convention and once as an invariant, in two
different passes over the same front-end module (0.75). The same duplication produced the
two live-region statements that show up as a cluster in §5.

**Matched test pairs.** Seven of the twenty are deliberately symmetric tests — env-versus-
flag config precedence, entity-versus-relation pushdown oracles, open-versus-answered
question scoping, include-versus-omit truncation blocks. They are supposed to look alike.
Their presence near the top of the list is a good sign about the suite's shape.

---

## 8. Coverage & methodology

**Coverage.** This was an incremental run. Twenty module groups were re-read and re-written
at commit `5b60a8b`:

`repo root (part 1/2)`, `tapestry (part 2/2)`, `tapestry/src`, `tapestry/src/lib`,
`tapestry/src/views/overview`, `tapestry/src/views/semantic`,
`tapestry/src/views/systems`, `tests (part 1/6)`, `tests (part 3/6)`,
`tests (part 4/6)`, `tests (part 5/6)`, `tests (part 6/6)`, `theloom/analysis`,
`theloom/composites (part 2/2)`, `theloom/operations (part 2/3)`,
`theloom/operations (part 3/3)`, `theloom/reification`, `theloom/store`,
`theloom/verification`, `theloom/viz`.

**No group failed enrichment.** Every group scheduled for this run produced a purpose,
patterns, invariants and strains.

The remaining 28 module groups in the map carry written material from earlier runs at
earlier commits. They are still described above, and their anchors were correct when
written, but they have not been re-read against this commit: `docs (parts 1-4)`,
`repo root (part 2/2)`, `tapestry (part 1/2)`, `tapestry/e2e`,
`tapestry/src (parts 1-4)`, `tests (parts 1-4 of the older four-way split)`,
`tests/fixtures`, `tests/fixtures/repo`, `tests/fixtures/repo/src`, `theloom`,
`theloom/algebra`, `theloom/cli`, `theloom/composites (part 1/2)`, `theloom/documents`,
`theloom/exploration`, `theloom/extraction`, `theloom/graph`,
`theloom/operations (part 1/3)`, `theloom/semantic`, `theloom/symbolic`,
`theloom/synthesis`. Where the partition was renamed between runs, both the old and the new
description survive — see §7.

**Not parsed.** 42 files. These are the formats tree-sitter has no grammar for here:
Markdown, JSON, CSS, YAML, TOML and the lockfile. They appear as files with their
documentation links intact; only their internal symbols are missing. Go and Rust files
would parse to a file record and nothing else, but this repository contains none.

**Provenance.** Everything above is a view over the graph `codebase-the-loom`, built by
`/map-codebase` at commit `5b60a8b78c90634aeb8f99639acba9549bd3f9e5`. The structural layer
(files, symbols, calls, imports, containment) is extracted deterministically by
tree-sitter. The written layer (purposes, patterns, invariants and strains) was written per
module group and each item carries a file-and-line anchor, reproduced above. The rankings
in §3 are reported as measured; the `typing` row is flagged rather than removed, because
removing it would misrepresent what the measurement says.

**How to re-run.** `/map-codebase /Users/jameswinans/Dropbox/Development/the-loom`. The run
reads `docs/architecture/map-manifest.json`, takes its `commit` as the baseline, and
re-enriches only the groups whose files changed since. A full re-run rewrites every group
and is the way to clear the duplicate-partition artifacts noted in §5 and §7. FalkorDB must
be up: `docker compose up -d falkordb`.

**How to interrogate the graph afterwards.** The recipe sheet is
[`QUERYING.md`](QUERYING.md). The short version: `loom explore` for a symbol's definition
plus its callers, callees and imports in one call; `loom find-callers` / `loom find-callees`
for anchored call sites; `loom blast-radius` for what breaks if you change something;
`loom entity-deep-dive` for everything attached to one record; `loom hybrid-search` for
meaning rather than names; and `loom list-entities` filtered to `claim`, `tension` or
`pattern` for the written layer of any module. The interactive view of the same graph is
`codebase-map.html` beside this file (generated, gitignored).
