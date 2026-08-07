---
repo: the-loom
commit: 0343de03f15efbb6ce1d329e8f8703e18bad4900
graph: codebase-the-loom
generated: 2026-08-07
mode: incremental
---

# The Loom — Architecture Map

## 1. Executive overview

The Loom is a knowledge-graph substrate with exactly one way in: a JSON-in/JSON-out
command line of 164 commands, declared once in a registry and generated mechanically
into a Typer application. Everything the system stores — graph topology, entity vectors,
document chunks and the event log — lives inside a single FalkorDB instance, and every
mutation is one Cypher statement committed together with its event append. Updates never
overwrite: they snapshot the outgoing incarnation as a version record and open a new one,
so *what did this look like at time T* is a real question with a real answer. Above the
store sits a thin operations layer that owns command semantics and nothing else; above
that, composites that chain many operations into one structured answer; and beside both,
pure libraries — graph algorithms, semiring traversal, analogy scoring, symbolic math,
prose synthesis, verification predicates — that take plain wire dictionaries and touch no
store at all. A separate, contributor-only frontend workspace (`tapestry/`) compiles a
React/sigma.js single-page app into one self-contained HTML file that the Python package
ships and that three transports feed: a static export, a live read-only REST service, and
a committed dev fixture.

### Stats

| Measure | Value |
| --- | --- |
| Files recorded | 379 source files (every one present in the tree at this commit) and 65 external package records |
| Language mix | Python 257, TypeScript 74, Markdown 20, JSON 14, CSS 9, JavaScript 2, YAML/TOML/lockfile 3 |
| Files tree-sitter could not parse | 0 this run — extraction touched only the two re-enriched groups (`repo-root-1`, `examples`) and the one carried group (`docs-architecture`); §8 accounts for the running total across the whole graph |
| Symbols in the current projection | 6,530 records — 2,783 functions and methods, 1,478 variables and constants, 451 classes/interfaces/type aliases, 444 file and package records (up 4 for the new `examples` guide files) |
| Written layer | 53 subsystem purposes, 355 conventions, 629 invariants, 347 risks |
| Records including superseded versions | 9,350 |
| Relationships in the current projection | 13,868 — last independently measured at commit `e9d4b425bba8c47b96922b5acfe0fdca3fe9481c`; this refresh's cheap re-run (graph-stats, cycles, centrality, components) does not isolate a current-only relation count, so the figure is carried forward rather than restated as fresh (§8) |
| Relationships including closed-out versions | 19,315 |
| Working tree at extraction | clean |

The gap between 9,350 stored records and 6,530 in the current projection is not code
churn: it is mostly the written layer being re-authored, plus a handful of structural
records a file rename supersedes rather than updates in place. Every superseded record is
either a purpose, convention, invariant or risk note that an earlier mapping run wrote and a
later one replaced, or a file/package record whose path no longer exists. That is the
mapping design working as specified — a re-run supersedes only what actually changed and
leaves the rest of the structural layer to incremental re-extraction
(`docs/design/2026-08-03-map-codebase-design.md:146-155`).

Since the previous edition (commit `8866c267f2de896bcaadfa452ef7e2ac275fa494`) two module
groups were freshly re-enriched. `examples` is new to the graph entirely — a four-file guide
layer for the repository's three shipped Claude Code skills (§2.46). `repo root part 1 of 7`
(module group `repo-root-1`) was re-enriched alongside it, because the same change that added
`examples/` also rewrote how `README.md` points at it — the eleven-file declaration surface
now delegates the skills description it used to carry inline (§2.42). One group,
`docs/architecture` (module group `docs-architecture`), carried a diff too small to trigger
re-enrichment this run, so §2.45 is unchanged from the previous edition — as every edition of
this document has to disclose, that section is always describing the commit before this one
anyway, because it cannot know its own hash while it is being written (§2.45). Component
structure was re-verified this run: still exactly two connected components, the larger now
holding 6,539 of 6,540 records (§5). Community clustering and the open-seams scan were not
re-run this refresh; §5 and §7 are carried forward from commit `e9d4b425` and say so where it
matters.

---

## 2. Subsystem walkthrough

Forty-six module groups have been read and written up. They fall into five areas: the
Python package (`theloom/`), the frontend workspace (`tapestry/`), the test suite, the
fixtures, and the repository's own declaration and design documents.

### 2.A The Python package

#### 2.1 The contract layer — `theloom/` (group `theloom`)

Six top-level modules that everything else imports and that import almost nothing
themselves. `model.py` is the single source of truth for the domain: every enum value in
a stable order, the entity/relation/confidence/provenance shapes, the paired `*Input`
creation schemas, the confidence-label scale, and the five-state lifecycle transition
table. `errors.py` declares the six structured error codes as a typed exception
hierarchy. `config.py` is the one configuration resolution path — flags, then environment,
then the user-level `.loom/config.json`, then defaults — plus optional LLM routing and the
process-global embedder test seam. `timeutil.py` fixes the timestamp shape and
`migrate.py` imports graph snapshots.

Key files: `theloom/model.py` (imported by 80 modules), `theloom/errors.py`,
`theloom/config.py`, `theloom/timeutil.py`, `theloom/migrate.py`.

Conventions: snake/camel wire boundary via Pydantic field aliases; enums as the stable
wire contract; a typed exception hierarchy that carries its own CLI error code; layered
override resolution in a single pass.

Invariants worth knowing:
- Unknown fields are rejected — every wire model forbids extras
  (`theloom/model.py:361-364`, and every model subclasses that base).
- The five-state lifecycle is enforced by a table; `retracted` is terminal and only
  `investigating` returns to `active` (`theloom/model.py:313-338`, `:341-353`).
- Every wire timestamp is ISO 8601 UTC with millisecond precision and a `Z`
  (`theloom/timeutil.py:12-15`, `theloom/model.py:38-49`).
- Configuration resolves once, through one loader, with precedence flags > env > file >
  defaults (`theloom/config.py:150-219`).

#### 2.2 Persistence — `theloom/store` (group `theloom-store`)

Where the two hardest architecture promises stop being prose and become Cypher. The
package maps the domain model onto one FalkorDB instance so topology, vectors, the event
log and document chunks share a single transactional store, and it makes every mutation
event-sourced and bi-temporal: a write is one Cypher statement plus its stream append
inside one Redis `MULTI/EXEC`; an update snapshots the outgoing incarnation as a version
node instead of overwriting it; a delete invalidates rather than destroys unless the
caller explicitly asks for erasure. Everything above it — operations, composites,
semantic, analysis, viz, documents, extraction — reaches the graph only here.

Key files: `theloom/store/falkor.py` (1,143 lines; 70 symbols, imported by 36 modules),
`theloom/store/space.py` (the shared chassis: graph handle, event log, commit primitive,
paged reads, vector and range indexes), `theloom/store/commit.py`,
`theloom/store/read_port.py`, `theloom/store/multigraph.py` and
`theloom/store/bridges.py`.

Conventions: one shared store chassis inherited by both the knowledge-graph store and the
chunk store; a one-statement commit primitive with two-directional compensation;
snapshot-on-write via version nodes; a derived read index that prefilters while Python
confirms; a `SKIP`/`LIMIT` paging wrapper over every full scan; the guard lives inside the
write, not in front of it.

Invariants worth knowing:
- A mutation and its event are committed as one unit or neither reaches the server
  (`theloom/store/commit.py:91-103`, `:12-20`).
- A failed Cypher half discards the events queued beside it; a failed event half is
  repaired *forward*, never rolled back, and the caller still sees success
  (`theloom/store/commit.py:106-111`, `:112-170`, `theloom/store/events.py:94-119`).
- An update snapshots the prior incarnation as a closed version node before the document
  is swapped, for entities (`theloom/store/falkor.py:420-437`) and relations
  (`:936-963`).
- Retracting an entity closes out every attached edge and drops its embedding in the same
  statement (`theloom/store/falkor.py:478-486`).
- Deletion invalidates by default; `hard=True` is the only path that destroys history
  (`theloom/store/falkor.py:439-500`, `:1005-1026`).
- `filters.py` is the semantics oracle; the Cypher pushdown may only ever be a superset,
  and server-side limit/count run only when the pushdown alone decides membership
  (`theloom/store/falkor.py:154-160`, `:132-151`, `theloom/store/filters.py:69-100`).
- Any full-scan read must page or FalkorDB silently truncates it at `RESULTSET_SIZE`
  (`theloom/store/paging.py:1-11`, `:24-44`).
- A vector index is write-once, sized from the vectors already stored, and only queryable
  once FalkorDB reports it `OPERATIONAL` (`theloom/store/space.py:122-137`, `:161-187`).

#### 2.3 The command line — `theloom/cli` (group `theloom-cli`)

The entire user-facing surface, holding no domain behaviour. `registry.py` declares every
command exactly once as a frozen descriptor built from a declarative row (name, category,
summary, Pydantic input model, handler, stdin stance); `app.py` generates one Typer
subcommand per descriptor at import time; `io.py` owns the wire protocol; `docs.py`
renders `COMMANDS.md` as a pure projection of the same descriptor list.

Key files: `theloom/cli/registry.py` (1,676 lines; 164 descriptors across 23 categories),
`theloom/cli/app.py`, `theloom/cli/io.py`, `theloom/cli/docs.py`.

Conventions: registry-driven command generation; documentation as a projection of the
registry; a single typed-error protocol boundary; lazy imports at the call site for heavy
or optional dependencies.

Invariants worth knowing:
- Every command except `version` and `init` is generated from the registry
  (`theloom/cli/app.py:108-109`; the two exceptions at `:54-57` and `:60-78`).
- Validation happens once, in `run_handler`, and Pydantic failures become
  `VALIDATION_ERROR` (`theloom/cli/registry.py:1666-1676`).
- stdout carries exactly the result document; diagnostics go to stderr and failures exit 1
  (`theloom/cli/io.py:79-84`, `theloom/cli/app.py:96-102`).
- `COMMANDS.md` is byte-identical to `generate_docs()` output, pinned by a drift test
  (`theloom/cli/docs.py:15-36`, `tests/test_generate_docs.py:34-40`).

#### 2.4 Command semantics I — `theloom/operations` part 1 (group `theloom-operations-1`)

The seam between the registry above and the store, graph, algebra, analysis and document
subsystems below. Every module owns one command family and exposes one plain function per
command with the same shape: a validated input model plus the multi-graph facade in, a
wire document out. The layer does six things and delegates the rest — declare the wire
schema, resolve which graph to talk to, add the operation-level semantics the raw store
does not have (name-first addressing, active-status filtering on id-hydrated reads,
budget honesty, revision bookkeeping, per-item error collection), translate library
exceptions into typed error codes, and shape the JSON. Part 1 covers the shared input
machinery, entity CRUD, the semiring and adaptive-routing commands, the traversal and
analytics commands, bulk import, the agent-facing comprehension commands
(`explore`, `find-callers`, `find-callees`, `blast-radius`) with their two deliberately
store-free cores, document ingest dispatch, and the proposer's typed options adapter.

Key files: `theloom/operations/common.py` (the `CommandInput` base and the shared entity
resolver), `theloom/operations/entity.py`, `theloom/operations/consumption.py`,
`theloom/operations/consumption_budget.py`, `theloom/operations/analysis.py`.

Conventions: a uniform `(params, multi) -> doc` handler; the Pydantic input model as the
wire schema; name-first addressing through one shared resolver; hydrate the whole graph
then delegate to a pure algorithm library; a store-free plain-data core behind the command
shell; one round-robin allocator serving two truncation policies; honest truncation as a
shared output contract.

Invariants worth knowing:
- Entity addressing takes exactly one of `id` or `name`, and a whitespace-only name is not
  a name (`theloom/operations/common.py:132-137`).
- An ambiguous name is refused with a candidate listing, never guessed
  (`theloom/operations/common.py:158-164`, `:82-91`).
- Consumption reads apply their own active-status filter, because id hydration carries
  none (`theloom/operations/consumption.py:254-269`, applied at `:339`, `:356`, `:468`).
- `explore`'s truncation accounting is exact — `shown + sum(cut) == total` — and when even
  the minimum honest answer will not fit, it says so instead of cutting below the floor
  (`theloom/operations/consumption.py:400-424`).
- `blast-radius` counts the seed and its `part_of` members as seeds, never as fallout, and
  a suppressed hub forces `truncation.applied`
  (`theloom/operations/blast_radius_traversal.py:129-155`,
  `theloom/operations/consumption.py:592-599`).
- Hub suppression requires both a percentile and an absolute degree floor
  (`theloom/operations/blast_radius_traversal.py:143`,
  `theloom/operations/consumption.py:90-93`).
- `bulk-import` is idempotent on the `name::entityType` composite key and reports only what
  this call actually wrote as created (`theloom/operations/bulk.py:221`, `:305-310`).
- Every `update-entity` bumps the version and auto-detects `changeType` in a fixed
  precedence (`theloom/operations/entity.py:271-281`).

#### 2.5 Command semantics II — `theloom/operations` part 2 (group `theloom-operations-2`)

The half of the operations layer that owns relations, the knowledge lifecycle and the
engine machinery: relation CRUD with the verification gate and bridge-aware neighbourhood
reads, duplicate consolidation, the seventeen epistemic queries plus credit propagation,
the forward-chaining inference engine whose rules and traces are themselves graph records,
Weisfeiler-Leman pattern reification, extraction dispatch with run status and rollback, the
zero-infrastructure JSON export, bridge-index queries, the prompt-profile loader and
`init`.

Key files: `theloom/operations/epistemic.py` (944 lines),
`theloom/operations/inference.py` (619), `theloom/operations/relations.py` (574),
`theloom/operations/extraction.py` (364), `theloom/operations/reification.py` (299).

Conventions: the same `(params, multi)` shape as part 1; a verification gate evaluated
before the write in both relation arities; tri-state field presence (absent vs explicit
null vs value); plan from store reads, model-validate the plan, then commit atomically;
engine machinery stored as graph records with prefixed JSON observations; refuse-by-default
with an explicit `force` opt-out; batch hydration — one query per graph, never one per item.

Invariants worth knowing:
- The causal/polarity partition is an invariant of the stored edge, not just of creation;
  `update-relation` enforces it against the resulting type/polarity pair
  (`theloom/operations/relations.py:321-359`).
- A failing strict relation batch still persists its valid prefix
  (`theloom/operations/relations.py:281-282`).
- The endpoint gate checks status, not just existence, in both arities
  (`theloom/operations/relations.py:56-59`, `:246-274`).
- `merge-entities` supersedes the secondary rather than deleting it, and is idempotent
  (`theloom/operations/merge.py:186-195`, `:172-175`).
