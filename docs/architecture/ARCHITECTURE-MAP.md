---
repo: the-loom
commit: e4a12a1b188e5391ec431a8c5754d2fa4733b1f9
graph: codebase-the-loom
generated: 2026-08-06
mode: incremental
---

# The Loom — Architecture Map

## 1. Executive overview

The Loom is a knowledge-graph substrate with exactly one user-facing surface: a
JSON-in / JSON-out command line of 164 commands across 23 categories, all of them
generated from a single declarative registry. Underneath that registry sits a thin
command-operation layer that validates input, resolves which named graph to talk to,
and delegates: to a FalkorDB-backed store that keeps graph topology, entity vectors,
document chunks and an append-only event log in one transactional place; to a set of
deliberately pure computation libraries (graph algebra, semiring traversal, symbolic
math, computational-creativity scorers, exploration signals, verification predicates);
and to a semantic layer that owns the single definition of "nearest". Above the
commands sit composites — one-call bundles that chain many operations into a single
structured answer with per-section fault isolation. A separate visualization arm turns
a live graph into a bounded, versioned payload and ships it three ways: raw JSON, a
self-contained HTML page carrying a committed React/sigma.js build, or a read-only
REST service that the same page talks to in live mode. The shape that recurs
everywhere is *pure core, thin shell*: the algorithm has no store and no I/O, and the
module above it is a translation layer that shapes wire documents and typed errors.

### Stats

| Measure | Value |
|---|---|
| Source files in the map | 368 |
| External packages referenced | 65 |
| Symbols (classes, functions, methods, variables) | 4,643 |
| Written observations about the code (purposes, patterns, invariants, tensions) | 997 |
| Records in the map, current state | 6,028 |
| Records including superseded revisions | 6,717 |
| Connections between them | 14,203 |
| Files not parsed | 42 |

Connections break down as: containment 5,255; calls 3,530; general association 3,342;
dependency 1,646; document-to-code references 205; type/instance links 199; supersession
26.

Language mix, by file: Python 249, TypeScript 73, Markdown 18, JSON 14, CSS 9,
JavaScript 2, YAML 1, TOML 1, lockfile 1.

> **Working tree was dirty at extraction time.** `CLAUDE.md` had uncommitted edits and
> `CONTEXT.md` was untracked when this map was built. Everything below is pinned to
> commit `e4a12a1`; the two files above may differ on disk from what is described here.

The 42 unparsed files are the formats tree-sitter has no grammar for in this
configuration — Markdown, JSON, CSS, YAML, TOML and the lockfile. They still appear as
files with their documentation links intact; only their internal symbols are absent.

---

## 2. Subsystem walkthrough

### 2.1 The core package — `theloom/`

**Purpose.** The root package holds the cross-cutting contracts every other subsystem
depends on and which depend on almost nothing themselves. `model.py` is the single
source of truth for the domain: every enum in a stable wire order, the entity and
relation shapes, confidence and provenance, the paired create-schemas, the filter
shapes, and the five-state status lifecycle table. `errors.py` defines six structured
error codes as a typed exception hierarchy. `config.py` is the one configuration
resolution path. `timeutil.py` fixes the canonical timestamp shape, and `migrate.py`
loads an exported graph folder back into the store.

**Key files.** `theloom/model.py` (~575 lines), `theloom/config.py`,
`theloom/errors.py`, `theloom/timeutil.py`, `theloom/migrate.py`.

**How it is built.** Python attributes are snake_case and wire names are camelCase,
translated by Pydantic field aliases rather than by hand. Enums are the stable wire
contract and runtime inventories are derived from them. The exception hierarchy carries
its own error code, so nothing downstream ever has to guess. Configuration resolves in
one pass through layered overrides. Domain shapes and their `*Input` twins are kept
separate so "stored" and "supplied" never blur. Retired enum members are coerced by a
validator rather than breaking old data.

**What must stay true.**

- Every wire timestamp is ISO 8601 UTC with a `Z` suffix — `theloom/model.py:38-49`.
- Confidence is bounded to `[0.0, 1.0]` at both the field and the label boundary —
  `theloom/model.py:370`, `:475`.
- `durability: volatile` requires `expiresAt` — `theloom/model.py:433-438`.
- The five-state lifecycle: retracted is terminal, and only *investigating* returns to
  active — `theloom/model.py:314-338`.
- Unknown fields are rejected; every wire model forbids extras —
  `theloom/model.py:361-364`.
- Errors carry their structured code from birth; the CLI never classifies by message
  text — `theloom/errors.py:12-19`, `:25`.
- Configuration resolves once, flags > environment > file > defaults —
  `theloom/config.py:150-219`.
- Snapshot import is idempotent because it wipes the prefix first —
  `theloom/migrate.py:34-36`.

**Where it strains.** Config file handling is fail-open on a parse error but fail-loud
on a field error (`theloom/config.py:125-147` vs `:114-122`). The requiredness of
relation fields is split between the model and the command layer
(`theloom/model.py:453-459`, `:524-528`). The model enforces two invariants at the type
level but only *advises* on the lifecycle (`:433-438` vs `:341-353`). Verbatim snapshot
import deliberately bypasses the event log (`theloom/migrate.py:50-58`). A
process-global embedder test seam lives inside the otherwise-pure config module
(`theloom/config.py:285-310`).

---

### 2.2 Persistence — `theloom/store/`

**Purpose.** The place where the two hardest architecture invariants stop being prose
and become code. It maps the domain model onto one FalkorDB instance so topology,
vectors and the append-only event log share a single transactional store, and it makes
every mutation event-sourced and bi-temporal: a write is one Cypher statement plus its
event append inside one Redis `MULTI`/`EXEC`; an update snapshots the prior incarnation
as a version node instead of overwriting it; a delete invalidates unless the caller
explicitly asks for erasure.

**Key files.** `theloom/store/falkor.py` (70 symbols, the widest file in the package),
`theloom/store/space.py`, `theloom/store/commit.py`, `theloom/store/read_port.py`,
`theloom/store/multigraph.py`, `theloom/store/bridges.py`.

**How it is built.** A shared `GraphSpace` chassis gives the entity store and the chunk
store the same handle, event log, commit primitive, paged read and vector index. One
commit primitive owns the write, with compensation in both directions. Writes snapshot
into version nodes. A derived read index prefilters and Python confirms, so the
pushdown may only ever be a superset of the true filter. Index migration happens lazily,
triggered by a read. Every full-scan read is wrapped in SKIP/LIMIT paging. A narrow,
typed read port sits alongside a second, non-throwaway in-memory adapter. Guards live
*inside* the write, not in front of it.

**What must stay true.**

- A mutation and its event are committed as one unit or neither reaches the server —
  `theloom/store/commit.py:91-103`, `theloom/store/events.py:75-82`.
- Exactly one half of a committed pair can fail at `EXEC`, and each is compensated —
  `theloom/store/commit.py:105-127`.
- Updates invalidate: the prior incarnation is snapshotted, never overwritten —
  `theloom/store/falkor.py:410-427`.
- Deletion invalidates by default; `hard=True` is the only path that destroys history —
  `theloom/store/falkor.py:429-486`, `:921-973`.
- A retracted entity leaves semantic reads because its vector is dropped —
  `theloom/store/falkor.py:467-470`.
- `filters.py` is the semantics oracle; the Cypher pushdown may only be a superset —
  `theloom/store/falkor.py:154-178`, `:616-669`.
- Any full-scan read pages, or FalkorDB silently truncates it —
  `theloom/store/paging.py:1-45`, `theloom/store/space.py:113-118`.
- The vector index is write-once and sized from stored vectors, never from a query —
  `theloom/store/space.py:122-158`; a create-then-query is only correct behind an
  OPERATIONAL barrier — `:159-187`.
- Legacy bridge migration is crash-safe and never drops an undrained document —
  `theloom/store/bridges.py:230-281`.

**Where it strains.** Entity updates snapshot but relation updates overwrite in place
(`theloom/store/falkor.py:913-919` vs `:410-427`) — the sharpest inconsistency in the
layer. As-of reads reconstruct the past by scanning the whole present
(`:312-383`). Cross-graph lookup scans every graph and builds a store per graph to do
it (`theloom/store/multigraph.py:96-97`, `:132-137`). Bridges are graph records that
live outside every graph (`theloom/store/bridges.py:1-44`). There are two read surfaces
and the abstract base class is not the one consumers use
(`theloom/store/base.py:44-161` vs `theloom/store/read_port.py:1-35`). A committed
mutation whose event repair fails can leave the log short or out of order — documented
as an explicit non-promise at `theloom/store/commit.py:36-43`.

---