- Inference rule conclusions may only reference variables the conditions bind
  (`theloom/operations/inference.py:188-194`).
- Every inference-derived relation carries provenance naming its rule and its trace —
  the only thing that makes a derived fact explainable
  (`theloom/operations/inference.py:391-398`).
- `run-inference` is a single snapshot pass with two-level dedup: derived facts cannot
  trigger further derivation (`theloom/operations/inference.py:299-320`).
- Credit propagation clamps confidence to `[0,1]`, halts below `minDelta`, and visits each
  node once (`theloom/operations/epistemic.py:816-858`).
- `export-graph` emits only relations whose endpoints both survive the entity filter, so
  the artifact never contains a dangling edge
  (`theloom/operations/portability.py:78-80`).

#### 2.6 Command semantics III — `theloom/operations` part 3 (group `theloom-operations-3`)

The reasoning-and-assurance third: the embedding lifecycle and the retrieval and discovery
commands, seven one-line adapters over the symbolic core, a natural-language solver routing
twenty operations into those adapters, the nine Plan-Traverse-Realize synthesis commands
including read-only cross-graph merged views, the guard / invariant / AC-3 / capability
suite plus the sandbox mutation-trace replayer, and the write half of cross-session
experiential memory. Every module here is a translation layer rather than an engine.

Key files: `theloom/operations/semantic.py` (965 lines),
`theloom/operations/verification.py` (641), `theloom/operations/synthesis.py` (626),
`theloom/operations/solve.py` (387), `theloom/operations/work_memory.py` (175).

Conventions: operations as thin adapters over an engine core; a typed soft-fail envelope
for external-dependency operations; duck-typed doc-store views that decouple synthesis from
FalkorDB; two-tier fail-fast verification; deterministic spread sampling instead of
first-N truncation; validate-then-write with a compensating rollback; sandbox-clone replay
for hypothetical mutation checking.

Invariants worth knowing:
- One retrieval binding backs every semantic read in the group
  (`theloom/operations/semantic.py:144-165`).
- Every similarity in the module is `1/(1+L2)`, not cosine — every threshold on the wire is
  on that scale (`theloom/operations/semantic.py:7-13`, thresholds at `:685`, `:713`,
  `:787`, `:900`).
- Embedding is opt-in and content-hash idempotent; nothing embeds as a side effect of a
  write (`theloom/operations/semantic.py:332-339`).
- An embedding failure is recorded on the entity, never raised to the caller
  (`theloom/operations/semantic.py:341-344`).
- Graph-mutating discovery and repair commands default to a dry run
  (`theloom/operations/semantic.py:492`, `:903`).
- Anchor search skips the embedding model entirely on a vectorless graph, and superseded
  or deprecated entities keep their vectors but may not anchor a synthesis
  (`theloom/operations/synthesis.py:112-127`).
- Cross-graph synthesis is read-only and refuses to ingest
  (`theloom/operations/synthesis.py:346-352`).
- Verification reads every entity status, not just the active projection
  (`theloom/operations/verification.py:48`, `:132-138`).
- `validate-mutation-trace` never touches the target graph: it clones, replays, and deletes
  the clone in a `finally` (`theloom/operations/verification.py:578-590`, `:639-641`).
- `record-outcome` writes nothing on a bad citation and cites each entity once
  (`theloom/operations/work_memory.py:103-123`, `:162-169`).

#### 2.7 Composites I — `theloom/composites` part 1 (group `theloom-composites-1`)

Eight one-call commands that bundle many internal operations into a single structured
answer, plus `framework.py`, the runner every composite in the package is built on. Each
module declares one input schema, resolves a store, runs a fixed ordered list of named
sections, and returns an envelope carrying per-section data, wall-clock timing and error
text. The eight span read-only reconnaissance, exploration ranking, generative discovery,
an autonomous multi-cycle loop, and the group's only two graph-mutating workflows.

Key files: `theloom/composites/framework.py`,
`theloom/composites/far_analogy_retrieval.py`, `theloom/composites/creativity_loop.py`,
`theloom/composites/enrichment_crawl.py`, `theloom/composites/gap_fill_cycle.py`.

Conventions: one section runner; prerequisite short-circuit; a closure pipeline over one
shared mutable state dictionary; declared capability boundaries reported in the payload;
compact-by-default payloads with a full escape hatch.

Invariants worth knowing:
- `time_section` never raises: every section outcome is a three-key result
  (`theloom/composites/framework.py:42-56`, `:59-61`).
- A non-null section error always accompanies `data: None`
  (`theloom/composites/framework.py:52`, `:56`, `:61`).
- `framework.py` imports nothing from `theloom`, preventing a layering leak
  (`theloom/composites/framework.py:15-20`).

#### 2.8 Composites II — `theloom/composites` part 2 (group `theloom-composites-2`)

Eleven more one-call commands. Seven are read-only analysis bundles — structural survey,
semantic landscape, influence map, multi-graph landscape, provenance audit, verified
extract and change simulation. The rest include the six-stage self-improvement capstone,
gap-driven hypothesis ranking and usage reflection.

Key files: `theloom/composites/self_improve.py` (605 lines),
`theloom/composites/simulate_change.py` (313),
`theloom/composites/hypothesis_engine.py` (415), `theloom/composites/reflect.py` (381),
`theloom/composites/influence_map.py` (208).

Conventions: section-thunk envelopes; copy-on-write simulation against a disposable clone
graph; a compensating hard-delete saga around the entity-plus-relations write; deterministic
decay-and-corroboration scoring of usage evidence.

Invariants worth knowing:
- `simulate-change` never mutates the graph it is asked about
  (`theloom/composites/simulate_change.py:100-110`, `:240-249`, `:310-313`).
- The simulation clone copies every status, not just active records
  (`theloom/composites/simulate_change.py:105-109`).
- Verdict ties break toward `degrades` (`theloom/composites/simulate_change.py:229-236`).

#### 2.9 Graph algebra — `theloom/graph` (group `theloom-graph`)

Hydrates wire documents into a small insertion-ordered directed multigraph and runs the
pure structural analyses on it: centrality and components, cycle detection and feedback-loop
classification, shortest and bounded all-simple paths, motif mining, subgraph filters, and
the parsers that read structured facts back out of observation strings. Its defining
constraint is determinism — enumeration order, tie-breaking and member order are part of
observable command output, which is why most algorithms are hand-written rather than
delegated.

Key files: `theloom/graph/hydrate.py`, `theloom/graph/cycles.py`,
`theloom/graph/analytics.py`, `theloom/graph/motifs.py`, `theloom/graph/metadata.py`.

Conventions: determinism-first hand-rolled algorithms; budgeted enumeration with truncation
flags instead of failure; observation strings as a structured side-channel.

Invariants worth knowing:
- Hydration drops dangling relations, so no edge can reference an absent node
  (`theloom/graph/hydrate.py:118`).
- Neighbour iteration is deduplicated and order-fixed at IN-then-OUT
  (`theloom/graph/hydrate.py:73-96`).
- Loop polarity is the parity of negative edges, with missing polarity read as positive
  (`theloom/graph/cycles.py:258-266`).

#### 2.10 Semiring traversal — `theloom/algebra` (group `theloom-algebra`)

The pure computational core for weighted traversal: five semirings as frozen operator
records, the extractors that turn a relation's strength label into a semiring element, one
shared DFS engine, and on top of that a relation-type registry sorting types into three
algebraic categories, a table of six cross-category morphisms, a query router, a segmented
executor, and a level-synchronous metapath engine. No CLI, no Pydantic, no I/O beyond an
optional lazy adjacency read.

Key files: `theloom/algebra/core.py`, `theloom/algebra/routing.py`.

Conventions: semiring as a frozen operator record behind a name-keyed table; one DFS engine
parameterized by an adjacency callable; direction handled by edge reversal, not by traversal
branching; plan-then-execute routing.

Invariants worth knowing:
- Traversal is a backtracking DFS, not Bellman-Ford: value and path are decoupled
  (`theloom/algebra/core.py:191-207`, `:160-161`).
- Adjacency emission order is part of the public contract; ties keep first discovery
  (`theloom/algebra/core.py:141-148`, `:128-131`).
- Relation categorization is total, with causal as the open-world default
  (`theloom/algebra/routing.py:44-49`, `:52-53`).

#### 2.11 Computational creativity — `theloom/analysis` (group `theloom-analysis`)

A store-free, IO-free library of scoring and search algorithms that turn an already-hydrated
graph into cross-domain mappings, analogy transfers with novel-entity proposals, concept
slippages, approximate subgraph matches, structural signatures, far-analogy candidate pairs,
and interestingness/confidence/adaptability scores. Each module implements one named piece
of the literature with the formula written out in its docstring.

Key files: `theloom/analysis/cwsg.py`, `theloom/analysis/crossdomain.py`,
`theloom/analysis/slippage.py`, `theloom/analysis/absence_surprise.py`,
`theloom/analysis/adaptability.py`.

Conventions: pure scorers over hydrated wire dictionaries; one literature algorithm per
module with the formula pinned in the docstring; deliberate approximation declared as the
behavioural contract; hard input caps as module-level constants.

Invariants worth knowing:
- Cross-domain mapping is strictly one-to-one
  (`theloom/analysis/crossdomain.py:198-219`).
- Novel transfer endpoints are `__NOVEL__`-prefixed placeholders, never graph ids
  (`theloom/analysis/cwsg.py:31`, `:110-119`, stripped at `:143-145`, `:208`, `:406`).
- Temperature is clamped to [0,1] and lowers the slippage threshold monotonically
  (`theloom/analysis/slippage.py:37`, `:54-56`).

#### 2.12 The meaning layer — `theloom/semantic` (group `theloom-semantic`)

Turns text into vectors, owns the single definition of "nearest" and the single retrieval
path, decides result order and grouping, owns what "needs embedding" means and how a
status/vector divergence is repaired, and on that base decides whether a proposed entity
already exists and generates entities the graph is structurally missing. It is deliberately
a dependency leaf: imported by operations, composites, documents, analysis, exploration and
viz, never the reverse.

Key files: `theloom/semantic/search.py`, `theloom/semantic/embed.py`,
`theloom/semantic/ranking.py`, `theloom/semantic/embedding_state.py`,
`theloom/semantic/deduplication_gate.py`, `theloom/semantic/entity_proposer.py`.

Conventions: Protocol-sliced collaborators instead of concrete store types; a
config-installed embedder override as the single injection seam; content hash of the
embedding text as the only cache key; plan-then-apply reconciliation; a growing candidate
window compensating for filters the vector index cannot answer.

Invariants worth knowing:
- Every vector is L2-normalized before it leaves the embedder
  (`theloom/semantic/embed.py:82-88`).
- Documents and queries are embedded with different task prefixes and no caller can bypass
  it (`theloom/semantic/embed.py:28-29`, `:90-99`).
- Embedding text is truncated at 30k characters, on a sentence boundary only within the
  last 20% (`theloom/semantic/embed.py:45-57`).
- Cosine similarity scores incomparable vectors 0.0 rather than raising
  (`theloom/semantic/embed.py:128-134`).

#### 2.13 Getting artefacts in — `theloom/extraction` (group `theloom-extraction`)

The package that turns artefacts living outside the graph into graph content. Its dominant
path is deterministic and LLM-free: tree-sitter parses each source file into file, class,
function and variable records plus containment, call, inheritance and import links; a
whole-project second pass joins the edges no single-file parse can resolve; a third pass
links Markdown documents into the code they name; every name, observation prefix and
evidence string those passes write travels through one encoding module so writers and
readers cannot drift; an incremental path replays a git diff over an existing graph,
superseding rather than deleting; and a thin driver keeps The Loom's own self-model current
from a stored commit marker. A second, unrelated path does LLM document extraction. The
only code the two share is the append-only run record used for status and rollback.

Key files: `theloom/extraction/treesitter.py` (1,386 lines; 65 symbols — parsers plus the
whole public API), `theloom/extraction/resolution.py`, `theloom/extraction/doclinks.py`,
`theloom/extraction/encoding.py`, `theloom/extraction/codebasediff.py`.

Conventions: two-pass extraction — per-file parse, then whole-project join; one module
builds and parses every codebase-graph string; plan the whole update, guard it, then write;
one generic symbol-edge resolver renamed at each call site; resolution certainty recorded in
the domain model's own confidence vocabulary; a mention becomes a doc link only after every
disqualifier fails; git, not the filesystem, decides what is in the codebase.

Invariants worth knowing:
- An incremental update supersedes entities; it never deletes them
  (`theloom/extraction/codebasediff.py:462-471`; vocabulary at `:69-70`).
- The structural diff only ever retracts edges structural extraction itself emits
  (`theloom/extraction/codebasediff.py:78-88`, `:266-282`).
- An update that looks like a collapse is refused rather than applied — a file that now
  extracts to nothing, or a plan superseding more than half the graph's file-owned records
  (`theloom/extraction/codebasediff.py:345-360`, raised at `:522-528`).
- A callee that does not resolve to exactly one reachable target produces no edge
  (`theloom/extraction/resolution.py:431-451`).
- Only bare-identifier calls become call edges; `obj.method()` produces no call record in
  any supported language (`theloom/extraction/treesitter.py:384-396`).
- Line numbers are 0-based in code and 1-based in the graph, and build-then-parse is the
  identity (`theloom/extraction/encoding.py:17-23`, `:117-134`, `:166-198`).
- Extraction output is deterministic for a given tree — every level of the walk sorts
  (`theloom/extraction/treesitter.py:1203-1211`).
- A document contributes at most 50 links, and the drop is reported rather than silent
  (`theloom/extraction/doclinks.py:74`, `:233-257`).
- A non-code file record declares that nothing parsed it: extractor `file-scan`, never
  `tree-sitter` (`theloom/extraction/treesitter.py:1214-1237`).

#### 2.14 Document ingestion — `theloom/documents` (group `theloom-documents`)

Turns an external artifact — a file, a directory, a raw string or a URL — into embedded,
searchable chunk rows living inside the same FalkorDB instance as the graph. It owns the
pipeline end to end: an extension allowlist and per-format parsers that normalise every
input into one block shape, a three-phase size-aware chunker with sentence overlap and an
atomic-block escape hatch, an SSRF-hardened fetcher for remote sources, the declared chunk
metadata shape, and event-sourced persistence into a dedicated per-prefix chunk graph. The
package knows nothing about the CLI: it raises its own exception taxonomy and lets the
operations layer map that onto typed error codes structurally.