### 2.3 The command line — `theloom/cli/`

**Purpose.** The whole user-facing surface, containing no domain behaviour of its own.
`registry.py` declares every command once as a frozen descriptor built from a
declarative spec row; `app.py` mechanically generates one Typer subcommand per
descriptor at import time; `io.py` owns the wire protocol; `docs.py` renders
`COMMANDS.md` as a pure projection of the same descriptor list.

**Key files.** `theloom/cli/registry.py` (1,676 lines; 164 descriptors across 23
categories built by 16 category factories), `theloom/cli/app.py` (119 lines),
`theloom/cli/io.py` (85 lines), `theloom/cli/docs.py` (37 lines).

**How it is built.** Command generation is registry-driven; every spec row funnels
through one construction path; documentation is a projection of the registry, not a
parallel document; there is a single typed-error protocol boundary; heavy or optional
dependencies are imported lazily at the call site; handlers are thin adapters that
shape results.

**What must stay true.**

- Every command except `version` and `init` is generated from the registry —
  `theloom/cli/app.py:108-109`.
- Input validation happens once, in `run_handler`; Pydantic failures become
  `VALIDATION_ERROR` — `theloom/cli/registry.py:1666-1676`.
- Unknown input keys are ignored and camelCase wire names resolve to snake_case fields —
  `theloom/cli/registry.py:68`, `:76-91`.
- stdout carries exactly the result document; diagnostics go to stderr and failures exit
  1 — `theloom/cli/io.py:79-80`, `:83-84`.
- `COMMANDS.md` is byte-identical to `generate_docs()` output —
  `theloom/cli/docs.py:15-36`, pinned by `tests/test_generate_docs.py:35-40`.
- Input is JSON from the argument or stdin, must be an object, and stdin is capped at
  100 MB — `theloom/cli/io.py:17`, `:20-27`.
- Non-finite floats serialize as null, keeping output valid JSON —
  `theloom/cli/io.py:56-64`.
- Every command states its stdin stance explicitly; `allow_empty` has no default —
  `theloom/cli/registry.py:112-123`.

**Where it strains.** The store client is imported lazily but the science stack is not:
`theloom/cli/registry.py:33` pulls the analysis package in at import time. The
`raw_handler` escape hatch bypasses the typed-error boundary (`:1669-1670` vs
`:1671-1674`). Every registry command opens a store connection before its handler runs
(`theloom/cli/app.py:81-86`). Command-name uniqueness is assumed but never enforced
(`registry.py:1659`).

---

### 2.4 Command semantics — `theloom/operations/` (three groups)

**Purpose.** The seam between the CLI registry and everything below it. Each module
owns one command family and exposes one plain module-level function per command with
the uniform shape `(params: CommandInput, multi: MultiGraph) -> dict`. The layer does
five things and delegates the rest: declare the wire schema as a Pydantic input model,
resolve which graph store to talk to, add operation-level semantics the raw store does
not have, translate library exceptions into the six typed error codes, and shape the
JSON the CLI prints. No graph algorithm, no semiring math, no chunking and no Cypher is
written here.

**Part 1 — shared machinery, CRUD, traversal, consumption.** Covers the shared input
base (`common.py`), entity CRUD (`entity.py`), the semiring and adaptive-routing
commands (`algebra.py`), the 16 traversal and analytics commands (`analysis.py`), bulk
import (`bulk.py`), the agent-facing comprehension commands `explore` / `find-callers`
/ `find-callees` / `blast-radius` (`consumption.py`) with their two store-free cores,
and the global document ingest (`documents.py`).

*Invariants:* entity addressing takes exactly one of id or name, and a blank name is not
a name (`common.py:112-117`); consumption and blast-radius reads apply their own
active-status filter (`consumption.py:254-269`); explore truncation accounting is exact,
shown plus cut equals total (`consumption.py:400-416`); bulk import enforces the same
causal-only polarity partition as `create-relation` (`bulk.py:208-217`) and is
idempotent on the `name::entityType` composite key (`bulk.py:221-237`); blast-radius
counts the seed and its members as seeds, never as fallout
(`blast_radius_traversal.py:132`, `:139-144`); `update-entity` refuses illegal status
transitions (`entity.py:249-252`); `delete-entity` retracts by default
(`entity.py:323-340`).

*Strains:* three different name-resolution policies coexist in one layer
(`common.py:133-139` vs siblings); full-graph in-memory hydration is used where a
narrowed read would do (`analysis.py:60-63`); `list-entities` returns two different
response shapes depending on whether `limit` is present (`entity.py:346-355`,
`:406-412`); `previousVersionId` is written as a self-reference (`entity.py:271-272`);
document commands accept a `graph` parameter and ignore it (`documents.py:85`);
`analyze-category` clusters with all-pairs cosine over up to 10,000 chunks
(`documents.py:356-361`).

**Part 2 — relations, lifecycle, inference, extraction dispatch.** Relation CRUD and
bridge-aware neighbor reads (`relations.py`), duplicate consolidation (`merge.py`), the
17 epistemic queries plus credit propagation (`epistemic.py`), the forward-chaining
inference engine whose rules and traces are themselves graph records (`inference.py`),
Weisfeiler-Leman pattern reification and the trigger queue (`reification.py`),
extraction dispatch with run status and rollback (`extraction.py`), zero-infrastructure
export (`portability.py`), and `init`.

*Invariants:* the causal/polarity partition is an invariant of the stored edge, not just
of creation (`relations.py:308-338`); a failing strict relation batch still persists its
valid prefix (`relations.py:267-269`); `merge-entities` never hard-deletes the secondary
and a completed merge is a no-op (`merge.py:174-175`); inference-rule conclusions may
only reference variables bound by conditions (`inference.py:188-194`); derived relation
polarity comes only from the canonical defaults table (`inference.py:381`); every
inference-derived relation carries provenance back to its rule and trace
(`inference.py:391-398`); `reify-patterns` is idempotent through a fingerprint marker
observation (`reification.py:217-233`); credit propagation clamps confidence to `[0,1]`
and halts below `minDelta` (`epistemic.py:855-858`); re-extraction retires the legacy
untyped twin of every typed call edge (`extraction.py:116-155`).

*Strains:* hard-delete escape hatches exist inside an event-sourced store
(`relations.py:364-376`, `inference.py:235-237`); some error codes are classified from
message prose the handlers are written to satisfy (`inference.py:480-482`); `dryRun`
defaults disagree across mutating commands in the same group (`reification.py:158` vs
`epistemic.py:807`); the trigger queue is observable from the CLI but can never be
filled from it (`reification.py:12-15`, `:329-333`); machinery decoded from observation
strings disappears silently when malformed (`inference.py:130-134`).

**Part 3 — reasoning and assurance.** Embedding lifecycle plus the five
retrieval/discovery commands (`semantic.py`, 965 lines — the largest), symbolic adapters
(`symbolic.py`), the natural-language solver (`solve.py`), the nine Plan-Traverse-Realize
commands (`synthesis.py`), the guard/invariant/spec/AC-3/capability suite
(`verification.py`), and the write half of work memory (`work_memory.py`).

*Invariants:* one retrieval binding backs every semantic read in the group
(`semantic.py:144-165`); embedding is opt-in and content-hash idempotent
(`semantic.py:327-344`); an embedding failure is recorded on the entity, never raised
(`semantic.py:341-344`); graph-mutating discovery and repair commands default to dry run
(`semantic.py:492`); `resolve-gaps` never duplicates an existing edge in either
direction (`semantic.py:940-944`); anchor search never pays for the embedding model on a
vectorless graph (`synthesis.py:122-126`); cross-graph synthesis is read-only and
refuses to ingest (`synthesis.py:333-340`); verification reads every entity status, not
just active (`verification.py:117-123`); `validate-mutation-trace` never touches the
target graph (`verification.py:660`); `record-outcome` writes nothing on a bad citation
and cites each entity once (`work_memory.py:103-110`).

*Strains:* soft-fail envelopes in `solve.py` and `symbolic.py` bypass the typed-error
invariant (`solve.py:8-13`, `:363-364`); an unknown invariant name is a hard error in one
command and a silent skip in another (`verification.py:199-205` vs `:291-294`); a hard
delete sits inside `work_memory.py:166-169`; the engine layer reaches back up into the
operations layer (`verification.py:492-524`); `embed-entities` carries two disagreeing
entity-type vocabularies (`semantic.py:321-324`, `:371-381`).

---

### 2.5 Meaning — `theloom/semantic/`

**Purpose.** The meaning layer's engine room. It turns text into vectors, owns the
single definition of "nearest" and the single retrieval path, decides the order and
grouping of results, owns what "needs embedding" means and how a status/vector
divergence is repaired, and on top of that decides whether a proposed entity already
exists and generates entities the graph is structurally missing.

**Key files.** `search.py` (the one retrieval core), `embed.py` (the embedding
contract), `ranking.py`, `embedding_state.py`, `deduplication_gate.py`,
`entity_proposer.py`.

**How it is built.** Collaborators are sliced by Protocol rather than named as concrete
store types. The embedder override installed by config is the single injection seam,
behind a process-wide lazy singleton with a pinned model cache. The content hash of the
embedding text is the only cache key. Reconciliation is plan-then-apply. Ranking stages
are pure functions over plain rows with an injected clock. A growing candidate window
compensates for filters the vector index cannot answer.

**What must stay true.**

- Every vector is L2-normalized before it leaves the embedder — `embed.py:83-87`.
- Documents and queries are embedded with different prefixes and no caller can bypass it
  — `embed.py:28-29`, `:90-99`.
- `cosine_similarity` scores incomparable vectors `0.0` rather than raising —
  `embed.py:121-135`.
- One cosine-to-score conversion exists and hits carry the raw cosine alongside it —
  `search.py:58-65`, `:141-151`.
- Vector search returns only active entities unless a caller explicitly opts out —
  `search.py:136`, `:102-108`.
- `needs_embedding` is the single skip predicate — `embedding_state.py:49-61`.
- The proposer is read-only: it returns proposals and never writes —
  `entity_proposer.py:96-152`.
- LLM-proposed types are allowlist-validated before becoming proposals —
  `entity_proposer.py:44-61`, `:454-476`.

**Where it strains.** Step 4 of the proposal pipeline filters nothing
(`entity_proposer.py:554-576`). The LLM reasoning strategy is on by default and
unreachable in practice — the docstring admits it (`entity_proposer.py:10-13`, `:108`).
Violation semantics travel as prose and are recovered by regex (`:63-66`, `:202-215`).
The deduplication gate's two paths do not agree on what a duplicate is
(`deduplication_gate.py:117-126` vs `:167-173`) and it fabricates `active=True` for
every candidate it resolves (`:103-106`). A strict `min_score` escalates the retrieval
core to a full index scan (`search.py:132-134`, `:154-156`). Three hard-coded type lists
in the proposer shadow the domain model (`entity_proposer.py:42-61`, `:422`).

---

### 2.6 Extraction — `theloom/extraction/`

**Purpose.** The package that turns artefacts outside the graph into graph content. Its
dominant path is deterministic, LLM-free codebase extraction: tree-sitter parses each
source file into file, class, function and variable records plus containment, call,
type and dependency edges; a whole-project second pass joins the edges no single-file
parse can resolve; a third pass links Markdown documents into the code they name; an
incremental path replays a git diff by superseding rather than deleting; and a thin
driver keeps The Loom's own self-model current. A second, unrelated path does LLM
document extraction, with an append-only run record shared by both.

**Key files.** `treesitter.py` (1,387 lines, 65 symbols — the parser and public API),
`resolution.py`, `doclinks.py`, `encoding.py`, `codebasediff.py`, `selfmodel.py`,
`runstore.py`.

**How it is built.** Two-pass extraction: per-file parse, then whole-project join. One
module builds *and* parses every string the codebase graph travels through, so writers
and readers cannot drift. Updates plan the whole change, guard it, then write. A mention
becomes a documentation link only after every disqualifier fails. Resolution certainty
rides the Loom's own confidence vocabulary. Git, not the filesystem, decides what is in
the codebase.

**What must stay true.**

- An incremental update supersedes; it never deletes — `codebasediff.py:462-472`.
- A callee that does not resolve to exactly one reachable target produces no edge —
  `resolution.py:431-451`.
- A structural edge belongs to a changed file when either endpoint does —
  `codebasediff.py:298-320`.
- The structural diff never retracts an edge structural extraction did not emit —
  `codebasediff.py:78-88`.
- Extraction output is deterministic for a given tree — `treesitter.py:1203-1211`.
- Only bare-identifier calls become call edges — `treesitter.py:384-398`.
- Line numbers are 0-based in code and 1-based in the graph, and the round trip is the
  identity — `encoding.py:17-23`, `:117-134`.
- Self-model update refuses any repository that is not The Loom —
  `selfmodel.py:30-52`, `:55-62`.
- A single document contributes at most 50 references — `doclinks.py:74`, `:233-241`.

**Where it strains.** One package holds two extraction philosophies that share no code
(`pipeline.py:12-26`). Inferred edges enter a graph whose consumers treat every edge as
fact (`resolution.py:437-448`). The incremental update is incremental only in its
writes — it re-extracts the whole tree first (`codebasediff.py:517-520`). Extraction run
records live outside the graph's bi-temporal history (`runstore.py:29`, `:84`). The file
collection rule exists in two copies that must agree (`codebasediff.py:149-153`). A bare
`assert` guards a runtime precondition in a typed-error codebase (`pipeline.py:125`).

---

### 2.7 Graph algebra — `theloom/graph/`

**Purpose.** The in-memory graph layer. It hydrates wire documents into a small
insertion-ordered directed multigraph and runs the pure structural analyses on top:
centrality and components, cycle detection and feedback-loop classification, shortest
and bounded all-simple paths, frequent-subgraph mining, subgraph extraction, and the
parsers that read structured facts back out of observation strings.

**Key files.** `hydrate.py`, `analytics.py`, `cycles.py`, `paths.py`, `motifs.py`,
`subgraph.py`.

**How it is built.** Its defining constraint is determinism: enumeration order,
tie-breaking and member order are part of the observable command output, so most
algorithms are written out longhand rather than delegated to a library. Enumeration is
budgeted with truncation flags instead of failure. Observation strings act as a
structured side-channel.

**What must stay true.**

- Hydration drops dangling relations, so no edge can reference an absent node —
  `hydrate.py:118`.
- Neighbor iteration is deduplicated and order-fixed at IN-then-OUT —
  `hydrate.py:87-96`.
- Loop polarity is the parity of negative edges, with missing polarity read as positive
  — `cycles.py:258-267`.
- `classify_loop` demands a closed path of length ≥ 3 — `cycles.py:244-257`.
- PageRank converges to the stated tolerance or raises rather than returning provisional
  scores — `analytics.py:55-68`.
- Motif identity is the canonical signature; the pattern id is only a per-response
  ordinal — `motifs.py:38-41`, `:164-167`.

**Where it strains.** The library-preference invariant is in open tension with
byte-stable output ordering (`analytics.py:3-14`, `cycles.py:3-12`). A known deviation
from Johnson's algorithm is preserved deliberately for bug-compatibility
(`cycles.py:115-117`). Half the group recurses and half uses explicit stacks, with no
depth guard (`analytics.py:119-142`, `cycles.py:38-50`). The pure algorithm layer writes
to the store for loop persistence (`cycles.py:20`, `:293-334`). An untyped
`RuntimeError` escapes at `analytics.py:68`.

---

### 2.8 Weighted traversal — `theloom/algebra/`

**Purpose.** The pure computational core for weighted graph traversal: five semirings
as frozen operator records, weight extractors that turn a relation's strength label into
a semiring element, one shared DFS engine, and on top of that a type registry sorting
relation types into three algebraic categories with a table of six cross-category
morphisms, a query router, a segmented executor and a metapath engine.

**Key files.** `core.py`, `routing.py`.

**How it is built.** Semiring as frozen operator record with a name-keyed strategy
table; one DFS engine parameterized by an adjacency callable; direction handled by edge
reversal rather than traversal branching; plan-then-execute routing with strategy
dispatch; level-synchronous frontier expansion for metapaths.

**Where it strains.** Two semiring resolvers with deliberately divergent semantics
(`core.py:85-92` vs `:95-105`). Missing-morphism handling is inconsistent across the
three consumers (`routing.py:218-220`, `:549-551`). Hand-rolled operator tables sit
against the stated library-first invariant (`core.py:9-11`, `:14-20`).
`execute_routing_plan` accepts a mode it cannot honour for segmented plans
(`routing.py:446-451`). Metapath expansion has no cycle guard and no frontier cap
(`routing.py:525-528`, `:586-596`).

---

### 2.9 Computational creativity — `theloom/analysis/`

**Purpose.** A store-free, IO-free library of scoring and search algorithms that turn an
already-hydrated graph into cross-domain mappings, analogy transfers with novel-entity
proposals, concept slippages, approximate subgraph matches, component signatures,
far-analogy candidate pairs, and interestingness scores. It is a direct implementation
of a named literature stack.