Key files: `theloom/documents/ingestion.py` (orchestrator plus the six verbs),
`theloom/documents/chunker.py`, `theloom/documents/chunkstore.py`,
`theloom/documents/parsers.py`, `theloom/documents/ssrf.py`,
`theloom/documents/metadata.py`.

Conventions: three-phase chunking with an atomic-block escape hatch; format dispatch to a
single block normal form; chunk storage as a subclass of the shared store chassis rather
than a second store; a deny-by-default egress guard re-validated on every redirect hop; a
structural error taxonomy translated at the operations boundary; deferred heavy imports at
the call site.

Invariants worth knowing:
- Chunk writes are event-sourced through the store's shared commit primitive
  (`theloom/documents/chunkstore.py:103`, `:207-222`).
- Chunks live in one per-prefix chunk graph, global across knowledge graphs — which is why
  the document verbs take no graph parameter
  (`theloom/documents/chunkstore.py:56`, `:69-78`).
- Chunk event payloads carry coordinates, never chunk text
  (`theloom/documents/chunkstore.py:232-251`).
- `sourceId` is a deterministic sha256 prefix of the resolved path or URL
  (`theloom/documents/ingestion.py:51-57`, `:180`, `:259`).
- Reingest preserves chunk identity and skips unchanged chunks
  (`theloom/documents/ingestion.py:316-367`).
- A chunk's `contentHash` covers its overlap prefix, not just its own body
  (`theloom/documents/chunker.py:205-229`).
- Every fetch hop requires all resolved addresses to be globally routable
  (`theloom/documents/ssrf.py:39-95`).
- Embedding failure never blocks chunk persistence; the reason is stored on the chunk
  (`theloom/documents/ingestion.py:60-69`, `theloom/documents/metadata.py:62`).
- Ingest enforces hard resource ceilings before parsing — 50 MB per file, 10 MB per HTTP
  response, 30 s timeout, 5 redirects (`theloom/documents/ingestion.py:27`, `:171-175`,
  `theloom/documents/ssrf.py:23-25`).

#### 2.15 Foraging signals — `theloom/exploration` (group `theloom-exploration`)

The library behind the explore-frontier composite. It turns a graph's connected components
into ranked "where should I look next" recommendations by computing four independent
normalized signals — age staleness, bridging potential, coverage gap and an exploration
bonus — fusing them with a renormalizing weighted average, layering a patch-leaving policy
on top, and running six anti-pattern guards over aggregated exploration state.

Key files: `theloom/exploration/guards.py` (486 lines),
`theloom/exploration/composite_signals.py`, `theloom/exploration/exploration_state.py`,
`theloom/exploration/coverage_gap.py`.

Conventions: store-agnostic pure signals over caller-supplied regions; optional-returning
detectors aggregated by a fixed-order runner; frozen dataclass configs with module-level
defaults; a two-tier detection with an embedding-free fallback.

Invariants worth knowing:
- Every signal score is clamped to [0,1]
  (`theloom/exploration/composite_signals.py:53`, `:89`,
  `theloom/exploration/coverage_gap.py:157`).
- Absent signals are dropped and weights renormalized, never treated as zero
  (`theloom/exploration/composite_signals.py:70-88`).
- Region identity is the smallest id in sorted order
  (`theloom/exploration/exploration_state.py:89-97`).

#### 2.16 Structural fingerprints — `theloom/reification` (group `theloom-reification`)

The one shared implementation of Weisfeiler-Leman ego fingerprinting. Each node reduces to
a short hash of its rooted neighbourhood up to a bounded depth, so nodes whose local
structure looks alike collapse to one digest and can be bucketed into pattern groups. The
package exists to de-duplicate that hashing: pattern reification, the entity proposer and
component signatures all import from here, so their fingerprints stay bit-identical by
construction.

Key file: `theloom/reification/fingerprint.py` (the whole implementation).

Conventions: colour refinement by depth-indexed recursion; a canonical tagged string then
one truncated-digest chokepoint; a caller-supplied memoization cache instead of module
state.

Invariants worth knowing:
- Fingerprints are invariant to adjacency ordering
  (`theloom/reification/fingerprint.py:49-53`).
- Depth is clamped at both public entry points
  (`theloom/reification/fingerprint.py:93`, `:133`).
- Output order and size are deterministic
  (`theloom/reification/fingerprint.py:150-161`).

#### 2.17 Rules and gates — `theloom/verification` (group `theloom-verification`)

The rule layer: a store-agnostic library of predicates that decide whether a graph, or a
single proposed mutation, keeps the model's structural promises. `checks.py` holds the
read-side guards, the five builtin invariants and the shared three-colour cycle detector;
`guards.py` holds the mutation gate that entity and relation creation call before writing,
and is the only module here that imports the store; `metrics.py` holds the coverage and
coupling generators shared by the capability command and the DSL, placed below the
operations layer on purpose; `capability_spec.py` layers a fluent DSL whose violations carry
suggested actions that feed proposal generation; `propagation.py` implements AC-3 arc
consistency over the entity-type domain.

Key files: `theloom/verification/checks.py`, `theloom/verification/guards.py`,
`theloom/verification/metrics.py`, `theloom/verification/capability_spec.py`,
`theloom/verification/propagation.py`.

Conventions: predicate tables as the public registry of rules; uniform violation envelopes;
one verdict shared across write and read surfaces through a message helper; store-optional
predicates over wire dicts; deterministic iteration order as part of the output contract;
default-argument binding to freeze loop variables in generated closures.

Invariants worth knowing:
- Guards abstain when a field is absent rather than reporting a violation
  (`theloom/verification/checks.py:42-45`, `:61-62`, `:79-80`, `:95-98`).
- The polarity partition is enforced on write and mirrored on read from one message
  (`theloom/verification/guards.py:64-71`, `theloom/verification/checks.py:24-28`).
- Entity gates warn; relation gates block
  (`theloom/verification/guards.py:41-52` versus `:55-78`).
- Retracted entities read back but cannot become relation endpoints
  (`theloom/verification/guards.py:81-101`).
- `noCausalCycles` exempts edges whose target is a loop record — a named feedback loop is an
  intentional cycle (`theloom/verification/checks.py:256-266`).
- `find_cycle_nodes` never leaves the supplied node set
  (`theloom/verification/checks.py:187`, `:194-195`, `:207-210`).
- The AC-3 worklist is LIFO and that choice is part of the wire contract, because it decides
  `revisionsCount` and which variable is named on inconsistency
  (`theloom/verification/propagation.py:104`, `:110-120`).
- The shared capability generators live in verification so operations imports downward
  (`theloom/verification/metrics.py:1-9`, consumed at
  `theloom/operations/verification.py:42-44`).

#### 2.18 Prose in, prose out — `theloom/synthesis` (group `theloom-synthesis`)

Turns a knowledge graph into prose and then grades that prose back against the graph. The
spine is Plan-Traverse-Realize: the planner picks a query-relevant subgraph, decomposes the
question and groups the result into ordered regions; the traverser walks those regions
attaching Viterbi confidence, source passages and an append-only provenance trail; the
linearizer topologically orders each region and the realizer renders it as narrative,
outline, evidence map, causal chain, proposal or raw. A fidelity module then grades the
produced text against the graph it came from. Two supporting concerns live here: the single
resolution point for an optional completion client, and the sanitize-and-tag
prompt-injection defence every LLM call site uses. A second, unrelated subsystem —
counterexample-guided inductive synthesis over graph structures — shares only the package
name.

Key files: `theloom/synthesis/planner.py`, `theloom/synthesis/traverser.py`,
`theloom/synthesis/realizer.py`, `theloom/synthesis/fidelity.py`,
`theloom/synthesis/cegis.py`.

Conventions: a staged pipeline over plain wire dictionaries; an optional LLM with a
deterministic template fallback at every call site; JavaScript-semantics parity shims
because outputs are pinned by tests; prompt-injection defence by sanitizing inputs then
wrapping them in a data tag; dependencies narrowed to a Protocol or a bare callable, never
a concrete store; one core number as the shared ordering currency.

Invariants worth knowing:
- Synthesis output is fully deterministic when no LLM is configured
  (`theloom/synthesis/llm.py:215-218`, `theloom/synthesis/realizer.py:318-321`).
- The seeded PRNG is bit-exact 32-bit, so a seed determines the candidate graph exactly
  (`theloom/synthesis/generator.py:28-60`).
- CEGIS verification touches no store; only a successful commit does
  (`theloom/synthesis/cegis.py:129-163`, `:211-257`).
- The refinement loop always terminates (`theloom/synthesis/cegis.py:382-418`).
- The fidelity composite index is a weighted harmonic mean that zeroes when either side
  fails (`theloom/synthesis/fidelity.py:351-359`).
- Provenance is append-only and sealed at finalize
  (`theloom/synthesis/traverser.py:42-86`).
- Selection depth and breadth are hard-capped regardless of caller input — depth 10,
  1,000 entities, 10 anchors (`theloom/synthesis/selector.py:26`, `:166-172`).
- Linearization topologically orders causal edges only; cyclic and non-causal nodes append
  by core number (`theloom/synthesis/linearizer.py:17`, `:32-52`).
- Only document-provenance entities can resolve to a source chunk, and a miss is not an
  error (`theloom/synthesis/links.py:32-54`).

#### 2.19 Computer algebra — `theloom/symbolic` (group `theloom-symbolic`)

An in-process algebra engine wrapping SymPy behind a single total function that looks a
string operation name up in a 21-entry dispatch table, runs the handler under a signal
watchdog, and returns a JSON-serializable envelope instead of raising. It owns all
expression parsing (LaTeX, then transformed sympify, then raw sympify), all formatting, and
a chain interpreter that pipes each step's result into the next.

Key file: `theloom/symbolic/core.py` (1,025 lines — the entire engine).

Conventions: registry-table dispatch over string names; two-level dispatch with a
sub-operation string; function-local SymPy imports; a cascading parse fallback chain; a
total-function boundary of watchdog plus error envelope.

Invariants worth knowing:
- `core.run` never raises for main-thread callers
  (`theloom/symbolic/core.py:1001-1022`).
- The watchdog timeout is clamped to 1–120s and restores prior signal state
  (`theloom/symbolic/core.py:1008-1009`, `:1023-1025`).
- One alarm covers an entire chain, not each step
  (`theloom/symbolic/core.py:1016`, `:931-932`).
- Handler results cross the boundary as strings, never as SymPy objects
  (`theloom/symbolic/core.py:61-75`).

#### 2.20 The visualization payload — `theloom/viz` (group `theloom-viz`)

Turns a live graph into a shippable payload for the Tapestry app. It chooses which slice to
show (full, ego, causal, typed or search, optionally bounded to a system-time instant),
optionally attaches analytics, the event stream for client-side replay, and a 2-D embedding
projection with clusters, validates the whole payload against a versioned schema, and emits
it through one of three transports: raw JSON, a self-contained HTML page carrying the
committed frontend build, or a read-only REST service. It computes almost nothing itself.

Key files: `theloom/viz/bundle.py`, `theloom/viz/schema.py`, `theloom/viz/scope.py`,
`theloom/viz/html.py`, `theloom/viz/serve.py`.

Conventions: one assembler behind three transports; optional heavy dependencies behind
function-local imports; sentinel substitution into a committed single-file build; a wire
schema generated from the Python model for cross-language drift tests.

Invariants worth knowing:
- Every payload leaves the assembler as a validated dump
  (`theloom/viz/bundle.py:146-165`, contract at `theloom/viz/schema.py:82-89`).
- Injected JSON can never terminate the template script block
  (`theloom/viz/html.py:33`).
- A missing or unbuilt frontend template fails as a typed configuration error
  (`theloom/viz/html.py:28-44`).
- Live-mode HTTP status is a typed-code table lookup, never prose matching
  (`theloom/viz/serve.py:28-35`, `:96-103`).

### 2.B The frontend workspace

#### 2.21 App shell — `tapestry/src` (group `tapestry-src`)

The outermost layer of the single-page app. `main.tsx` is the entire bootstrap: one root
render wrapping the app in a bundle provider, so nothing below ever renders without data in
hand. `App.tsx` is chrome and router in one component — a fixed header with the brand mark,
title, counts and bi-temporal note, a tablist of the five views, a live-server chip with a
graph switcher, a help trigger and a theme radiogroup — plus four mount-time effects wiring
the app to browser globals.

Key files: `tapestry/src/App.tsx`, `tapestry/src/main.tsx`,
`tapestry/src/views/HelpOverlay.tsx`, `tapestry/src/App.css`.

Conventions: header composite widgets as single tab stops with roving focus; shell-owned
modal state with a child-owned focus trap; browser globals wired in mount effects with
explicit teardown.

Invariants worth knowing:
- Nothing renders before a bundle exists
  (`tapestry/src/main.tsx:6-10`, `tapestry/src/App.tsx:170`).
- Hash restore runs before the hash writer's first write
  (`tapestry/src/App.tsx:233-245`).
- The URL hash is replaced, never pushed (`tapestry/src/App.tsx:248-258`).

#### 2.22 Shared kernel — `tapestry/src/lib` (group `tapestry-src-lib`)

Everything the four canvas views and the shell need but no single view owns: bundle
acquisition from one of three sources with a typed failure naming the branch that failed;
live-mode detection and its small REST client; a context that loads once, memoizes the graph
model once, gates children until data exists and exposes a two-shape failure surface; canvas
interaction primitives (drag thresholds, node dragging, wrapped labels); and the export
paths.

Key files: `tapestry/src/lib/data.ts`, `tapestry/src/lib/BundleContext.tsx`,
`tapestry/src/lib/exportSvg.ts`, `tapestry/src/lib/nodeLabels.ts`,
`tapestry/src/lib/dragNodes.ts`.

Conventions: a pure decision core with a thin impure canvas/DOM edge; mode detection by
parsed shape, never by sentinel literal; load once, gate, then share through a context whose
hooks throw outside it; per-graph namespaced storage funnelled through one write primitive.

Invariants worth knowing:
- Every load failure raises a typed error naming its source
  (`tapestry/src/lib/data.ts:69-101`).
- A failure after data is up keeps the data and reports the loaded graph, not the requested
  one (`tapestry/src/lib/BundleContext.tsx:99`).
- Live mode is detected by the parsed marker's shape, never by the sentinel literal
  (`tapestry/src/lib/live.ts:20-28`, `tapestry/src/lib/data.ts:59-63`).

#### 2.23 Shared state — `tapestry/src/state` (group `tapestry-src-state`)

All cross-view UI state and its URL projection. One flat store holds the active view, theme,
selection, filters, path-tool mode and endpoints, the isolated loop, the scrubber triple and
the brushed id set, with one narrow setter per field. A second module turns a chosen subset
into a shareable location hash and back, through a single code path that both initial-mount
restore and saved-view application use.