**Key files.** `cwsg.py`, `crossdomain.py`, `slippage.py`, `absence_surprise.py`,
`adaptability.py`, `interestingness.py`, `isomorphism.py`, `component_signatures.py`.

**How it is built.** Store-free pure scorers over hydrated wire dictionaries; options
resolved to named defaults at the function boundary; weighted sub-scores divided by
total weight and clamped; module docstrings pin deliberately approximate algorithms as
behavioural contract; hard input caps bound combinatorial cost; prebuilt indexes are
injected so graph construction amortizes across scored items.

**Where it strains.** This is the most tension-dense group in the package (11 recorded).
Adaptability skips the weight normalization every sibling scorer applies
(`adaptability.py:124-135`). Timeout budgets are advertised in three modules and
enforced in one (`crossdomain.py:18`, `:29`). A timed-out subgraph search is
indistinguishable from a complete one (`isomorphism.py:189-194`, `:243-244`). Oversized
input raises in one module and is silently truncated in another
(`crossdomain.py:167-174` vs `isomorphism.py:37-38`). **The analysis package imports the
operations layer while the operations layer imports analysis**
(`component_signatures.py:32`). Two `farAnalogyScore` fields carry incomparable scales
(`component_signatures.py:217`, `:221-226` vs `sliced_wasserstein.py`). Slippage
failures are swallowed whole inside the transfer pipeline (`cwsg.py:152-166`).

---

### 2.10 One-call bundles — `theloom/composites/` (two groups)

**Purpose.** High-level commands that bundle many internal operations into a single
structured answer. Each module owns exactly one command: it declares an input model,
resolves a store, runs a fixed ordered list of named sections through the shared runner,
and returns an envelope carrying per-section data, wall-clock timing and error text.
Composites are consumers — they orchestrate, and define no store access or domain
algorithm of their own.

**Part 1** spans read-only reconnaissance (`graph-reconnaissance`, `entity-deep-dive`,
`provenance-audit`, `influence-map`, `multi-graph-landscape`), exploration ranking
(`explore-frontier`), generative discovery (`far-analogy-retrieval`, `analogy-transfer`,
`hypothesis-engine`, `propose-entities`), an autonomous loop (`creativity-loop`), and
the two mutating workflows (`gap-fill-cycle`, `enrichment-crawl`), plus
`framework.py` — the shared section runner every composite calls.

**Part 2** holds `structural-survey`, `semantic-landscape`, `verified-extract`,
`simulate-change`, `reflect` and the capstone `self-improve`, which chains
reconnaissance → capability check → propose → simulate → rank → apply into one governed,
human-in-the-loop-by-default cycle.

**How it is built.** One section runner; prerequisite short-circuit via a failed-section
marker; a closure pipeline over one shared mutable state dictionary; store resolution
hoisted out of the timed sections; determinism by omitting the LLM and embedding
pipelines; compact-by-default payloads with a full escape hatch; copy-on-write
simulation against a disposable clone graph; best-effort side effects under suppression.

**Where it strains.** Auto-apply's write cycle is non-atomic, against the
one-atomic-mutation invariant (`self_improve.py:327-351`, `:361-394`, `:396-410`). A
failed simulation makes a proposal rank *higher*, not lower (`self_improve.py:269-270`,
`:291-2xx`). Full-graph cloning buys perfect isolation at O(graph) cost per simulation
(`simulate_change.py:100-110`, `:246-249`), and best-effort cleanup can leave `sim-<uuid>`
graphs behind (`:310-313`). `centralityDelta` reports raw degree, not a centrality
measure (`simulate_change.py:65-68`, `:91`). `reflect` reads like a report but mutates
entity observations by default (`reflect.py:97`, `:315`). PageRank scores are published
under the key `eigenvector` (`graph_reconnaissance.py:132-138`). Three incompatible
top-level result shapes coexist across one package (`analogy_transfer.py:8-13`,
`propose_entities.py:2-6`). `explore-frontier` maps advice back to regions by Python
object identity (`explore_frontier.py:214-223`).

---

### 2.11 Documents — `theloom/documents/`

**Purpose.** Turns external artifacts — files, directories, raw strings and URLs — into
embedded, searchable chunks that live inside the same FalkorDB instance as the graph. It
owns format detection and parsing into one uniform block list, size-aware chunking with
sentence overlap, SSRF-hardened fetching, a declared chunk-metadata shape, and
event-sourced persistence into a dedicated chunk graph.

**Key files.** `ingestion.py`, `chunker.py`, `parsers.py`, `chunkstore.py`, `ssrf.py`,
`metadata.py`.

**How it is built.** Chunk storage is a graph space, not a second store. Format dispatch
converges on a single block normal form. A structural error taxonomy is translated at
the operations boundary. Egress is deny-by-default and revalidated on every redirect
hop. Chunking is three-phase with an atomic-block escape hatch.

**Where it strains.** First ingest appends blindly while reingest diffs, so re-ingesting
a file duplicates its chunks (`ingestion.py:141-146`). Directory ingest records per-file
errors and then strips them before returning (`ingestion.py:209-226`). Chunk queries
apply the row limit before the category filter (`chunkstore.py:110-125`). The SSRF guard
resolves DNS separately from the connection it protects — a documented residual TOCTOU
(`ssrf.py:7-9`, `:74-80`). The four ingest entry points disagree on format detection and
size ceilings (`ingestion.py:164-186`). HTML and JSON extraction drop content by
allowlist and by length threshold (`parsers.py:180`, `:187-227`).

---

### 2.12 Exploration signals — `theloom/exploration/`

**Purpose.** The foraging-signals foundation for the `explore-frontier` composite. It
turns a graph's connected components into ranked "where should I look next"
recommendations from four independent normalized signals — age staleness, bridging
potential, coverage gap and a UCB1 bonus — fused by a renormalizing weighted average,
with a marginal-value-theorem patch-leaving policy on top and six anti-pattern guards
over aggregated state.

**Key files.** `__init__.py` (42-name facade), `composite_signals.py`, `guards.py` (486
lines), `exploration_state.py`, `coverage_gap.py`.

**Where it strains.** Three incompatible region-identity schemes coexist
(`exploration_state.py:89-97` vs `guards.py:374`). The stateless-by-design state store
leaves UCB and the patch-leaving policy informationless per run
(`exploration_state.py:100-113`). Bridging potential collapses to a binary constant
under its documented usage (`bridging_potential.py:12-16`). Fault isolation covers the
signals but not the two orchestrating entry points (`mvt.py:51-61`).

---

### 2.13 Verification — `theloom/verification/`

**Purpose.** The rule layer: a store-agnostic library of predicates that decide whether
a graph, or a single proposed mutation, satisfies the model's structural promises.
`checks.py` holds read-side guards and the five builtin invariants; `guards.py` holds the
mutation gate that entity and relation creation call before writing;
`capability_spec.py` layers a fluent DSL whose violations carry suggested-action strings
that feed the proposal engine; `propagation.py` implements AC-3 arc consistency over the
19 entity types.

**Where it strains.** Verification depends *upward* on the operations layer's private
helpers (`capability_spec.py:80`, `:95`). Duplicate rules are deduplicated by import in
some places and copy-pasted in others (`capability_spec.py:60-69`). Every capability
check re-lists the entire graph (`capability_spec.py:82-83`, `:96-97`, `:110-111`,
`:151-152`). Cycle detection recurses in Python on graph depth (`checks.py:184-210`). The
duplicate-name warning matches partially and case-insensitively (`guards.py:46-51`).

---

### 2.14 Prose generation — `theloom/synthesis/`

**Purpose.** Turns a knowledge graph into prose, and prose back into checkable claims.
Its spine is the Plan-Traverse-Realize pipeline: the planner selects an anchored ego
subgraph and decomposes the query; the traverser walks those regions emitting per-entity
evidence with Viterbi-decayed confidence and a provenance trail; the realizer linearizes
each region causally and renders narrative, outline, causal chain, evidence map,
proposal or raw text. A fidelity grader then scores the generated text against the
source graph. A second, independent subsystem implements counterexample-guided inductive
synthesis over a seeded PRNG.

**Where it strains.** Source passages are structurally supported but permanently empty —
`links.py:13-14` returns `[]`. `quick_verify` falls back to regex-matching violation
prose (`cegis.py:278-303`). Two fidelity modes report the same score field with
incomparable semantics (`fidelity.py:220-274`). LLM and parse failures are swallowed
without a signal (`fidelity.py:152-153`, `decomposer.py:75`). `relationCount` counts
relations whose far endpoint was dropped (`selector.py:124-129`). The whole pipeline
works in untyped wire dictionaries while the project holds the domain model as its
single source of truth.

---