Key files: `tapestry/src/state/store.ts`, `tapestry/src/state/urlHash.ts`.

Conventions: a flat single-slice store with one setter per field; the URL hash as the
shareable projection of view state; `null` as the universal unset sentinel.

Invariants worth knowing:
- Hash parsing is total: a malformed or foreign hash yields an empty patch, never a throw
  (`tapestry/src/state/urlHash.ts:16-21`).
- Applying a hash is a partial merge: absent keys leave state untouched
  (`tapestry/src/state/urlHash.ts:34-37`).
- One path keeps the address bar and the store in step, history first
  (`tapestry/src/state/urlHash.ts:30-38`).

#### 2.24 Visual contract — `tapestry/src/design` (group `tapestry-src-design`)

One token file defining two complete themes, a TypeScript mirror of the model's 19 entity
types with the accessor that turns a type name into a token reference, and the three-line
bridge resolving a tri-state theme setting to a concrete attribute on the document element.
Everything downstream reads colour, type ordering and typography through this layer, so a
theme swap is a single attribute write.

Key files: `tapestry/src/design/tokens.css` (289 lines),
`tapestry/src/design/palette.ts`, `tapestry/src/design/theme.ts`.

Conventions: custom-property indirection for entity colour; attribute-scoped dual-theme
override; an as-const tuple mirroring the backend enum; rationale recorded beside the value.

Invariants worth knowing:
- Every entity type has a token in both themes
  (`tapestry/src/design/palette.ts:35-39`, `tapestry/src/design/tokens.css:99-118`).
- The type tuple mirrors the Python enum in enum order
  (`tapestry/src/design/palette.ts:1-27`, mirrored source `theloom/model.py:56-75`).
- Identity is never encoded by colour alone
  (`tapestry/src/design/tokens.css:8-17`, `:133-135`).

#### 2.25 Graph Explorer — `tapestry/src/views/explorer` (group `tapestry-src-views-explorer`)

The force-directed WebGL weave that is the default reading surface. It compiles a bundle
into a graph model whose attributes encode every visual channel (fill by type, size by
degree, tint by relation family, width by strength), settles it for three seconds, then
hands the reader fuzzy search, non-destructive facet filters, a shortest-path tool, a detail
panel, a legend, a minimap, keyboard walking, image export and per-graph saved views.

Key files: `tapestry/src/views/explorer/buildGraph.ts`,
`tapestry/src/views/explorer/Explorer.tsx`, `tapestry/src/views/explorer/filters.ts`,
`tapestry/src/views/explorer/pathMode.ts`, `tapestry/src/views/explorer/layout.ts`.

Conventions: reducer-layer compositing for non-destructive interaction; refs for the render
loop and React state for the React tree; pure calculation modules paired with thin view
components; scale-gated degradation with explicit node-count thresholds.

Invariants worth knowing:
- Filtering hides via reducers and never mutates the model
  (`tapestry/src/views/explorer/filters.ts:45-61`).
- Entities without a confidence score pass every confidence floor
  (`tapestry/src/views/explorer/filters.ts:36-40`).
- An edge is visible only when both endpoints are
  (`tapestry/src/views/explorer/filters.ts:53-58`).

#### 2.26 Overview — `tapestry/src/views/overview` (group `tapestry-src-views-overview`)

A read-only dashboard answering "what shape is this weave in" before any exploration: six
headline tiles, three panels (composition, health, confidence histogram) and a most-central
table whose rows deep-link into the Explorer. Every number comes from one pure pass over the
bundle documents.

Key files: `tapestry/src/views/overview/stats.ts` (111 lines),
`tapestry/src/views/overview/Overview.tsx` (435), `tapestry/src/views/overview/Overview.css`.

Conventions: a pure derivation pass behind a presentational dashboard; job-based colour
tokens paired with icon and label, never colour alone; absent-versus-empty degradation;
print-media CSS as the zero-dependency export path.

Invariants worth knowing:
- Stats read the bundle arrays, never the rendered model, so dangling relations stay
  countable (`tapestry/src/views/overview/stats.ts:5-9`, `:56`, `:63-66`).
- The confidence histogram is exactly ten bins and 1.0 clamps into the last
  (`tapestry/src/views/overview/stats.ts:58`, `:76-77`).
- Unscored entities are excluded from the histogram and reported separately
  (`tapestry/src/views/overview/stats.ts:49-52`, `:71-78`).

#### 2.27 Systems — `tapestry/src/views/systems` (group `tapestry-src-views-systems`)

A causal-loop diagram that re-reads the weave as a systems-dynamics model: it projects the
bundle to its causal slice, colours each edge by polarity on a diverging channel and stamps
a sign glyph at its midpoint, badges every variable carrying a leverage point with its
numbered level, and lists feedback loops in a rail where selecting a row isolates that loop
and unlocks a pulse that travels it in its influence direction.

Key files: `tapestry/src/views/systems/systems.ts`,
`tapestry/src/views/systems/SystemsView.tsx`, `tapestry/src/views/systems/LoopPanel.tsx`.

Conventions: a canvas-free model core with a thin rendering shell; ref-backed reducers keep
the renderer instantiated once; redundant encoding — every new colour channel doubled by a
glyph; degenerate scopes explain themselves instead of rendering blank.

Invariants worth knowing:
- The Systems graph holds only causal edges and the entities they touch
  (`tapestry/src/views/systems/systems.ts:63-107`).
- Loop edge keys resolve through directed out-edges, never undirected lookup
  (`tapestry/src/views/systems/systems.ts:125-128`).
- The flow pulse is a wrapped raised-cosine, so exactly one edge peaks at a time
  (`tapestry/src/views/systems/systems.ts:193-201`).

#### 2.28 Chronicle — `tapestry/src/views/chronicle` (group `tapestry-src-views-chronicle`)

The bi-temporal time-travel view: a second diagram over the same shared model, driven per
instant by a pure client-side replay of the exported event log. One helper reshapes events
into millisecond lookups, one answers which nodes and edges existed at instant *t* and each
node's effective status, and one classifies what changed between two instants as added,
invalidated or changed. It is *read as of T* semantics reimplemented in the browser.

Key files: `tapestry/src/views/chronicle/replay.ts`,
`tapestry/src/views/chronicle/Chronicle.tsx`,
`tapestry/src/views/chronicle/Scrubber.tsx`, `tapestry/src/views/chronicle/EventList.tsx`.

Conventions: a pure replay core behind an impure view shell; time travel as a render-time
projection, not a model edit; state carried by a labelled overlay above the canvas.

Invariants worth knowing:
- Retraction replays as a status flip plus edge closure, never node removal
  (`tapestry/src/views/chronicle/replay.ts:150-162`).
- A node with no creation event is present from the start of the replay
  (`tapestry/src/views/chronicle/replay.ts:231-253`).
- The timeline span is always strictly positive
  (`tapestry/src/views/chronicle/replay.ts:208-210`).

#### 2.29 Semantic Map — `tapestry/src/views/semantic` (group `tapestry-src-views-semantic`)

A scatter plot of the precomputed embedding projection, read as a map of meaning rather than
link structure. It is the only canvas view that runs no layout and draws no edges —
coordinates come straight from the payload, so screen distance encodes semantic distance.
Over the point field it layers convex cluster hulls that track the camera and a freehand
lasso that brushes enclosed points into shared state, which the Explorer then reads as a
highlight layer.

Key files: `tapestry/src/views/semantic/semanticMap.ts` (126 lines),
`tapestry/src/views/semantic/SemanticView.tsx` (710).

Conventions: a pure geometry core split from the rendering shell; stacked overlays with
pointer-events gating; camera-tracking overlays rebuilt on every frame that renders; a
menu-button keyboard equivalent for the pointer-only lasso.

Invariants worth knowing:
- The projection is the layout; no force algorithm ever runs here
  (`tapestry/src/views/semantic/semanticMap.ts:50-64`).
- A point exists only where both a coordinate and an entity exist
  (`tapestry/src/views/semantic/semanticMap.ts:32-48`).
- Hull and lasso geometry is computed in viewport pixels, never graph space
  (`tapestry/src/views/semantic/SemanticView.tsx:248-254`).

#### 2.30 Build and contract toolchain — `tapestry` part 2 (group `tapestry-2`)

No application code: the build, contract and verification toolchain that turns the app into
the single artifact the Python distribution ships. The build is a three-stage chain —
typecheck the two composite TypeScript projects, inline the whole app into one HTML file,
then assert the data sentinel survived bundling before copying the file into the Python
package. A generated JSON Schema is checked into the frontend so JavaScript tooling and
Python tests agree on what the backend emits. Two Playwright configurations partition
end-to-end verification.

Key files: `tapestry/package.json`, `tapestry/scripts/emit-template.mjs`,
`tapestry/vite.config.ts`, `tapestry/schema/bundle.schema.json`,
`tapestry/playwright.live.config.ts`.

Conventions: a single-file bundle handed to Python through a data sentinel; a generated
schema as a three-way wire contract; a fail-fast post-build guard; directory-partitioned
test runners with disjoint scopes.

Invariants worth knowing:
- The build is a three-stage gate: typecheck, then bundle, then emit
  (`tapestry/package.json:8`).
- No template is emitted unless the data sentinel survived bundling
  (`tapestry/scripts/emit-template.mjs:4-8`).
- `theloom/viz/static/tapestry.html` is the only artifact crossing from the Node workspace
  into the Python package (`tapestry/scripts/emit-template.mjs:8`).

#### 2.31 Fixtures and live proof — `tapestry` part 1 (group `tapestry-1`)

Three artifacts that decide what the app is built from and how it is proved. A verbatim
export snapshot of a small development graph is the workspace's single data contract: the
dev build fetches it over HTTP, seven browser specs inject it as page state, a unit test
validates it against the generated schema, and a Python test round-trips it through the
Pydantic model. A single live spec is the counterpart proof for the server path, asserting
the boot chain, the live indicator and graph switching. The lockfile pins the toolchain.

Key files: `tapestry/fixtures/dev-bundle.json`, `tapestry/e2e-live/live.spec.ts`,
`tapestry/package-lock.json`.

Conventions: a golden fixture as the single data contract; capability-partitioned end-to-end
testing where the live project asserts only server-only affordances; render-as-assertion.

Invariants worth knowing:
- The dev fixture is an exact round-trip of the assembler's output
  (`tapestry/fixtures/dev-bundle.json:1-14`).
- Derived sections drop entities on two orthogonal axes: status for analytics, embeddings
  for projection (`tapestry/fixtures/dev-bundle.json:141`, `:292-347`).
- Optional fields are omitted rather than nulled; a ten-key core is always present
  (`tapestry/fixtures/dev-bundle.json:16-34`).

#### 2.32 Browser acceptance — `tapestry/e2e` (group `tapestry-e2e`)

The browser-level acceptance suite. It deliberately does not drive the dev server or mount
components in isolation; it drives the single self-contained HTML artifact the CLI emits,
re-created at setup time from two committed inputs and opened over `file://`. Seven specs
partition the surface: a broad walkthrough of all five views and their signature
interactions, an accessibility gate over every panel in both themes, keyboard and help
semantics, saved views and deep links, pointer semantics, and the export filename contract.

Key files: `tapestry/e2e/smoke.spec.ts`, `tapestry/e2e/a11y.spec.ts`,
`tapestry/e2e/savedviews.spec.ts`, `tapestry/e2e/drag.spec.ts`,
`tapestry/e2e/export.spec.ts`.

Conventions: shipped-artifact fixture injection over `file://`; role and accessible-name
first locators with class names only for visual state; a generated accessibility matrix over
themes and panels; behavioural proof by re-probing the canvas rather than reading app state.

Invariants worth knowing:
- The suite renders through the same substitution as the Python renderer
  (`tapestry/e2e/smoke.spec.ts:18-21`, replicated across the other specs).
- Each spec owns a distinct temporary artifact so parallel specs never clobber each other
  (`tapestry/e2e/smoke.spec.ts:15`, `tapestry/e2e/a11y.spec.ts:21`).
- The accessibility gate is zero serious or critical violations, by construction
  (`tapestry/e2e/a11y.spec.ts:43-45`, applied at `:86-129`).

### 2.C The test suite

#### 2.33 Infrastructure and outer layers — `tests` part 1 (group `tests-1`)

The first sixth carries two things the rest of the suite depends on — the namespaced live
store fixture chain in `conftest.py` and the shared test doubles in `fakes.py` — and then
pins the contracts of the system's outermost and innermost layers at once: the CLI JSON
protocol and its typed error codes, the command registry's single construction path, the
config loader's precedence chain, multi-graph and visualization wire shapes, the composite
framework's never-throw section envelope, the event-sourcing of cross-graph bridges and
document chunks, and the pure algorithmic foundations.

Key files: `tests/conftest.py`, `tests/fakes.py`, `tests/test_consumption.py`,
`tests/test_bridges.py`, `tests/test_chunk_events.py`.

Conventions: a per-test namespaced live store with self-teardown; one shared doubles module
instead of per-module stubs; CLI-surface commands driven through the registry's
`run_handler`; documentation harvested and validated as a machine-checked contract;
concurrency and failure pinned by monkeypatching a named module-level seam; truncation
honesty asserted as arithmetic, not as a message.

Invariants worth knowing:
- Every live-store test is namespaced and leaves the store as it found it
  (`tests/conftest.py:35-45`).
- Documented `loom` invocations must validate against the live CLI input models
  (`tests/test_claude_examples_contract.py:146-160`).
- A chunk write and its event append are one unit, in both failure directions
  (`tests/test_chunk_events.py:179-218`).
- Bridge removal invalidates rather than erases, and an interrupted legacy migration resumes
  without losing or duplicating a document
  (`tests/test_bridges.py:122-140`, `:223-279`).
- A composite section never throws; failure degrades to a data-null envelope
  (`tests/test_composites_framework.py:28-41`, `:100-124`).
- Error codes come from the typed exception hierarchy, never from prose matching
  (`tests/test_cli_io.py:67-85`).
- A truncated consumption answer accounts for every row it dropped, and superseded entities
  leave every consumption read surface
  (`tests/test_consumption.py:263-313`, `:158-171`, `:409-415`, `:533-540`).

#### 2.34 Comprehension and proposal surfaces — `tests` part 2 (group `tests-2`)

The executable specification for the surfaces that compute an answer or propose a change
rather than store a fact, plus the extraction resolver's honesty guards. Four families live
here: pure docker-free algebra pinned with hand-worked goldens (the consumption budget
allocator, the cosine helper's degenerate cases, the embedding state machine, the foraging
signals); the entity-proposal foundation run against a tiny in-memory store double, with the
deduplication gate re-run live to prove it uses the vector index; two composites that were
once registered commands which could never succeed; and the extraction contracts —
doc-to-code link resolution and its four false-positive guards, the single encode/parse
module, include/exclude globs, and bi-temporal retirement of legacy call edges.