### 2.15 Symbolic math — `theloom/symbolic/`

**Purpose.** An in-process computer-algebra engine wrapping SymPy behind a single total
function that maps a string operation name onto one of 21 handlers, executes it under a
SIGALRM watchdog, and returns a JSON-serializable envelope instead of raising. It owns
all expression parsing, all formatting, and a small chain interpreter.

**Where it strains.** LaTeX is an output format but never a working input format — the
docstring admits the branch is dead (`core.py:7-8`, `:36-4x`). The never-raises
guarantee has a hole outside the main thread (`core.py:998-1000`, `:1014-1016`).
`sympify` on caller-controlled strings assumes a trusted caller (`core.py:48`, `:772`).
The dispatch table has grown to 21 operations while the module still describes seven
(`core.py:3`, `:78-80`).

---

### 2.16 Structural fingerprints — `theloom/reification/`

**Purpose.** A two-file leaf package providing reusable Weisfeiler-Leman structural
fingerprinting over a hydrated graph: hash each node's rooted neighborhood to a bounded
depth so isomorphic local structure collapses to the same 16-hex digest, then bucket
nodes by digest into candidate pattern groups.

**Where it strains.** Two copies of the WL hash exist — this shared module and the
frozen inline copy inside `reify-patterns` (`fingerprint.py:5-8`). The hash is
direction-sensitive at depth 1 and direction-blind at depth 2 and beyond
(`fingerprint.py:63-72`). A group's description is one arbitrary member's neighborhood,
not the group's (`fingerprint.py:139-146`). The 64-bit truncated digest trades compact
keys for silent collision merging (`fingerprint.py:26-27`).

---

### 2.17 Visualization payload — `theloom/viz/`

**Purpose.** Turns a live graph into a shippable visualization payload. It resolves
which slice to show, enriches it with three optional analysis sections, validates the
whole thing against a versioned wire contract, and emits it through one of three
transports: raw JSON, a self-contained single-file HTML page carrying the committed
React/sigma.js build, or a read-only REST service. It computes almost nothing itself —
only the 2D PCA fallback and the degree truncation.

**Key files.** `bundle.py` (the single assembler), `schema.py`, `scope.py`, `html.py`,
`serve.py`, `analytics.py`, `temporal.py`, `semantic.py`.

**What must stay true.**

- Every bundle the assembler returns has passed schema validation —
  `bundle.py:132-151`.
- Injected bundle JSON can never terminate the template's script block —
  `html.py:33`.
- A missing or unbuilt frontend template fails as a typed configuration error —
  `html.py:28-32`.
- Live-mode HTTP status is a typed-code table lookup, never prose matching —
  `serve.py:28-35`.
- Degree truncation is deterministic and always disclosed in the payload metadata —
  `bundle.py:66-76`.
- The bundle ships entities of every status, not just active ones — `scope.py:43`.
- Live mode is read-only: every registered route is a GET — `serve.py:108`, `:140`,
  `:166`, `:181`.
- A graph with fewer than three vectors omits the semantic section entirely —
  `semantic.py:19-21`, `:64`.
- Bi-temporal reconstruction is one store call, not a client-side approximation —
  `scope.py:55-73`.

**Where it strains.** An `asOf` bound applies to entities, relations and events but
leaves analytics and semantics at the present (`bundle.py:115-121`) — a scoped or
truncated entity set therefore coexists with whole-graph analytics. Two different
`asOf` comparison strategies live inside one bundle (`temporal.py:15` vs
`bundle.py:99-105`). Search scope silently drops the non-active entities the rest of the
bundle ships (`scope.py:88-97`). The static path writes to a caller-controlled
filesystem location (`html.py:55-58`).

---

### 2.18 The Tapestry front end — `tapestry/`

**App shell.** `main.tsx` mounts the app inside a bundle provider; `App.tsx` is the
chrome and the router of last resort — brand mark, entity and relation counts, an
optional bi-temporal note, an ARIA tablist switching among the five views, a theme
radiogroup with an OS-preference listener, live-mode-only controls, a polite live region
and two URL-hash effects that make the static page deep-linkable. The help overlay is a
real focus-trapping dialog.

*Invariants:* hash restore must run before the hash writer subscribes
(`App.tsx:233-245`); the URL hash is replaced, never pushed (`App.tsx:250-254`); exactly
one view component is mounted at a time (`App.tsx:395-407`); the live region is silent on
first load and distinguishes a switch from a refresh (`App.tsx:187-195`); help focus
makes a round trip (`HelpOverlay.tsx:70-72`, `:82-98`); the OS colour-scheme listener
exists only while the theme is auto (`App.tsx:261-268`).

*Strains:* the shortcut sheet is a hand-maintained copy of bindings defined elsewhere
(`HelpOverlay.tsx:27-60`); opening the modal does not suspend the app's global shortcuts
(`HelpOverlay.tsx:76-99`); four of the five tabs point `aria-controls` at panels that are
not in the DOM (`App.tsx:307` vs `:395-407`); narrow viewports hide the live indicator
but keep its controls (`App.css:382-398`).

**Shared kernel — `tapestry/src/lib/`.** Everything the four Sigma views need but none
owns: getting the bundle into the browser and agreeing on its shape (three-way source
resolution — live REST, inline injected JSON, dev fixture — behind a load-gated context
that memoizes the graph model); canvas interaction primitives (click-hold-drag, pure
threshold/resume decisions, wrapping label and hover renderers); export (visible
subgraph to standalone SVG, Sigma canvas layers flattened to PNG); and app-shell
affordances (global keydown dispatcher, roving-tabindex math, per-graph saved views in
localStorage).

*Invariants:* every renderer that wraps labels must also override the hover renderer
(`nodeLabels.ts:193-254`); the hover background is a rounded rect because sigma's
circle-joined shape goes NaN once a label wraps (`nodeLabels.ts:203-207`, `:241-247`); the
drag `moved` flag latches and stays latched for the rest of the hold
(`dragState.ts:46-57`) and a view's click handlers must consume that latch before acting
(`dragNodes.ts:26-30`, `:102-114`); PNG export must call `sigma.refresh()` synchronously
immediately before reading the canvases (`exportSvg.ts:272-326`); `graphToSvg` serializes
only the supplied visibility set and re-checks edge endpoints (`exportSvg.ts:139-173`);
the TypeScript bundle type is pinned to the committed JSON schema in both directions
(`schema.test.ts:166-259`); keyboard shortcuts never fire while the user is typing
(`keyboard.ts:20-24`, `:30-39`).

*Strains:* `loadBundle` has no failure path — a fetch error leaves the app on the loading
gate forever (`data.ts:57-71`). Exports are called WYSIWYG but omit every DOM-overlay
decoration (`exportSvg.ts:24-30`). Label reveal is interaction-gated because sigma's
level-of-detail engages too late (`nodeLabels.ts:5-16`).

**Views.** The Explorer is the primary weave (a WebGL force-directed canvas with search,
non-destructive facet filters, a shortest-path tool, legend, minimap, saved views and
export). The Overview rolls the same bundle into headline tiles and a centrality table.
The Semantic Map plots the embedding projection with cluster hulls and a lasso brush the
Explorer echoes. The Chronicle re-implements the store's as-of read semantics
client-side over the exported event log, feeding the projection into Sigma reducers so
dragging the scrubber replays the weave assembling itself without mutating the graph.
The Systems view reads the bundle as a systems-dynamics model: causal-only edges,
polarity-coloured with glyph overlays, feedback-loop isolation, and a raised-cosine pulse
travelling the isolated loop in its influence direction.

Every view follows the same split: a pure, unit-tested derivation module paired with a
React/Sigma shell that only renders it; layered reducers as non-destructive interaction
state; refs mirroring store state so reducers read current values without
re-instantiating Sigma; colour resolved only through CSS custom-property tokens;
redundant non-colour encoding of polarity and loop classification.

**Build and contract toolchain.** Vite with the single-file plugin inlines the whole app
into one HTML file; a post-build script checks that the data sentinel survived bundling
and copies the result into the Python package, where the HTML renderer substitutes the
bundle payload for that sentinel. The committed JSON Schema — generated from the Python
model — lets the front end assert it still understands what Python emits. Two Playwright
configurations partition end-to-end verification into a static `file://` smoke suite and
a live suite pointed at a running server.

---

### 2.19 The test suite — `tests/` (six groups)

The suite is not a lint on the code; it is where the architecture invariants stop being
prose and become executable assertions. Its shared infrastructure lives in
`tests/conftest.py` (a namespaced live-store fixture chain) and `tests/fakes.py` (shared
doubles). Two populations coexist without mixing inside a module: pure in-memory tests
that compute expected numbers by hand, and live-store tests that drive real commands
against a UUID-namespaced graph.

Representative pinned contracts, one per area:

- Every live-store test is namespaced and leaves the store as it found it —
  `tests/conftest.py:35-45`.
- Documented `loom` invocations must validate against the live CLI input models —
  `tests/test_claude_examples_contract.py:146-160`.
- A truncated consumption answer must balance: shown plus cut equals total —
  `tests/test_consumption.py:267-271`; and a suppressed hub means the answer is
  incomplete and must say so — `:522-530`.
- Structural extraction never emits an untyped association edge —
  `tests/test_extraction_resolution.py:451-463`; and no extracted edge points at an
  entity the extraction does not create — `:481-499`.
- An ambiguous symbol produces no edge at all —
  `tests/test_extraction_resolution.py:205-241`.
- Git visibility, not directory contents, decides what becomes an entity —
  `tests/test_extraction_units.py:413-469`.
- Polarity belongs to causal relation types only, at every write seam —
  `tests/test_ops_relations.py:118-143`.
- A read-port behaviour binds every adapter equally —
  `tests/test_read_port.py:62-80`.
- A mutation and its event append are one unit or neither happens —
  `tests/test_store_atomicity.py:92-99`.
- Server-side pushdown must be observationally identical to the Python filter oracle —
  `tests/test_store_pushdown.py:219-227`.
- Every search surface reports the same `1/(1+L2)` score scale —
  `tests/test_semantic_search_core.py:36-49`.
- The ANN candidate window is approximate and must be grown, not trusted —
  `tests/test_semantic_perf.py:440-497`.
- Typed error codes surface as fixed HTTP statuses with the code in the body —
  `tests/test_viz_serve.py:32-37`.
- Citation weight decays by an exact half-life against the supplied `asOf` —
  `tests/test_work_memory.py:262-293`.

Several module docstrings are defect narratives naming the bug the file exists to
prevent — `tests/test_ops_bulk.py:205-224` records that 1,270 call edges once vanished.

*Strains across the suite:* pure-unit and live-FalkorDB tests share one unmarked suite,
so the whole thing needs Docker and git (`tests/conftest.py:24-26`). Two conventions for
driving a command under test coexist. Tests reach into private surfaces to pin behaviour
the public API does not expose (`tests/test_cli_registry.py:12`, `:30-46`). A shared
doubles module exists, yet several modules still write their own
(`tests/test_entity_proposer_foundation.py`). Error prose and registry wording are
asserted as contract (`tests/test_enrichment_crawl.py:157`, `:170`). Two wall-clock
assertions live in a suite whose CI forbids performance gates
(`tests/test_enrichment_crawl.py:345-370`, `tests/test_gap_fill_cycle.py:111-123`). Seven
copies of the same entity and relation builders exist across modules
(`tests/test_name_addressing.py:40-70`, `tests/test_ops_analysis.py:19-42`). Hard delete
is supported and tested while the architecture forbids overwriting
(`tests/test_ops_entity.py:152-161`).

**Fixtures.** `tests/fixtures/repo/` is a seven-file miniature polyglot project that is
the single golden input for codebase extraction — a three-file Python banking service
(`src/`), a TypeScript entry point over a JavaScript helper (`lib/`), two Markdown
documents engineered as doc-link positive and negative cases (`docs/`), and a README plus
a token stylesheet proving non-code files still become graph roots. Every character is
load-bearing test data: `roundCents` is defined twice on purpose so no document mention
of it may resolve (`lib/index.ts:22`, `lib/helper.js:5`); `docs/glossary.md` must
contribute zero relations (`:3-14`); `docs/architecture.md` yields exactly four
references and two refusals (`:3-10`); rationale comments bind to the innermost enclosing
symbol, else to the file (`src/policy.py:8`). The strain is inherent: the fixture is
simultaneously a growable negative-case corpus and a frozen count baseline, so adding a
case rewrites unrelated assertions.

---

### 2.20 Repository control plane and design records

**Repo root.** `pyproject.toml` is the single manifest — dependency floors, the two
console entry points, and the configuration for all three green-main gates.
`docker-compose.yml` stands up the one substrate the architecture allows. `CLAUDE.md`,
`README.md`, `STACK.md`, `CONTRIBUTING.md` and `COMMANDS.md` are the documentation tier.
Two `scripts/` files are operator tooling — a demo seeder and a synthetic 50k/100k
benchmark generator — that drive the store directly, bypassing the CLI.

*Invariants:* `COMMANDS.md` is generated from the registry and never hand-edited
(`COMMANDS.md:3`); the green-main gate is lint plus format-check plus `mypy --strict`
plus pytest, with a template drift check (`CONTRIBUTING.md:29-38`); `mypy --strict`
covers only `theloom` — scripts and tests sit outside the type gate
(`pyproject.toml:67-70`); dev seed scripts never write to the caller's default graph
(`scripts/gen_bench_graph.py:65-68`, `scripts/seed_live_dev.py:24-27`).

*Strains:* the command count is hand-copied into two documents and generated into a
third (`COMMANDS.md:5`, `README.md:12`). The repository layout is described three times
and the copies already differ (`CLAUDE.md:56-73`, `README.md:356-370`,
`CONTRIBUTING.md:110-118`). Shipped features still carry pre-implementation spec status
(`docs/superpowers/specs/2026-07-11-loom-visualization-design.md:4`). An ISC project's
only supported store is SSPL-licensed (`pyproject.toml:7`, `STACK.md:22`).

**Lockfile.** `uv.lock` is the resolved, hash-pinned closure — 187 package entries across
4,909 lines, every one digest-pinned to a single index. It records support for five
Python bands from 3.11 to 3.15, and the document-AI stack is non-optional: a default sync
installs torch, transformers and onnxruntime. Its recorded strains: `z3-solver` is
declared twice with divergent version floors; the tree-sitter grammar set has two
independent owners; and on Python 3.14+ the UMAP path resolves onto 2021-era sdist-only
numba and llvmlite.

**Design records.** `docs/superpowers/` holds the matched spec-and-plan pairs behind the
visualization surface and the map-codebase skill. Each plan is an agent-executable
contract: numbered tasks, literal file contents, exact shell commands, gate lists and
commit pathspecs, executed as a fixed red-green-gates-commit cycle. They are the
authoritative rationale for `theloom/viz`, `tapestry/` and the `.claude/` pipeline — none
of which explain their own tradeoffs in code. Their recorded strain: the plans are frozen
and drift from the code they specified, with nothing marking them superseded, and a
checkbox plan that no one ever checks.

---

## 3. Load-bearing modules

Ranked by how many other things touch them (degree) and by how much traffic passes
*through* them (betweenness).

### Most connected

| # | Module | Why it is a hub |
|---|---|---|
| 1 | `CommandInput` (`theloom/operations/common.py`) | The base class of ~158 command input models — it has 159 direct neighbors. Every CLI command's wire schema derives from it, so its `provided()` "explicitly set vs absent" semantics are load-bearing for the entire surface. |
| 2 | `typing` (stdlib) | Imported nearly everywhere; an artifact of a fully annotated codebase under `mypy --strict`, not an architectural fact. |
| 3 | `theloom/store/falkor.py` | 70 symbols in one file, imported by 35 modules. Every entity and relation row, and what it means, is defined here. |
| 4 | `theloom/model.py` | Imported by 77 modules while importing only 4 — the purest sink in the package, exactly as the "single source of truth" invariant intends. |
| 5 | `theloom/store/multigraph.py` | Imported by 100 modules — the facade every command receives as its second argument. |
| 6 | `tapestry/src/views/explorer/Explorer.tsx` | 95 symbols in one file; the largest single component in the repository and the front end's primary surface. |
| 7 | `tapestry/src/views/chronicle/Chronicle.tsx` | The bi-temporal replay view; wide because it wires the pure replay engine into Sigma reducers, a scrubber, an event rail and a diff mode. |
| 8 | `tapestry/src/views/systems/SystemsView.tsx` | The causal-loop view with polarity glyph overlays, loop isolation and flow animation. |
| 9 | `tapestry/src/views/semantic/SemanticView.tsx` | The embedding scatter with hulls and lasso brushing. |
| 10 | `tests/test_entity_proposer_foundation.py` | The widest single test module — it stands up a complete in-memory store double and hand-computes proposal scores. |
| 11 | `theloom/operations/semantic.py` | 965 lines, 43 symbols, 16 imports: the single funnel through which every retrieval and embedding command passes. |
| 12 | `theloom/extraction/treesitter.py` | 1,387 lines, 65 symbols: the whole parser and its public API in one module. |
| 13 | `theloom/cli/registry.py` | 164 command descriptors in one file — the declarative source of the entire CLI. |
| 14 | `theloom/operations/analysis.py` | 41 symbols covering the 16 traversal and analytics commands. |
| 15 | `tests/test_falkor_store.py` | 702 lines pinning store CRUD, the event log, bi-temporal reads and vector-index readiness. |