Key files: `tests/test_entity_proposer_foundation.py` (612 lines),
`tests/test_epistemic_session.py` (409), `tests/test_extraction_doclinks.py` (382),
`tests/test_enrichment_crawl.py` (370), `tests/test_exploration_foundation.py` (354).

Conventions: the module docstring as the defect narrative; hand-worked golden values with
the arithmetic spelled out; inline duck-typed fakes for the narrow read surface under test;
monkeypatched fault injection proving degradation rather than fabrication; one four-way
session fixture applied uniformly across a query family; round-trip plus verbatim-literal
pinning for the serialization module.

Invariants worth knowing:
- Every populated section keeps its first row, even when that row alone blows the budget
  (`tests/test_consumption_budget.py:40-45`, `:24-37`).
- The dedup gate asks the vector index and matches against every status
  (`tests/test_dedup_gate_search.py:52-76`).
- A composite missing its LLM half still produces real findings and states the gap as a
  boundary; an upstream failure nulls every downstream section instead of fabricating zeros
  (`tests/test_enrichment_crawl.py:60-88`, `:283-301`).
- Symmetric evidence never infers a causal relation
  (`tests/test_enrichment_crawl.py:226-241`).
- A doc-to-code link is drawn only for an unambiguous, code-shaped, callable, non-vocabulary
  mention (`tests/test_extraction_doclinks.py:98-165`).
- `exclude` is applied after `include` and removes the file's records from the graph, not
  just from the file list (`tests/test_extraction_filters.py:48-63`).
- Legacy call-edge retirement is bi-temporal, idempotent and dry-run safe
  (`tests/test_extraction_legacy_calls.py:89-122`).

#### 2.35 Extraction and store — `tests` part 3 (group `tests-3`)

The executable specification for the codebase-extraction pipeline and the FalkorDB store
beneath it. Eight of the twelve files — six `test_extraction_*` modules plus
`test_extraction_filters.py` and `test_falkor_store.py` — tell one continuous story: how
source text becomes a graph, what the resolvers refuse to guess, how a re-run retires what
an older extractor got wrong, and what the store guarantees underneath. The recurring
subject is refusal under uncertainty: an ambiguous call resolves to no edge rather than a
wrong one, a bare word in prose is never a doc link, a term the project writes as a string
value is vocabulary rather than a symbol reference, a legacy `related_to` twin left over
from before call edges were typed is closed out bi-temporally rather than erased, and a
self-model refresh aborts unless the repository really is The Loom. The remaining four files
pin narrow contracts the fixed-repo golden tests cannot isolate on their own: graph-algorithm
details (cycle rotation, loop polarity, path ordering), the generated command catalog's
byte-equality with the registry, the gap-fill composite's commit gate, and the string-format
encoders every reader parses.

Key files: `tests/test_falkor_store.py` (793 lines — store CRUD, status lifecycle, the event
log, bi-temporal version intervals, vector-index readiness),
`tests/test_extraction_units.py` (553 — tree-sitter internals, signatures, docstrings,
rationale comments, git visibility, the golden fixed-repo stats),
`tests/test_extraction_resolution.py` (521 — cross-file import/call/inheritance resolution
and its refusal guards), `tests/test_extraction_doclinks.py` (382 — Markdown-to-code linking
and the vocabulary guard), `tests/test_extraction_rollback.py` (253 — codebase run records
and scoped rollback).

Conventions: parametrized truth tables that spend most of their rows on the negative case;
the regression that motivated a test written into its docstring, magnitude included; golden
fixed-repo assertions with the arithmetic spelled out in comments; round-trip plus
copied-literal pinning for the string-format module; dry-run claims proven by re-reading the
store rather than trusting the response; production dependencies faked at their narrowest
seam rather than replaced with a stand-in service; a throwaway git work tree as the
extraction fixture wherever visibility or diffing matters.

Invariants worth knowing:
- Structural extraction never emits a generic `related_to` edge — the fixture repo's relation
  types are exactly `part_of`, `requires`, `calls` and `references`
  (`tests/test_extraction_resolution.py:451-463`, `tests/test_extraction_units.py:61`).
- No extracted edge points at an entity the extraction did not create, and the check also
  proves the positive types are actually emitted so a silently-dropped type cannot pass
  vacuously (`tests/test_extraction_resolution.py:481-499`,
  `tests/test_extraction_doclinks.py:371-376`).
- An ambiguous name produces no edge at all; an import that names the target overrides the
  ambiguity (`tests/test_extraction_resolution.py:205-241`, `:243-264`).
- The unique-name resolver is guarded by builtin, language and callable kind — the guard that
  exists because 288 Python `len()` calls once resolved to a lone TypeScript `len` constant
  (`tests/test_extraction_resolution.py:339-359`, `:301-307`).
- A term the project writes as a string value is vocabulary, never a doc link — code-shaped,
  backticked and unambiguous is still refused if the spelling collides with a file's own
  string literals (`tests/test_extraction_units.py:146-162`,
  `tests/test_extraction_doclinks.py:231-247`).
- Re-extraction retires the legacy `related_to` call twin bi-temporally: closed out once,
  reported once, zero on the next run, and still readable through an as-of query before the
  retirement (`tests/test_extraction_legacy_calls.py:89-122`, `:142-168`).
- A codebase run's record scopes rollback to exactly the entities that run created — merged-
  into and pre-existing entities survive it, and a dry run writes no record at all
  (`tests/test_extraction_rollback.py:72-103`, `:115-153`).
- A git rename lands as delete-old-path plus add-new-path rather than the new path simply
  appearing, so the old path's file and symbol records are superseded instead of left live
  forever under a path that no longer exists (`tests/test_incremental_update.py:421-441`).
- Version intervals partition system time with no gap and no overlap, so an as-of read always
  returns exactly the document that was live at that instant
  (`tests/test_falkor_store.py:602-636`).
- Full-scan store reads stay complete above FalkorDB's server-side result-set cap
  (`tests/test_falkor_store.py:468-490`).
- `COMMANDS.md` is byte-equal to the registry-generated catalog
  (`tests/test_generate_docs.py:34-40`).

#### 2.36 Write path and model — `tests` part 4 (group `tests-4`)

The executable contract for the write path and the domain model beneath it: the model
itself (19 entity types, 17 relation types, 5 statuses, the confidence-label boundaries and
the whole transition table), then the operations layer above the store — entity CRUD with
revision auto-population and guard-warning observations, merge, bulk import, document
ingestion's error translation, the embedding state machine, trigger dequeue, init and
centrality. Around those: relation semantics end to end, the multi-graph manager and its
bridge registry, and name-first addressing for every entity-addressed read. The stance is
uniform — a contract is what the test asserts by equality on whole output documents, whole
enum inventories and whole transition tables.

Key files: `tests/test_ops_relations.py`, `tests/test_ops_merge.py`,
`tests/test_ops_entity.py`, `tests/test_model.py`, `tests/test_ops_bulk.py`.

Conventions: operations tested at the function seam through their input models;
whole-document equality instead of spot checks; parametrized sweeps over a whole partition;
the module docstring stating the contract and naming the regression it guards;
monkeypatched call counters pinning query shape, never elapsed time.

Invariants worth knowing:
- Polarity belongs to causal relation types only, on every write path
  (`tests/test_ops_relations.py:132`, `:146`, `:321`).
- The verification gate runs before the bridge branch, so `create-relation` refuses
  cross-graph edges (`tests/test_ops_relations.py:204`).
- Deletion retracts by default and the record stays readable
  (`tests/test_ops_entity.py:138`, `tests/test_ops_relations.py:391`).
- Invalid status transitions are refused and `retracted` is terminal
  (`tests/test_model.py:312`, `tests/test_ops_entity.py:211`).
- `merge-entities` is a single atomic contract — union, redirect, supersede, one event — and
  a re-merge is a no-op that does not bump the version
  (`tests/test_ops_merge.py:79-249`, `:303`).
- Every extracted call edge must import; a dropped endpoint is a reported error, not silence
  (`tests/test_ops_bulk.py:205` — the docstring records that 1,270 call edges once vanished
  this way).
- Name and id addressing produce identical results, including for non-active entities
  (`tests/test_name_addressing.py:138`, `:158`).
- Errors are classified by exception class, never by message text
  (`tests/test_ops_documents.py:119`).
- `get-neighbors` hydrates in one batched read, including across bridges
  (`tests/test_ops_relations.py:569`, `:602`).

#### 2.37 Store contract and the vector layer — `tests` part 5 (group `tests-5`)

The specification for the two places where the architecture's promises are invisible in the
source and only provable by running. On the store side: read-port conformance across two
adapters from one behaviour suite, bi-temporal as-of reads that resurrect the version live
at a bound, mutation/event atomicity under four injected failure points, the event-log
repair path, and server-side filter pushdown proved equivalent to a Python oracle across a
26-case matrix crossed with three limits. On the semantic side: the one search core every
caller shares, the hybrid-ranking stages as pure functions with hand-derived orderings,
content-hash skip on re-embed, WL fingerprint goldens, and the composites that must account
for every write they attempt.

Key files: `tests/test_read_port.py` (595 lines), `tests/test_store_pushdown.py` (543),
`tests/test_store_atomicity.py` (466), `tests/test_semantic_perf.py` (497),
`tests/test_self_improve.py` (400).

Conventions: one conformance suite parametrized across every adapter; a Python oracle as the
semantics reference for a pushed-down query; fault injection at a class-level monkeypatched
seam; deterministic embeddings via an injected fake on two seams; expected values derived by
hand in the docstring; call-count spies standing in for performance assertions.

Invariants worth knowing:
- Every read-port adapter answers the same way, down to ordering
  (`tests/test_read_port.py:71`, `:174`, `:271`, `:294`, `:337`, `:378`).
- An as-of read returns the version that was live at the bound, not the present filtered
  (`tests/test_read_port.py:458-565`).
- A mutation and its event append are one unit — neither half survives alone — and an
  unrepairable log gap is named in a typed error
  (`tests/test_store_atomicity.py:92`, `:192`, `:313`, `:384`).
- Server-side filter pushdown is exactly equivalent to the Python filter path, and `limit`
  reports the untruncated total counting only true matches
  (`tests/test_store_pushdown.py:219`, `:464`, `:482`).
- Vector search never full-scans and never trusts the engine's window order
  (`tests/test_semantic_perf.py:57`, `:109`).
- A non-active entity keeps its embedding, so every search filters by status itself
  (`tests/test_semantic_search_core.py:52`, `tests/test_semantic_perf.py:134`).
- `embed_entities` skips unchanged content by hash unless explicitly forced
  (`tests/test_semantic_perf.py:321`, `:344`).
- Auto-apply accounts for every write: reported, rolled back, or reported as stranded
  (`tests/test_self_improve.py:57`, `:173`).
- Retraction leaves no live trace: the entity's vector goes with it
  (`tests/test_store_atomicity.py:457`).

#### 2.38 Visualization, work memory and leaf units — `tests` part 6 (group `tests-6`)

Three subsystems that share a testing method rather than a subject. First, the whole
visualization and export pipeline walked stage by stage: scope resolution and its typed
refusals, the three optional sections, the bundle assembler with its degree-ranked
truncation, the as-of bound, HTML sentinel injection and escaping, the FastAPI serve layer
exercised in-process, and two drift guards holding checked-in artifacts against the live
model and the built app. Second, the work-memory feedback loop — usage evidence, citation
edges, half-life decay into a preferred/contested/dead-end verdict, and staleness detection
over a stored file fingerprint. Third, the leaf units nothing else pins: source-passage
resolution, the synthesis helper math and the verification metric generators.

Key files: `tests/test_work_memory.py` (485 lines), `tests/test_synthesis_units.py` (245),
`tests/test_synthesis_source_passages.py`, `tests/test_viz_bundle.py`,
`tests/test_viz_serve.py`.

Conventions: a deterministic stub embedder in place of a downloaded model; guardrail
thresholds injected by monkeypatching module constants; optional-extra gating via
`pytest.importorskip`; committed-artifact drift guards that carry their own regeneration
hint; records written by hand at exact ages instead of a mocked clock; registry handler and
in-process HTTP client invocation rather than a subprocess or a bound port.

Invariants worth knowing:
- `record-outcome` is all-or-nothing: no evidence record survives a failed citation write
  (`tests/test_work_memory.py:184`, `:213`).
- One outcome is one vote: duplicate citations collapse to a single edge
  (`tests/test_work_memory.py:196`, `:358`).
- Citation weight decays by an exact half-life measured against the supplied `asOf`
  (`tests/test_work_memory.py:262`, `:291-293`).
- An as-of bound reconstructs the graph as it stood, including edges retired since
  (`tests/test_viz_asof.py:18`, `:37`).
- Analytics and semantic sections are never recomputed as-of; they self-label
  `temporalScope: current` (`tests/test_viz_asof.py:107`, `:124-129`).
- Optional bundle sections are omitted, never emitted empty
  (`tests/test_viz_bundle.py:28`, `:40`).
- Bundle JSON is injected at a sentinel and escaped against script-close
  (`tests/test_viz_html.py:22`, `:28-33`).
- The committed JSON Schema and dev fixture must equal what the Pydantic model emits
  (`tests/test_viz_schema_drift.py:21`, `:29`).
- Typed error codes surface as fixed HTTP statuses with the code in the body
  (`tests/test_viz_serve.py:32`, `:51-57`).
- Source passages resolve only a document-provenance `externalRef`, and degrade to empty
  rather than guessing (`tests/test_synthesis_source_passages.py:79-101`).

### 2.D Fixtures

#### 2.39 Python sample service — `tests/fixtures/repo/src` (group `tests-fixtures-repo-src`)

A three-file miniature banking service that exists only to be parsed, never imported. In
about 50 lines it presents one instance of every construct the extractor must recognise: a
decorated dataclass with a method, a module-level factory, a module constant read by two
functions, typed functions with and without docstrings, a stdlib import, a package-qualified
cross-file import, intra- and cross-module call sites, and rationale comments.

Key files: `tests/fixtures/repo/src/models.py`, `tests/fixtures/repo/src/service.py`,
`tests/fixtures/repo/src/policy.py`.

Conventions: a layered sample service with exactly one cross-module import; one instance per
construct, never two; near-miss symbols as negative fixtures for the doc linker.

Invariants worth knowing:
- Rationale comments bind to the innermost enclosing symbol, else to the file
  (`tests/fixtures/repo/src/policy.py:8`, `tests/fixtures/repo/src/service.py:17`).
- The sibling import is package-qualified, so the fixture must be rooted one level up
  (`tests/fixtures/repo/src/service.py:3`).
- One name is deliberately both a callable and the string it returns
  (`tests/fixtures/repo/src/policy.py:12-14`).

#### 2.40 Non-Python fixture surface — `tests/fixtures/repo` (group `tests-fixtures-repo`)

The other half of the sample repository: two Markdown documents that pin doc-link resolution
(one that must produce links, one whose every mention must produce none), a TypeScript entry
point and a JavaScript helper exercising cross-language import and call resolution, and a
README plus a stylesheet proving non-code text files still become graph roots. Their exact
content is a contract — extraction over this directory is asserted to yield fixed counts.

Key files: `tests/fixtures/repo/docs/glossary.md`,
`tests/fixtures/repo/docs/architecture.md`, `tests/fixtures/repo/lib/index.ts`,
`tests/fixtures/repo/lib/helper.js`, `tests/fixtures/repo/styles/tokens.css`.

Conventions: a negative-case fixture surface engineered to produce no links; each trap
stating its own contract inline; a miniature cross-language module graph.

Invariants worth knowing:
- One name is defined twice on purpose, so no mention of it may resolve
  (`tests/fixtures/repo/lib/index.ts:22`, `tests/fixtures/repo/lib/helper.js:5`).
- The glossary must contribute zero links (`tests/fixtures/repo/docs/glossary.md:3-14`).
- The architecture document yields exactly four links and two refusals
  (`tests/fixtures/repo/docs/architecture.md:3-10`).

#### 2.41 Snapshot seed — `tests/fixtures/multi` (group `tests-fixtures-multi`)

A four-file snapshot seed, the only multi-graph fixture for folder import. It encodes three
named graphs plus a reserved sidecar holding two cross-graph bridges, wired into a chain. It
is deliberately tiny so tests can assert exact counts and byte-exact document equality.

Key files: `tests/fixtures/multi/_bridges.json`, `tests/fixtures/multi/default.json`,
`tests/fixtures/multi/research.json`, `tests/fixtures/multi/systems.json`.

Conventions: underscore-prefixed sidecar files carrying non-graph records; a three-graph
bridge chain as a minimal traversal fixture.

Invariants worth knowing:
- Fixture documents are the byte-exact expected output, not merely valid input
  (`tests/fixtures/multi/_bridges.json:2-29`).
- Bridge endpoints must resolve to nodes in sibling files
  (`tests/fixtures/multi/_bridges.json:5-6`, `:13-14`, `:18-19`, `:26-27`).
- Every graph file carries the full triple, empties included
  (`tests/fixtures/multi/systems.json:18-19`).

### 2.E Declarations and design record

#### 2.42 Declaration surface — repo root part 1 of 7 (group `repo-root-1`)

Eleven root files that state what The Loom is, what it is built from, how it is run and
gated, what its words mean, and how to report a hole in it, plus the two executable scripts
under `scripts/`. None of the eleven is imported by the package. `pyproject.toml` is the
single manifest: the runtime dependency set with conservative floors (`falkordb` carries
none at all), two console entry points bound to the same callable, and the configuration for
all three quality gates. `docker-compose.yml` declares the one FalkorDB service architecture
invariant 1 depends on, its persistence path and result-set cap each commented with the
incident that produced it. `CLAUDE.md`, `CONTRIBUTING.md`, `README.md` and `STACK.md` restate
the same six architecture invariants for four different audiences; `COMMANDS.md` is the
machine-generated catalog; `CONTEXT.md` is the ubiquitous-language glossary, most terms
carrying an explicit avoid-list; `SECURITY.md` draws the repo's only stated trust boundary —
a local-first CLI against a trusted FalkorDB, `loom serve` read-only on localhost with no
authentication. The two `scripts/` files are the only executable code here, reaching past the
CLI into `MultiGraph` to seed live-mode demo graphs and a synthetic benchmark graph. Since the
previous edition, `README.md` stopped inlining a description of the three shipped Claude Code
skills and now delegates it to a three-row table pointing at `examples/` (§2.46).

Key files: `pyproject.toml`, `docker-compose.yml`, `CONTRIBUTING.md`, `CONTEXT.md`,
`SECURITY.md`, `scripts/gen_bench_graph.py`.

Conventions: a generated artifact committed and pinned by a drift test; architecture
invariants restated per audience instead of linked to one copy; optional dependency extras
keeping the core install thin; destructive dev scripts binding their target graph at compile
time; ubiquitous language recorded as a term plus an explicit avoid-list; configuration
comments recording the incident that produced the setting; local seed scripts driving the
store through `MultiGraph` rather than the CLI; skill documentation delegated from the README
to a per-skill guide outside this group's files.

Invariants worth knowing:
- `COMMANDS.md` is generated from the registry and a test fails when it drifts
  (`COMMANDS.md:3`, enforced at `tests/test_generate_docs.py:34-40`).
- The green-main gate is four commands, and `ruff format --check` — not just `ruff check` —
  is one of them (`CONTRIBUTING.md:36-44`).
- FalkorDB persists to `/var/lib/falkordb/data` (not `/data`) and runs with
  `RESULTSET_SIZE` uncapped, each a comment naming the incident that forced it
  (`docker-compose.yml:10-19`).
- `scripts/` is linted but sits outside the type gate and the test suite, and nothing in the
  package imports it (`pyproject.toml:76`, `:84`, `:92`).
- The live-mode seed refuses to delete a graph matching the caller's configured default even
  if it collides with a demo name; the benchmark generator's delete of `tapestry-bench`
  carries no such guard (`scripts/seed_live_dev.py:25` vs
  `scripts/gen_bench_graph.py:162-164`).
- The benchmark generator seeds no embeddings and writes relations through batched
  `store.create_relations` rather than `bulk_import`, avoiding `bulk_import`'s per-relation
  dedup read at 100k-relation scale (`scripts/gen_bench_graph.py:16-18`, `:36-43`).
- Dependency floors are conservative and unpinned; `uv.lock` alone is the reproducibility
  artifact (`pyproject.toml:19-20`, `:22`).
- `mypy --strict` covers `theloom` only and treats nine libraries, including the FalkorDB
  client itself, as untyped (`pyproject.toml:81-88`).

#### 2.43 Dependency closure — repo root part 2 (group `repo-root-2`)

`uv.lock` alone: the resolved, digest-pinned dependency closure that turns the loose version
floors in `pyproject.toml` into an exact, byte-verifiable install. 4,908 lines holding 187
package blocks (183 distinct names; `llvmlite`, `numba`, `numpy` and `scipy` each appear
twice as marker-forked variants) and 2,826 sha256-digested distribution records, every one
served from a single index. It contains no code and nothing imports it, but it decides what
every import inside `theloom/` resolves to at runtime.

Key file: `uv.lock` (its declared inputs are mirrored verbatim into the metadata block at
`uv.lock:4258-4293`).

Conventions: a marker-forked universal lockfile serving every supported interpreter and
platform; a single-registry digest-pinned supply chain; a platform-gated GPU stack behind
`sys_platform` markers; optional feature surfaces carved out as extras plus a dev group.

Invariants worth knowing:
- Every locked artifact is digest-pinned to a single index; the sole non-registry source is
  the editable root project (`uv.lock:899-901`, `:4214`).
- Marker forks partition the 20-entry marker matrix exhaustively and disjointly
  (`uv.lock:4-25`, `:2168-2179`, `:3874-3877`).
- The lock restates `pyproject.toml`'s declarations, which is what makes drift detectable
  (`uv.lock:4215-4239`, `:4259-4283`).
- The document-AI stack is non-optional: a default sync installs torch, transformers and
  onnxruntime (`uv.lock:691-700`, `:816-846`).

#### 2.44 Design record — `docs` (group `docs`)

The repository's written rationale layer: where decisions that code cannot explain are
recorded before or alongside the code that implements them. Three kinds of record live here.
Two approved design specs fix a subsystem's contract before implementation — the Tapestry
visualization surface and the `/map-codebase` architecture-map skill, each with purpose,
architecture, data contract, CLI surface, error-code table and out-of-scope list. One
numbered ADR records a decision that is a deliberate absence rather than a feature:
entity-to-chunk provenance pointers are soft references across a store boundary, with the
alternatives rejected and the consequences of accepting dangling pointers spelled out. One
benchmark report closes the loop on the Tapestry spec's 50k-node ambition with measured
numbers, a reproduction recipe, and honest caveats where targets were missed.

Key files: `docs/design/2026-07-11-loom-visualization-design.md` (274 lines),
`docs/design/2026-08-03-map-codebase-design.md` (205),
`docs/adr/0001-soft-chunk-pointers.md` (85), `docs/benchmarks/tapestry-scale.md` (91).

Conventions: every decision argued against the numbered architecture promises rather than
against local rules; a dated verified-constraints preamble separating checked fact from
design intent; failure behaviour tabulated before implementation; negative space written
down — rejected options, out of scope, missed targets; benchmarks as reproducible reports
with a checked-in generator.

Invariants worth knowing:
- A dangling entity-to-chunk pointer yields no passage, never an error
  (`docs/adr/0001-soft-chunk-pointers.md:34-50`).
- No sidecar may track cross-graph pointers; one transactional store forbids it
  (`docs/adr/0001-soft-chunk-pointers.md:17-24`, `:65-68`).
- Scale numbers are reported benchmarks; no wall-clock assertion may enter CI
  (`docs/benchmarks/tapestry-scale.md:4-6`, `:19-20`, `:88-90`).
- The payload is a versioned contract pinned across Python and TypeScript by a drift test
  (`docs/design/2026-07-11-loom-visualization-design.md:84-86`).
- The visualization surface adds no store and never writes back to the graph
  (`docs/design/2026-07-11-loom-visualization-design.md:67-68`, `:49-56`).
- Map re-runs supersede only the written layer; structural churn belongs to incremental
  re-extraction (`docs/design/2026-08-03-map-codebase-design.md:146-155`, `:102-105`).
- Enrichment attempted with nothing verified halts the run
  (`docs/design/2026-08-03-map-codebase-design.md:178-179`, `:119`).

#### 2.45 Map deliverables — `docs/architecture` (group `docs-architecture`)

The committed output of the `/map-codebase` pipeline: two prose projections of the graph
plus one machine-readable run record. Nothing in the Python package or the frontend imports
these files and no test or CI job references them; they exist for human reviewers, for
coding agents, and for the next mapping run. `ARCHITECTURE-MAP.md` is this walkthrough —
front matter pinning repo, commit, graph and mode; an executive overview with a stats
table; one subsection per module group written to a fixed three-slot template; load-bearing
modules ranked by degree with a per-row justification and by betweenness in prose; a
verdict-annotated cycle table; a communities-versus-directories reading that treats its own
null result as the finding; a ranked risk register; open seams; and a coverage section that
declares what was not covered as loudly as what was. `QUERYING.md` is the agent-facing
recipe sheet: graph name and commit, the naming conventions needed to address records by
name instead of id, the module-group identifiers, one runnable `loom` invocation per
question class with its typical result shape declared, and a copy-pasteable agent hook.
`map-manifest.json` is the run record the next invocation reads as its incremental
baseline. A fourth output, `codebase-map.html`, is generated beside them and deliberately
left untracked. No program consumes any of the three tracked files except the pipeline that
writes them and, for the manifest specifically, the next run itself.

Key files: `docs/architecture/ARCHITECTURE-MAP.md`, `docs/architecture/QUERYING.md`,
`docs/architecture/map-manifest.json`.

Conventions: a generated map plus manifest as a re-run contract, with every open question
routed back to whichever of the two files actually answers it; a fixed three-slot subsystem
template that mirrors the four written-layer record types, with risks deliberately promoted
out of the per-section template into one ranked global register; the section heading doing
double duty as the literal query key a reader pastes into `list-entities`; algorithmic
output adjudicated with a human verdict rather than reported raw; risks written as two-sided
tensions, not bug reports; a runnable recipe plus a declared typical result shape; failure
modes published with their fallback rather than omitted; a self-disabling, non-blocking
agent-nudge hook; cross-edition delta reporting, so a regression is visible rather than
silently overwritten.

Invariants worth knowing:
- `map-manifest.json` is input to the next run, not a report of the last one: its `commit`
  field is the baseline the next diff runs against and its `mode` field is the run mode
  (`docs/architecture/map-manifest.json:4-5`; the read is documented at §8 below).
- The manifest necessarily records the commit *before* the one that lands the deliverables
  — a run cannot know the hash of a commit that will contain its own output — which is why
  this group's files always differ from their own recorded baseline and this group
  re-enriches on every incremental run without ever converging (§1; confirmed again this
  run, whose changed-file set for this group was exactly the three deliverables).
- All three deliverables independently pin the same graph and commit — front matter here,
  `QUERYING.md`'s header, and `map-manifest.json`'s `graphName`/`commit` keys — so
  disagreement among them is the detectable signature of a partial or hand-edited run.
- The manifest's group list and this document's numbered sections are the same set,
  one-to-one; a group in one without the other is either an unenriched blank page the
  coverage section would wrongly count as covered, or a section the next incremental run can
  never select for re-enrichment.