### Most traffic passes through

`theloom/store/multigraph.py` and `theloom/store/falkor.py` top betweenness by a wide
margin — every path from a command to data runs through the pair, which is precisely
what the "one transactional store" invariant asks for. `theloom/cli/registry.py` is
third: everything above the store enters through it. Then `theloom/viz/bundle.py` — the
single assembler behind all three visualization transports, so every visualization path
narrows to it. `theloom/operations/semantic.py` and `theloom/operations/analysis.py`
follow as the two widest command modules. `theloom/config.py` (imported by 11) and
`theloom/semantic/embed.py` (imported by 11) are chokepoints by design: one config path,
one embedding contract.

Four of the top fifteen betweenness entries are documents — `README.md`, this map,
`CLAUDE.md`, and two Tapestry phase plans. They score high because documentation-to-code
links make prose a genuine bridge between otherwise separate parts of the repository.
That is a property of the map, not of the runtime.

---

## 4. Dependency cycles

Fifteen cycles exist. Twelve are single-function self-references — recursion, which is
normal — and three are multi-node.

| Members | Verdict | Reason |
|---|---|---|
| `theloom/store/falkor.py` ↔ `theloom/store/read_port.py` | **intentional** | The read port declares the narrow typed read surface and names both concrete adapters; the adapters name the port back for typing. Its docstring states exactly why it exists: so read-only consumers stop naming `FalkorGraphStore` or, worse, `Any`. |
| `theloom/store/read_port.py` ↔ `theloom/store/memory.py` | **intentional** | The same protocol/adapter pairing on the in-memory side. Worth noting that the port imports *both* adapters, so the module cannot be loaded without both. |
| `CLAUDE.md` → `README.md` → `docs/superpowers/plans/2026-07-11-tapestry-phase-5.md` → `CLAUDE.md` | **intentional** | Prose cross-references harvested by documentation-link extraction, not a build dependency. Nothing loads at runtime. |
| `hash_at_depth` (`theloom/operations/reification.py`) | **intentional** | Recursive Weisfeiler-Leman neighborhood hashing. |
| `_hash_at_depth` (`theloom/reification/fingerprint.py`) | **suspect** | Same recursion — but this is the *second copy* of the same algorithm. `fingerprint.py:5-8` records that the logic is identical to the inline copy in `reify-patterns`, kept frozen deliberately. Two copies of one hash is a drift hazard, and the pair also surfaces as a top open seam in §7. |
| `_extract_calls`, `_find_identifier`, `_comment_notes`, `_extract_require_calls`, `_string_literal_vocabulary` (`theloom/extraction/treesitter.py`) | **intentional** | Five recursive tree-walkers over tree-sitter parse trees; recursion is the natural shape. |
| `_jsonify` (`theloom/cli/io.py`) | **intentional** | Recursive JSON coercion that also enforces the non-finite-float invariant. |
| `_generic_json_to_blocks` (`theloom/documents/parsers.py`) | **intentional** | Recursive descent over nested JSON. |
| `_js_string` (`theloom/synthesis/prompts.py`) | **intentional** | Recursive serialization for prompt construction. |
| `_resolve_references` (`theloom/symbolic/core.py`) | **intentional** | Recursive `$reference` substitution in the chain interpreter. |
| `_substitute` (`tests/test_claude_examples_contract.py`) | **intentional** | Recursive placeholder substitution in the documentation-example harvester. |

The recursion cluster is individually benign but collectively worth one note: two
recorded strains flag unguarded Python recursion on input-controlled depth —
`theloom/graph` uses recursive DFS in half its modules with no depth guard
(`analytics.py:119-142`, `cycles.py:38-50`), and `theloom/verification/checks.py:184-210`
recurses on graph depth. Neither is in the tree-walker set above, but they are the same
hazard class.

There is **no import cycle among the Python packages themselves at the file level** —
with one exception recorded as a tension rather than a cycle: `theloom/analysis` imports
`theloom/operations` while `theloom/operations` imports `theloom/analysis`
(`component_signatures.py:32`). That is a layering inversion the cycle detector does not
surface because the two edges land on different files.

---

## 5. Communities vs. directories

The clustering pass sampled 500 of 6,028 records, so it reads the neighbourhood
structure rather than the whole map. What it found is worth reporting precisely because
of how *little* it disagrees with the folder structure: twelve clusters, none larger
than five members, and every code cluster confined to a single file or a single view
directory.

- **Four clusters are same-file local variables** — `Explorer`, `Chronicle`,
  `SystemsView`, `EventList`, `exportSvg`, `buildGraph`, `schema.test`. Cohesion inside
  a module is high enough that similarity finds nothing to say across module boundaries.
- **Two clusters cross a file boundary but not a directory boundary** —
  `EventList.tsx` with `eventWindow.test.ts` (the component and the pure helper it
  renders), and `Legend.tsx` with `SearchBox.tsx` (two Explorer side panels). Both pairs
  are already siblings on disk.
- **Three clusters cross directory boundaries, and all three are in the written layer,
  not the code.** The red-green-gates task cycle recorded against the Phase 2-3 plans
  clusters with the failing-test-first cycle recorded against the Phase 5 and
  map-codebase plans — the same practice, described twice under two names, in two
  document groups. The type-coverage fixture pattern clusters with its own drift
  tension. And the retrieval-window claim in `theloom/semantic` clusters with the
  `min_score` full-scan tension in the same package.

**What this suggests about the real seams.** The directory tree is an honest
representation of this codebase's modularity; the clustering finds no hidden cross-cutting
concern that the folders conceal. The one place the folders *do* mislead is layering
rather than grouping: `theloom/analysis` and `theloom/operations` import each other, and
`theloom/verification/capability_spec.py` reaches up into
`theloom/operations/verification.py` for private helpers. Both are directories that look
like clean leaves and are not.

The second real seam is the two-layer split the map itself is built on: the structural
layer (5,031 records extracted from source) and the written layer (997 purposes,
patterns, invariants and tensions). The written layer is where the only genuinely
cross-directory similarity lives — which is a fair description of where the shared ideas
in this project actually reside.

---

## 6. Risks & tensions

241 strains are recorded. The ones below are the ones a reviewer should see first,
ordered by how much they threaten a stated architecture invariant.

1. **Entity updates snapshot; relation updates overwrite in place.** The event-sourced,
   bi-temporal invariant holds for entities and does not hold for relation documents.
   `theloom/store/falkor.py:913-919` (`SET r._doc = $doc`, no version node) against
   `:410-427`.

2. **Auto-apply's write cycle is non-atomic.** `self-improve` writes entity, relations
   and credit in separate store calls, against the one-atomic-mutation invariant.
   `theloom/composites/self_improve.py:327-351`, `:361-394`, `:396-410`, `:412-4xx`.

3. **`theloom/analysis` imports `theloom/operations` while `theloom/operations` imports
   `theloom/analysis`.** A layering inversion between a package documented as pure and
   store-free and the command layer above it.
   `theloom/analysis/component_signatures.py:32`.

4. **Verification depends upward on the operations layer's private helpers.** The rule
   layer imports `_coverage` and `_coupling` from `theloom/operations/verification.py`,
   inverting the same seam from the other side. `theloom/verification/capability_spec.py:80`,
   `:95`; the operations-side view is `theloom/operations/verification.py:492-524`.

5. **Hard-delete escape hatches inside an event-sourced store.** Present in relations,
   inference and work memory, and covered by tests, so the invariant "history is real" has
   documented exceptions. `theloom/operations/relations.py:364-376`,
   `theloom/operations/inference.py:235-237`, `theloom/operations/work_memory.py:166-169`;
   the tests that pin them are `tests/test_ops_entity.py:152-161`,
   `tests/test_ops_relations.py:400-406`.

6. **A failed simulation makes a proposal rank higher, not lower.** An exception during
   simulation yields a neutral score and `simulatedImpact` is never set, so the safest
   ranking signal is silently the most permissive.
   `theloom/composites/self_improve.py:269-270`, `:291-2xx`.