- No machine-specific absolute path appears in any deliverable: the manifest records the
  project location as the literal `"."` and this guide refers to the Loom checkout by a
  bracketed placeholder rather than a real directory (`docs/architecture/map-manifest.json:3`;
  the fallback prefix in `QUERYING.md`'s header).
- The deliverables are generated; the only supported edit is a re-run (§8) — a hand edit
  survives only until the next refresh and leaves no trace when it is overwritten.
- `codebase-map.html` is declared in the manifest's `outputs` block but is gitignored and
  regenerated, never committed (`docs/architecture/map-manifest.json`, `outputs` key).
- Coverage is stated negatively as well as positively: which groups were re-read this run,
  which are inherited, which identifiers are legacy, and what the standing limitation of
  incremental mode actually means for the age of any given section's prose (§8).

One note on provenance. This is the only group whose subject is the file you are reading. The
graph read for this section describes the *previous* edition of these same three files —
commit `e9d4b425bba8c47b96922b5acfe0fdca3fe9481c` — because extraction necessarily runs
before this run's own write lands; that lag is structural, not an error, and it is why this
group is a permanent member of every incremental run's re-enrichment set (§1). The invariants
above and the corrected load-bearing-modules entry at §3 are what actually changed this
edition; the rest of this section's shape is stable across editions by design. Intra-document
references are given as section numbers rather than line numbers for the same reason the
previous edition adopted the practice: this run replaces the line numbers the graph's notes
were anchored against. See §6, item 21 for the self-reference this practice exists to
manage.

#### 2.46 The guide layer — `examples` (group `examples`)

New this edition: four Markdown files with no runnable code, schemas or agent prompts. It is
the public-facing documentation for the three Claude Code agent skills the repository ships —
`deep-research`, `hyper-research` and `map-codebase` — and doubles as the worked-example layer
for the Loom CLI itself: each guide states, concretely, how its skill drives Loom to build,
query and maintain a knowledge graph. The split is deliberate and stated in the index —
explanation lives here, everything executable lives under `.claude/`, because that is the only
place Claude Code resolves skills, workflows, agents and references from.

Key files: `examples/README.md` (index, shared prerequisites, the two CLI invariants),
`examples/deep-research/README.md`, `examples/hyper-research/README.md`,
`examples/map-codebase/README.md`.

Conventions: guides here, runnable assets under `.claude/`; one four-part template across all
three guides (framing, usage, pipeline shape, "how it uses The Loom", "after a run"); each
run's output documented as the next run's input, by a different Loom mechanism per skill — an
accumulating `--graph` target for deep-research, the document store for hyper-research, and
the commit-anchored manifest for map-codebase.

Invariants worth knowing:
- `examples/README.md` is the single place stating the two CLI invariants every example
  respects — `create-relation` requires `polarity`/`strength`/`evidence`, and embedding is a
  deliberate follow-up step, never a side effect of a write — and no per-skill guide restates
  them (`examples/README.md:36-44`).
- All 22 distinct `loom` commands the four guides name resolve to a real registered command;
  none cited does not exist, checked against `theloom/cli/registry.py`.
- Every repo-relative link the guides follow — into `.claude/README.md`, the three workflow
  scripts, the eight `research-*.md` agents, `docs/architecture/` — resolves to a file that
  is actually checked in.
- The fifteen `loom <command> '<json>'` invocations in this group sit outside
  `tests/test_claude_examples_contract.py`'s harvest, which is scoped to `.claude/` only
  (`tests/test_claude_examples_contract.py:32`, `:116`) — nothing reddens if one of them
  drifts out of contract with the CLI.

---

## 3. Load-bearing modules

Ranked by degree (how many things touch it) and by betweenness (how often the shortest route
between two parts of the system runs through it). Both rankings were recomputed fresh this
run.

### By degree

| # | Module | Why it is a hub |
| --- | --- | --- |
| 1 | `CommandInput` (`theloom/operations/common.py:42-56`) | The base every command input schema extends — 155 subclasses. It sets extras-forbidden parsing and the camelCase alias behaviour for the whole CLI. |
| 2 | `pkg:typing` | External. Imported by 157 modules; a marker of a fully typed codebase, not an architectural seam. |
| 3 | `theloom/store/falkor.py` | The store implementation: 70 symbols, 13 imports, imported by 36 modules. Every read and write in the system ends here. |
| 4 | `tapestry/src/views/explorer/Explorer.tsx` | The largest frontend component (95 symbols, 21 imports) — the default view and the one that composes every interaction layer. |
| 5 | `theloom/model.py` | The domain vocabulary: 42 symbols, imported by 80 modules, and the only place an entity or relation shape is defined. |
| 6 | `theloom/store/multigraph.py` | Imported by 106 modules — the facade every operation takes as its second argument to resolve which named graph to talk to. |
| 7–9 | `Chronicle.tsx` (98 symbols), `SystemsView.tsx` (77), `SemanticView.tsx` (77) | The other three canvas views; each is a single large component owning a renderer instance, its reducers and its overlays. |
| 10 | `tests/test_entity_proposer_foundation.py` | 71 symbols — the largest single test module by symbol count, carrying its own in-memory fakes. |
| 11 | `docs/architecture/ARCHITECTURE-MAP.md` | This file. Up from rank 13 last edition: 50 outbound documentation links out into the code it names (a per-document cap the extraction pipeline enforces, not an organic count — §6 item 21), and 66 written-layer notes now anchored back to it, up from 32. |
| 12 | `theloom/operations/semantic.py` | 43 symbols, 16 imports — the widest operations module, spanning the embedding lifecycle and five retrieval commands. |
| 13 | `theloom/extraction/treesitter.py` | 65 symbols in one file: the parsers, the per-language walkers and the public extraction API. |
| 14 | `tests/test_falkor_store.py` | 55 symbols pinning store CRUD, lifecycle, event log and version intervals — see §2.35 for the invariants it pins. |
| 15 | `theloom/cli/registry.py` | 31 contained symbols, 13 imports, imported by 12 — every command in the system is declared exactly once here. |

### By betweenness

`theloom/store/multigraph.py` and `theloom/store/falkor.py` top this ranking for the same
reason they top degree: they are the only route from any command to any stored fact.
`theloom/cli/registry.py` is next — the single door between the Typer surface and every
handler. `theloom/viz/bundle.py`, `theloom/operations/semantic.py` and
`theloom/operations/analysis.py` follow, then — at rank 7 —
`docs/architecture/ARCHITECTURE-MAP.md`, ahead of `theloom/config.py`,
`theloom/semantic/embed.py`, `theloom/model.py`, `theloom/operations/common.py`,
`theloom/viz/semantic.py`, `theloom/store/space.py`, `theloom/documents/chunkstore.py` and
`theloom/operations/synthesis.py`. Each of the code modules is the sole connector between an
upper layer and a lower one (payload assembly, retrieval, analytics, configuration,
embedding, domain shapes, input machinery, projection, the store chassis, chunk persistence,
synthesis).

The map's own climb is the sharper signal here than in the degree ranking. It entered the
named list only near the bottom two editions ago and this run places it seventh — a bigger
jump than its move from thirteenth to eleventh by degree, confirming what §6 item 21
predicts: prose that names two distant subsystems manufactures a short path between them
that no import or call justifies, and that effect compounds on betweenness faster than on
degree, because betweenness rewards being *between* things, which is exactly what a document
surveying the whole codebase is built to do. The distortion is disclosed, not corrected — no
deliverable excludes documentation edges from either centrality pass.

---

## 4. Dependency cycles

Thirteen cycles, unchanged from the previous edition. Two are multi-file; eleven are
self-loops. None is a layering violation.

| Members | Verdict | Reason |
| --- | --- | --- |
| `theloom/store/falkor.py` → `theloom/store/read_port.py` → `falkor.py` | intentional | `read_port.py` declares the narrow typed read Protocol; `falkor.py` imports it to be typed by it, and `read_port.py` imports `falkor.py` back for its typechecked conformance assertion. The idiom is the point (`theloom/store/read_port.py:1-25`). |
| `read_port.py` → `theloom/store/memory.py` → `read_port.py` | intentional | The same conformance assertion for the second adapter, which is what makes the two-adapter conformance suite meaningful (`theloom/store/memory.py:14-17`). |
| `_extract_calls`, `_find_identifier`, `_comment_notes`, `_string_literal_vocabulary`, `_extract_require_calls` (`theloom/extraction/treesitter.py`) | intentional | Recursive tree walks over syntax nodes — the natural shape for a parser. |
| `_generic_json_to_blocks` (`theloom/documents/parsers.py:261-305`) | intentional | Recursive descent over nested JSON. |
| `_hash_at_depth` (`theloom/reification/fingerprint.py:56-82`) | intentional | Depth-indexed colour refinement is defined recursively. |
| `_resolve_references` (`theloom/symbolic/core.py:789-822`) | intentional | Recursive substitution through a nested parameter structure. |
| `_jsonify` (`theloom/cli/io.py:56-64`) | intentional | Recursive normalization of nested output before serialization. |
| `_js_string` (`theloom/synthesis/prompts.py:13-24`) | intentional | Recursive escaping. |
| `_substitute` (`tests/test_claude_examples_contract.py:96-110`) | intentional | Recursive placeholder substitution in a test helper. |

No documentation cycle appears. The mutual mentions among `CLAUDE.md`, `README.md`,
`CONTRIBUTING.md` and this map that an older edition recorded were retired with the stale
file records, and nothing has reintroduced them.

One caveat carried from the written layer: several of these recursive walkers have no depth
guard, and the graph package mixes recursive DFS with explicit stacks across modules
(`theloom/graph/analytics.py:119-142`, `theloom/graph/cycles.py:38-99`). The verification
package's cycle detector is recursive for the same reason and is bounded only by the
interpreter's stack (`theloom/verification/checks.py:191-205`). Deep or hostile inputs are
bounded by input caps rather than by the recursion itself.

---

## 5. Communities vs. directories

*Clustering below is carried forward from commit `e9d4b425bba8c47b96922b5acfe0fdca3fe9481c`
— this refresh re-ran the structural analyses (cycles, centrality, components) but not
`find-clusters`, which is embedding-heavy; see §8.*

Semantic clustering over a 500-record sample of the 6,521-record current projection (as
measured at that commit) returns thirteen groups, all of size two or three. That is itself
the finding: at this scale, embedding-space proximity tracks *file locality*, not
cross-cutting communities. Nine of the thirteen are same-file neighbourhoods — the local
variables of `Minimap`, `Chronicle`, `EventList`, `Explorer`, `FilterPanel`, `SemanticView`
and `buildGraph`, and two pairs inside single test modules. Directory structure and
community structure agree, which for a codebase organized one concern per package is the
expected answer.

The four that cross a file boundary are the ones worth reading:

- **The same responsive-media variable is defined three times over.** `mq` appears in
  `Explorer`, `SemanticView` and `SystemsView` and the three cluster together at 0.71 — the
  only three-way file-crossing group in the sample. Three views each re-derive the same
  breakpoint state instead of sharing one hook.
- **The app stylesheet and the design tokens cluster as one thing.** `tapestry/src/App.css`
  and `tapestry/src/design/tokens.css` pair at 0.71. They sit in different directories but
  describe one concern: the visual contract. The split is chrome-versus-vocabulary; the
  subject is the same.
- **A test module clusters with its subject across the tree.** `tests/test_bridges.py` and
  `theloom/store/bridges.py` pair at 0.70. This is the only test-to-implementation pairing
  in the sample, and it is what a well-named test file should look like.
- **One test helper name is redefined in two suites.** `ent` in `test_name_addressing` and
  `ent` in `test_ops_relations` (0.71) — two local fixtures doing the same job with no
  shared helper.

Structurally, the graph is now one connected mass with a single outlier: **two components**
(re-verified this run), of which the larger holds 6,539 of 6,540 records. The lone singleton
is `tapestry/src/views/explorer/Explorer.css`, unchanged from the previous edition and still
the only record no import link reaches. The edition before last reported 79 components; the
other 77 singletons were orphaned written-layer notes, and they were retired (§6, resolved).
Every note in the graph remains reachable from the file it describes.

---

## 6. Risks & tensions

345 recorded risks; these are the ones to read first. Each is a real tension, not a bug
report — two things the code wants that cannot both be fully true.

1. **Hard-delete escape hatches inside an event-sourced, bi-temporal store.** Several
   operations expose a path that destroys history rather than invalidating:
   `inference-rule-delete` calls it unconditionally
   (`theloom/operations/inference.py:235-237`), `extraction-rollback` hard-deletes every
   record a run created (`theloom/operations/extraction.py:293`, `:307`),
   `delete-relation` exposes `hard: true` to callers
   (`theloom/operations/relations.py:377-389`), and `record-outcome`'s compensating rollback
   erases the evidence record (`theloom/operations/work_memory.py:166-169`). The tests pin
   both the invalidating and the destroying behaviour
   (`tests/test_ops_entity.py:138` vs `:152`).
2. **Every analytics and algebra command hydrates the entire graph in memory.**
   `theloom/operations/analysis.py:60-63` and `theloom/operations/algebra.py:51-54` read all
   records and all relations with no filter, and `transitive-closure` then runs a
   single-source pass per record over that same in-memory graph
   (`theloom/operations/algebra.py:359-381`). Correct and deterministic; it sets a hard
   ceiling on graph size for those commands.
3. **The discovery commands cost one vector query per candidate.** `find-clusters` and
   `semantic-gaps` loop a full similarity search per sampled record
   (`theloom/operations/semantic.py:743-750`, `:797-798`), with a default ceiling of 5,000
   records; `hybrid-search` additionally materializes the whole graph on every call. They
   present as ordinary read commands with plain numeric bounds.
4. **`blast-radius` pays a full graph scan on every call to compute one percentile.** The
   hub rule compares a node's degree against a whole-graph percentile, so every invocation
   lists every `calls`, `requires` and `instance_of` relation before it looks at the seed
   (`theloom/operations/blast_radius_traversal.py:61-75`, with the rationale at `:7-23`).
5. **As-of reads reconstruct the past by scanning the whole present.** `read_graph_as_of`
   costs four unbounded paged scans — live entities, covering entity versions, live edges,
   relation versions — regardless of how little of the graph existed at the bound
   (`theloom/store/falkor.py:331-393`).
6. **The derived read index re-encodes filter semantics that must be kept in sync by hand.**
   `filters.py` is the stated single oracle, but `_index_props` restates the same rules a
   second time so they can be pushed into Cypher, and nothing tests the two against each
   other (`theloom/store/falkor.py:101-114` against
   `theloom/store/filters.py:69-100`).
7. **Not every write is event-sourced.** Vector writes, metadata writes, verbatim imports,
   the index migration and the bridge import all call the store directly with no event
   (`theloom/store/falkor.py:270-275`, `:1129-1137`, `:252-262`, `:712-732`,
   `theloom/store/bridges.py:166-176`). Each has a documented reason; the invariant as
   stated has exceptions.
8. **First ingest appends blindly while reingest diffs.** Ingesting the same file twice
   duplicates its chunks, because the chunker mints fresh ids per run and the first-ingest
   path reports its update counters as literal zeros
   (`theloom/documents/ingestion.py:141-157`, `theloom/documents/chunker.py:224`).
9. **The SSRF guard validates a different resolution than the one it protects.** `guard_url`
   resolves the hostname and requires every address to be global, then `httpx` performs its
   own independent resolution when it opens the connection
   (`theloom/documents/ssrf.py:74-80` versus `:92-95`, acknowledged at `:8-9`). The 10 MB
   response ceiling is likewise applied after the body is fully buffered (`:95`, `:106-109`).
10. **Deduced edges enter a graph whose consumers treat every edge as fact.** The unique-name
    rule marks its edges honestly as `0.7 / inference`
    (`theloom/extraction/resolution.py:266-270`), but cycles, centrality, components and
    blast-radius read no confidence, and the guards keeping deduction honest are
    hand-maintained literal lists curated after an observed 288-caller `len()` incident
    (`theloom/extraction/resolution.py:77-141`).
11. **The incremental update is incremental only in its writes.** A one-file change still
    re-extracts the entire project (necessarily, so cross-file resolution sees every file),
    then reads every record and every relation to plan the diff
    (`theloom/extraction/codebasediff.py:517-519`, `:213`, `:227`).
12. **Extraction run records live outside the graph's transactional, bi-temporal history.**
    Runs are JSON blobs in a raw Redis list; `get_run` is a linear scan and `wipe()` is an
    unconditional delete of exactly the audit trail
    (`theloom/extraction/runstore.py:28-29`, `:73-84`).
13. **`extraction-rollback` reports counts that hide the failures behind them.** Both delete
    loops swallow every exception and increment only on success, so a rollback in which every
    delete failed returns zeros and no error
    (`theloom/operations/extraction.py:291-296`, `:305-310`).
14. **The soft-fail commands opt out of half the error contract.** `solve.py` and
    `symbolic.py` never raise: they emit a success-shaped document with `success: false` and
    exit 0, so a shell caller testing `$?` sees success for a failed solve
    (`theloom/operations/symbolic.py:3-7`, `theloom/operations/solve.py:382-387`).
15. **Verification is inconsistent about unknown names.** An unknown invariant name is a hard
    error in `check-invariants` and a silent skip in `validate-spec`
    (`theloom/operations/verification.py:216-220` versus `:307-309`); an unrecognised coupling
    metric silently falls back to degree centrality
    (`theloom/verification/metrics.py:65`); and `constrained-generate` commits records while
    reporting a hard-coded `skippedRelations: 0` for relations it dropped entirely
    (`theloom/operations/verification.py:550-556`).
16. **`run-inference`'s dry run still writes a trace record**, because the trace is created
    above the dry-run guard (`theloom/operations/inference.py:347-373`). Dry-run defaults also
    disagree across the mutating commands in one group
    (`theloom/operations/reification.py:103` and `theloom/operations/epistemic.py:807` versus
    `theloom/operations/inference.py:291` and `theloom/operations/extraction.py:190`).
17. **Two error-classification policies live in one operations layer.** `documents.py` maps
    exception classes onto typed codes by `isinstance`, while `analysis.py` deliberately
    writes error *messages* to feed a downstream substring classifier
    (`theloom/operations/documents.py:155-163` versus `theloom/operations/analysis.py:5-6`,
    `:518`). `ingest_url` does the same, deciding an SSRF failure's class by testing the
    message prefix (`theloom/documents/ingestion.py:250-254`).
18. **`cegis.py` inverts the package dependency direction.** Every other synthesis module
    depends only downward and takes a Protocol or a bare callable; `cegis.py` imports the
    operations layer, subclasses its input base and takes a concrete `MultiGraph`
    (`theloom/synthesis/cegis.py:34-35`, `:67-76`, `:436`).
19. **The lockfile carries two silent Python-version cliffs.** On 3.14+ `numba` and
    `llvmlite` resolve to sdist-only 2021 releases (`uv.lock:2099-2118`, `:1454-1468`), and
    `python-graphblas` drops its numba edge entirely on those bands
    (`uv.lock:3303-3310`) — the JIT path is absent rather than degraded. Separately,
    `falkordb`, the one non-negotiable dependency, is the only entry with no version floor
    (`uv.lock:4262`).
20. **The command count is hand-copied into two documents while the catalog is generated**
    (`COMMANDS.md:5` versus `README.md:12` versus `CLAUDE.md:8-9`), the repository layout is
    described three times with two copies already drifted (`CLAUDE.md:57-74`,
    `README.md:340-353`, `CONTRIBUTING.md:111-118` — corrected this run, the README anchor
    had drifted to a stale line range), and the glossary that declares itself the project's
    ubiquitous language is linked from none of them (`CONTEXT.md:1-6`).
21. **This directory has the same defect it reports elsewhere, and its own numbers show the
    feedback loop is not shrinking.** Every number in §1, §3, §4, §5 and §7 is prose
    transcribed by the run that wrote it: nothing regenerates it, nothing asserts it, and
    nothing fails when it drifts — unlike `COMMANDS.md`, which §2.42 records as byte-pinned
    to its generator by a drift test. The forty-five-group vocabulary is written out three
    times in this directory in two spellings (the manifest lists labels such as
    `theloom/composites (part 1/2)`; both prose files list ids such as
    `theloom-composites-1`), with no rule stated anywhere for converting one to the other.
    The map is also a node in the graph it measures — it climbed from thirteenth to
    eleventh by degree this edition and from outside the named list two editions ago to
    seventh by betweenness (§3) — so writing it changes the ranking it reports, and the
    growth compounds rather than settles: 50 outbound documentation links (a per-document cap
    the extraction pipeline enforces, not an organic count) against 66 inbound written-layer
    notes now anchored back to it, up from 32 two editions ago, because superseded notes keep
    their grounding edge to this file rather than losing it when a later run replaces them.
    The feedback is disclosed, but it is real, it is measurably growing, and it distorts
    betweenness more than degree for exactly the reason predicted: a document that names two
    distant subsystems creates a short path between them that no import or call justifies.

22. **The project's license is ISC; its only supported store's server is SSPL.**
    `pyproject.toml` declares and classifies the distribution as ISC (`pyproject.toml:7`,
    `:13`), but FalkorDB — the single, non-optional substrate architecture invariant 1
    requires — ships an SSPLv1 server; only the Python client is MIT (`STACK.md:22`,
    `:24-26`). STACK.md scopes the exposure correctly (SSPL only bites a resold managed
    service), but the constraint is recorded nowhere else, and invariant 1 forbids the escape
    hatch of a second store by design.
23. **The public-facing guides are the CLI examples nobody checks.**
    `tests/test_claude_examples_contract.py` already harvests every `loom <command> '<json>'`
    invocation out of Markdown and validates it against the command's registered input model
    — but only under `.claude/` (`tests/test_claude_examples_contract.py:32`, `:116`). The
    fifteen invocations across `examples/README.md` and the three skill guides (new this
    edition, §2.46) are the more public surface and are validated by nothing; a renamed
    command or a changed required field reddens no test.

Resolved since the previous edition: the seventy-seven orphaned written-layer notes that
opened the previous risk register are gone. Every note in the graph now connects to the file
it describes, and the only unreachable record left is a stylesheet (§5). Two records still
mention `docs/superpowers/` paths in their prose, a residue of the directory move that
caused the orphaning; they are attached to live files and answer queries correctly.

---

## 7. Open seams

*Carried forward from commit `e9d4b425bba8c47b96922b5acfe0fdca3fe9481c` — `semantic-gaps` is
embedding-heavy and was not re-run this refresh; see §8.*

Pairs the graph finds semantically close but structurally unconnected — places where two
parts of the system are talking about the same thing without a link between them. The
strongest pair, as last measured, scores 0.78, down from 0.86 the edition before that: the
duplicate-invariant pairs that topped that list were among the notes retired in that run.

- **Singular/plural and near-name method pairs with no shared implementation.**
  `InMemoryGraphStore.read_entities` / `read_entity` (0.78),
  `TypeCompatibilityGraph.get_valid_relations` / `get_valid_sources` (0.77),
  `list_relations` / `get_relations` in `theloom/operations/relations.py` (0.75),
  `Harness.relations` / `Harness.relation` in `tests/test_read_port.py` (0.75),
  `FalkorGraphStore.read_entities` / `read_entity_docs` (0.75),
  `DocumentIngestion.ingest_url` / `_ingest` (0.75), and `LoomGraph.add_edge` /
  `add_node` (0.75). Each is two functions doing one job in two arities or two modes, with
  the shared logic copied rather than factored.
- **Symmetrical test pairs written out twice.** The causal-polarity guard cases in
  `tests/test_phase9_units.py` (0.77 and 0.76), the adaptive and non-adaptive
  source-passage cases in `tests/test_synthesis_source_passages.py` (0.76), the
  open/answered session-scoping cases in `tests/test_epistemic_session.py` (0.76), and the
  create/update atomicity cases in `tests/test_store_atomicity.py` (0.75). Each is two tests
  of one rule with no shared helper between them.
- **Written-layer notes still recorded twice under two wordings.** "Per-graph namespaced
  localStorage funnelled through one write primitive" versus "…with a single write
  primitive" (0.76); "Scale-gated degradation with explicit node-count thresholds" versus
  "Scale-gated level of detail: behaviour changes only above a node-count threshold" (0.76);
  "Every Sigma instance, layout driver and listener is destroyed in its effect cleanup"
  versus "Every Sigma-owned resource is released when the graph changes or the view
  unmounts" (0.75). All three are the same fact written by two runs — once under a legacy
  `tapestry-src-*` identifier and once under the current per-view one. Unlike the retired
  orphans these are anchored and reachable; they are duplication, not breakage.
- **Near-synonymous local names inside one component.** `selectLoop` / `selectedLoop` in
  `SystemsView` (0.76), `selection` / `selectionRef` in `SemanticView` (0.76), `order` /
  `ordered` in `FilterPanel` (0.76) — an action and its result differing by one character —
  plus `COMMANDS` / `EXAMPLES` in `tests/test_claude_examples_contract.py` (0.75) and
  `invalidated` defined in both `replay.ts` and `replay.test.ts` (0.75).

---

## 8. Coverage & methodology

**Coverage.** 46 of 46 module groups are described above; none was skipped. The groups are,
by their identifiers in the graph:

```
theloom               theloom-algebra       theloom-analysis      theloom-cli
theloom-composites-1  theloom-composites-2  theloom-documents     theloom-exploration
theloom-extraction    theloom-graph         theloom-operations-1  theloom-operations-2
theloom-operations-3  theloom-reification   theloom-semantic      theloom-store
theloom-symbolic      theloom-synthesis     theloom-verification  theloom-viz
tapestry-1            tapestry-2            tapestry-e2e          tapestry-src
tapestry-src-design   tapestry-src-lib      tapestry-src-state
tapestry-src-views-chronicle  tapestry-src-views-explorer
tapestry-src-views-overview   tapestry-src-views-semantic
tapestry-src-views-systems
tests-1  tests-2  tests-3  tests-4  tests-5  tests-6
tests-fixtures-multi  tests-fixtures-repo   tests-fixtures-repo-src
repo-root-1  repo-root-2  docs  docs-architecture  examples
```

**Re-written this run.** This is a refresh: it patches specific sections rather than
re-deriving every one. Two groups had a real diff and were freshly re-enriched from source
this run — `examples` (§2.46, new to the graph — a four-file guide layer for the repository's
three shipped Claude Code skills) and `repo-root-1` (§2.42, re-enriched alongside it because
the same change added the `examples/README.md` cross-link this group's `README.md` now
carries). One group, `docs-architecture`, had a diff too small to trigger re-enrichment — so
its semantic layer (§2.45) is carried forward unchanged from whichever earlier run last wrote
it, and, as that section's own provenance note explains, it always describes the commit
before the one it is read at regardless. No group was attempted and left unenriched. The
other 43 sections describe records written by earlier runs against files that have not moved
since; their anchors were valid when written and their files are unchanged (one exception
corrected this run: §6 item 20's `README.md` anchor had drifted to a stale line range and is
now fixed). The load-bearing-modules ranking (§3), the cycle table (§4) and the component
count (§5) were recomputed fresh this run from cheap, non-embedding analyses (`graph-stats`,
`detect-cycles`, `analyze-centrality`, `detect-components`) and came back structurally
unchanged from the previous edition (same 13 cycles, same top-15 hubs by degree and by
betweenness); community clustering (§5) and the open-seams scan (§7) were not re-run — both
call `find-clusters` or `semantic-gaps`, which re-embed the whole sample and are too costly
for a refresh — so those two readings are carried forward from commit `e9d4b425` and say so
in place. That is the standing limitation of incremental (and especially refresh) mode: the
front matter presents one commit, but the reading of any given subsystem or analysis is as
old as the last time it was actually recomputed.

**Legacy identifiers.** Seven labels survive from earlier runs and still carry 130 records
between them: `docs-1`, `root-1`, `tests-fixtures`, and the coarser frontend partition
`tapestry-src-1` … `tapestry-src-4`. Their content overlaps the current groups; prefer the
identifiers listed above. This population was not re-audited this refresh; as of the
previous edition none of it was orphaned — every one of the 130 was anchored to a file that
exists (§5, §6).

**Not parsed.** Zero files failed to parse this run, because extraction touched only the
files behind the two re-enriched groups, the one carried group, and any renamed paths.
Across the whole graph, 69 of the 379 recorded files carry no symbols, and all of them are
accounted for: 46 are formats with no symbol grammar in this pipeline (20 Markdown — up 4 for
the new `examples/` guides, 14 JSON, 9 CSS, 1 YAML, 1 TOML, 1 lockfile), 18 are Python
`__init__.py` package markers that declare nothing, and 5 are TypeScript entry and config
files whose contents are a single default export or top-level call (`tapestry/src/main.tsx`,
`src/vite-env.d.ts`, `vite.config.ts`, `playwright.config.ts`, `src/lib/roving.test.ts`). All
69 still participate in documentation links and containment.

**Graph and commit.** Graph `codebase-the-loom`, commit
`0343de03f15efbb6ce1d329e8f8703e18bad4900`, mode `incremental`. The working tree was
**clean** at extraction.

**How to re-run.** `/map-codebase <repo-root>`. The run reads the commit recorded in
`docs/architecture/map-manifest.json` and re-extracts only what changed since; a group is
re-enriched only when its diff crosses the size threshold, carried forward (semantic layer
unchanged) when it does not, and left alone when nothing in it changed. Re-running is the
only supported way to edit any file in `docs/architecture/`.

**How to interrogate the graph afterwards.** Start with
`docs/architecture/QUERYING.md`, which carries a runnable recipe per question. The two
highest-yield calls are `loom entity-deep-dive '{"name": "<symbol>", "compact": true,
"graph": "codebase-the-loom"}'` for everything known about one symbol, and
`loom hybrid-search '{"query": "<what you are looking for>", "graph": "codebase-the-loom"}'`
when you only know roughly what you want. `loom explore`, `loom find-callers`,
`loom find-callees` and `loom blast-radius` answer where-defined, who-calls, what-it-calls
and what-breaks-if-I-change-this in one call each.

**The visualization.** `docs/architecture/codebase-map.html` is a self-contained page holding
the 400 highest-degree records and the 1,873 relationships among them, with analytics and
event-replay sections attached but the semantic-clustering bundle excluded (kept cheap for a
refresh). It is generated and gitignored; regenerate it by re-running the map.