7. **Error codes classified from message prose.** Several handlers are written to satisfy
   substring matches, directly against invariant 4 ("never classify errors by
   substring-matching prose"). `theloom/operations/inference.py:480-482`,
   `theloom/operations/extraction.py:173-175`; also
   `theloom/synthesis/cegis.py:278-303`.

8. **Soft-fail envelopes bypass the typed-error boundary.** `solve-problem` and the
   symbolic commands always return an envelope and never raise, so their failures never
   become a typed code. `theloom/operations/solve.py:8-13`, `:363-364`;
   `theloom/operations/symbolic.py:3-5`.

9. **Two copies of the Weisfeiler-Leman hash.** A shared module and a frozen inline copy
   that must stay identical, with only a docstring holding them together.
   `theloom/reification/fingerprint.py:5-8` against
   `theloom/operations/reification.py`.

10. **The bundle's `asOf` bound is partial.** Entities, relations and events honour it;
    analytics and the semantic projection stay at the present, so a historical
    visualization shows historical nodes with present-day centrality.
    `theloom/viz/bundle.py:115-121`.

11. **`loadBundle` has no failure path.** A fetch error leaves the front end on the
    loading gate forever, with no error state. `tapestry/src/lib/data.ts:57-71`.

12. **The SSRF guard resolves DNS separately from the connection it protects.** A
    documented residual TOCTOU window on every outbound fetch.
    `theloom/documents/ssrf.py:7-9`, `:74-80`.

13. **First ingest appends blindly while reingest diffs.** Re-ingesting the same file
    through the ingest path duplicates its chunks. `theloom/documents/ingestion.py:141-146`.

14. **Cross-graph lookup scans every graph and builds a store per graph to do it.** The
    cost is linear in the number of named graphs on a read that looks like a point
    lookup. `theloom/store/multigraph.py:96-97`, `:132-137`.

15. **As-of reads reconstruct the past by scanning the whole present.** Every bi-temporal
    read is a full scan. `theloom/store/falkor.py:312-383`.

16. **Metapath expansion has no cycle guard and no frontier cap.** A cyclic metapath over
    a dense graph has no declared bound. `theloom/algebra/routing.py:525-528`, `:586-596`.

17. **A timed-out subgraph search is indistinguishable from a complete one.** The
    `timed_out` flag exists but the early return does not surface it to every caller.
    `theloom/analysis/isomorphism.py:189-194`, `:243-244`.

18. **`reflect` reads like a report but mutates by default.** `dry_run` is optional with
    no default true, and `bool(None)` is `False`.
    `theloom/composites/reflect.py:97`, `:315`.

19. **Two wall-clock assertions in a suite whose CI forbids performance gates.**
    `tests/test_enrichment_crawl.py:345-370`, `tests/test_gap_fill_cycle.py:111-123`.

20. **The repository layout is described three times and the copies already differ.**
    `CLAUDE.md:56-73`, `README.md:356-370`, `CONTRIBUTING.md:110-118` — the cheapest fix
    on this list.

---

## 7. Open seams

Areas the map found highly similar but *not* connected — candidates for consolidation,
or for a link that should exist and does not.

**Genuine duplication.**

- `_hash_at_depth` (`theloom/reification/fingerprint.py`) and `hash_at_depth`
  (`theloom/operations/reification.py`), similarity 0.76 — the two Weisfeiler-Leman
  copies from §6.9, confirmed independently by similarity.
- `DocumentIngestion._ingest` and `DocumentIngestion.ingest_content`
  (`theloom/documents/ingestion.py`), 0.79 — the highest-similarity pair in the Python
  package, and the same file whose four entry points are recorded as disagreeing on
  format detection and size ceilings.
- `list_relations` and `get_relations` (`theloom/operations/relations.py`), 0.76 — two
  read commands with near-identical descriptions and separate implementations.
- `TypeCompatibilityGraph.get_valid_sources` / `get_valid_targets` /
  `get_valid_relations` (`theloom/synthesis/generator.py`), 0.77 and 0.76 — three
  near-identical lookups over the same table.

**Documentation drift, front end.**

- Two claims about PNG export say the same thing with different wording (0.84), and two
  claims about SVG export do the same (0.81). These are the two highest-similarity pairs
  in the whole map, and they are both descriptions of `tapestry/src/lib/exportSvg.ts`
  written twice — once against the shared kernel and once against the app shell. The
  export contract is described in two places; only one can be canonical.
- `Live/static/dev mode is decided by parsed shape, never by the sentinel literal` (a
  stated invariant) and `Mode detection by parsed shape, never by sentinel literal` (a
  named pattern), 0.75 — the same rule recorded at two levels of abstraction.

**Naming collisions worth a rename.**

- `FOCUSABLE` and `focusables` in `HelpOverlay` (0.77); `facets` and `Facets` in
  `FilterPanel` (0.76); `Confidence` and `confidence` in `DetailPanel` (0.76). In each
  case a type and a value differ only by case, which is legal TypeScript and a reading
  hazard.
- `createWrappedLabelRenderer` and `createWrappedHoverRenderer`
  (`tapestry/src/lib/nodeLabels.ts`), 0.76 — deliberately paired, and the invariant at
  `nodeLabels.ts:193-254` says you must never install one without the other. The
  similarity is a feature; the seam is that nothing enforces the pairing at the type
  level.
- `TestIdentifiesAsLoom.test_true_for_the_loom_package_name` and
  `..._for_theloom_package_name` (0.77) — two test names distinguished only by a space
  that the names themselves cannot show.

**Test-name near-collisions** account for the remaining pairs
(`test_store_pushdown`, `test_read_port`, `test_ops_entity`, `test_store_atomicity`,
`test_consumption_budget`). They are not defects, but they are the places where a
reader scanning failures will misread which case broke.

**Structural islands.** Four things in the repository connect to nothing else:
`tapestry/src/views/chronicle/Chronicle.css`,
`tapestry/src/views/explorer/Explorer.css`, `tests/__init__.py`, and `uv.lock` together
with everything written about it. The two stylesheets are imported by their components at
build time in a way source parsing does not see; `tests/__init__.py` exists only to make
`tests` importable; and `uv.lock` determines what every import in the package actually
resolves to at runtime while being connected to none of them. That last one is the
meaningful island: the lockfile is load-bearing for the whole build and structurally
invisible.

---

## 8. Coverage & methodology

**Coverage.** 45 module groups carry a full written layer — purpose, patterns,
invariants and strains. **No group was left unenriched.** This incremental run enriched
25 groups against commit `e4a12a1`:

`repo root (part 1/2)`, `tapestry/src`, `tapestry/src/lib`, `tests (part 1/6)` through
`tests (part 6/6)`, `tests/fixtures/repo`, `tests/fixtures/repo/src`, `theloom`,
`theloom/analysis`, `theloom/cli`, `theloom/composites (part 1/2)`,
`theloom/composites (part 2/2)`, `theloom/documents`, `theloom/exploration`,
`theloom/extraction`, `theloom/operations (part 1/3)` through
`theloom/operations (part 3/3)`, `theloom/semantic`, `theloom/store`, `theloom/viz`.

The remaining 23 groups carry findings from the prior full run at commit `067a5b8`:
the four `docs` groups, `repo root (part 2/2)`, both `tapestry` groups, `tapestry/e2e`,
the four `tapestry/src` parts, the four earlier `tests` parts, `tests/fixtures`,
`theloom/algebra`, `theloom/graph`, `theloom/reification`, `theloom/symbolic`,
`theloom/synthesis` and `theloom/verification`. Anchors from that run point at
`067a5b8`; line numbers in those sections may have shifted.

**Files not parsed: 42.** Markdown, JSON, CSS, YAML, TOML and the lockfile have no
grammar in this configuration. They are present as files, and their documentation links
into code are intact, but their internal contents are not broken into symbols.

**Working tree was dirty.** `CLAUDE.md` had uncommitted changes and `CONTEXT.md` was
untracked when extraction ran. Findings about those two files may not match `e4a12a1`.

**Sampling.** The community analysis in §5 sampled 500 of 6,028 records; the open-seam
analysis in §7 likewise. Both report their own sample, and neither claims exhaustiveness.

**Graph.** `codebase-the-loom`, at commit `e4a12a1b188e5391ec431a8c5754d2fa4733b1f9`.

**To rebuild this map:** `/map-codebase /Users/jameswinans/Dropbox/Development/the-loom`.
An incremental run reads `map-manifest.json`'s `commit` field as its starting point and
re-derives only what changed since.

**To interrogate the map directly**, rather than re-reading this document — see
[QUERYING.md](QUERYING.md) for the full cheat sheet. The two commands worth knowing
first:

```bash
loom entity-deep-dive '{"name": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'
loom hybrid-search '{"query": "bi-temporal invalidation", "graph": "codebase-the-loom"}'
```

Every claim in this document traces to a record in that graph, and every record carries
the file and line it came from.
