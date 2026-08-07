---
repo: the-loom
commit: 21466d5250d7ce760079705305a422077e36f17d
graph: codebase-the-loom
generated: 2026-08-06
mode: full
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
| Files recorded | 379 source files (371 present in the tree at this commit, plus 8 stale records — see §6) and 65 external package records |
| Language mix | Python 254, TypeScript 74, Markdown 23, JSON 14, CSS 9, JavaScript 2, YAML/TOML/lockfile 3 |
| Files tree-sitter could not parse | 43 (kept as file records carrying no symbols) |
| Symbols in the current projection | 6,497 — 2,738 functions and methods, 1,468 variables and constants, 450 classes/interfaces/type aliases, 444 file and package records |
| Written layer | 56 subsystem purposes, 367 conventions, 630 invariants, 344 risks |
| Records including superseded versions | 8,620 |
| Relationships including closed-out versions | 18,045 — 6,959 associations, 5,318 containment, 3,593 calls, 1,671 imports, 279 documentation links, 199 type instantiations, 26 supersessions |
| Working tree at extraction | clean |

The gap between 8,620 stored records and 6,497 in the current projection is not code
churn: it is the written layer being re-authored. Every superseded record is a purpose,
convention, invariant or risk note that an earlier mapping run wrote and a later one
replaced; file, symbol and variable records are the same in both counts. That is the
mapping design working as specified — a re-run supersedes only the written layer and
leaves structural facts to incremental re-extraction
(`docs/design/2026-08-03-map-codebase-design.md:146-155`).

---

## 2. Subsystem walkthrough

Forty-five module groups were read and written up. They fall into five areas: the Python
package (`theloom/`), the frontend workspace (`tapestry/`), the test suite, the fixtures,
and the repository's own declaration and design documents.

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

Key files: `theloom/model.py` (574 lines, imported by 47 modules), `theloom/errors.py`,
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

Where the two hardest architecture invariants stop being prose and become Cypher. The
package maps the domain model onto one FalkorDB instance so topology, vectors, the event
log and document chunks share a single transactional store, and it makes every mutation
event-sourced and bi-temporal: a write is one Cypher statement plus its stream append
inside one Redis `MULTI/EXEC`; an update snapshots the outgoing incarnation as a version
node instead of overwriting it; a delete invalidates rather than destroys unless the
caller explicitly asks for erasure. Everything above it reaches the graph only here.

Key files: `theloom/store/falkor.py` (1,143 lines), `theloom/store/space.py` (the shared
chassis: graph handle, event log, commit primitive, paged reads, vector index),
`theloom/store/commit.py`, `theloom/store/read_port.py`, `theloom/store/multigraph.py`
and `theloom/store/bridges.py`.

Conventions: one-statement commit with two-directional compensation; snapshot-on-write via
version nodes; derived read index prefilters with Python confirming; a `SKIP`/`LIMIT`
paging wrapper over every full scan; the guard lives inside the write, not in front of it.

Invariants worth knowing:
- A mutation and its event are committed as one unit or neither reaches the server
  (`theloom/store/commit.py:91-103`, `:10-20`).
- A failed event half is repaired forward, never rolled back, and the caller still sees
  success (`theloom/store/commit.py:112-170`, `theloom/store/events.py:94-119`).
- An update snapshots the prior incarnation as a closed version node before the document
  is swapped, for entities (`theloom/store/falkor.py:420-437`) and relations
  (`:936-963`).
- Deletion invalidates by default; `hard=True` is the only path that destroys history
  (`theloom/store/falkor.py:439-500`, `:1005-1026`).
- `filters.py` is the semantics oracle; the Cypher pushdown may only ever be a superset
  (`theloom/store/falkor.py:154-178`, `:687-700`, `theloom/store/filters.py:69-100`).

#### 2.3 The command line — `theloom/cli` (group `theloom-cli`)

The entire user-facing surface, holding no domain behaviour. `registry.py` declares every
command exactly once as a frozen descriptor built from a declarative row (name, category,
summary, Pydantic input model, handler, stdin stance); `app.py` generates one Typer
subcommand per descriptor at import time; `io.py` owns the wire protocol; `docs.py`
renders `COMMANDS.md` as a pure projection of the same descriptor list.

Key files: `theloom/cli/registry.py` (1,676 lines; 164 descriptors across 23 categories),
`theloom/cli/app.py` (119 lines), `theloom/cli/io.py` (85 lines), `theloom/cli/docs.py`.

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
command with the same shape: validated input plus the multi-graph facade in, a wire
document out. The layer declares schemas, resolves which graph to talk to, adds semantics
the raw store does not have (name-first addressing, active-status filtering, truncation
honesty, per-item error collection), translates library exceptions into typed error codes,
and shapes JSON. Part 1 covers the shared input machinery, the 12 semiring/routing
commands, the 16 traversal and analytics commands, and the consumption commands.

Key files: `theloom/operations/common.py`, `theloom/operations/consumption.py`,
`theloom/operations/consumption_budget.py`, `theloom/operations/analysis.py`,
`theloom/operations/algebra.py`.

Conventions: one uniform handler shape; name-first addressing through one shared resolver;
hydrate the whole graph then delegate to a pure algorithm library; one round-robin
allocator behind two truncation policies; honest truncation as a shared output contract.

Invariants worth knowing:
- Addressing takes exactly one of id or name, and a blank name is not a name
  (`theloom/operations/common.py:112-117`).
- An ambiguous name is refused with candidates, never guessed
  (`theloom/operations/common.py:133-139`).
- Name resolution reaches every status but prefers active
  (`theloom/operations/common.py:67`, `:126`, `:131-132`).
- Consumption reads apply their own active-status filter because id hydration has none
  (`theloom/operations/consumption.py:254`, `:268`, `:339`, `:356`, `:468`).

#### 2.5 Command semantics II — `theloom/operations` part 2 (group `theloom-operations-2`)

CRUD, knowledge lifecycle and machinery. Each module applies the operation-level semantics
the store deliberately does not know about — verification gating, polarity inference,
revision metadata, merge planning, forward-chaining derivation, structural fingerprinting,
dry-run and force guards — then delegates persistence.

Key files: `theloom/operations/epistemic.py` (943 lines),
`theloom/operations/inference.py` (618), `theloom/operations/relations.py` (573),
`theloom/operations/entity.py` (430), `theloom/operations/extraction.py` (363).

Conventions: the verification gate evaluated before the write in both relation arities;
tri-state field presence (absent vs explicit null vs value); plan, model-validate, then
commit in one atomic call; refuse-by-default with an explicit force opt-out; batch
hydration — one query per graph rather than one per item.

Invariants worth knowing:
- The causal/polarity partition is an invariant of the stored edge, not just of creation
  (`theloom/operations/relations.py:321-351`, `:163-166`, `:259-265`).
- A failing strict relation batch still persists its valid prefix
  (`theloom/operations/relations.py:280-282`).
- The endpoint gate is one verdict across both arities
  (`theloom/operations/relations.py:270-274`, `:171`).
- `update-entity` enforces the transition table and `retracted` is terminal
  (`theloom/operations/entity.py:249-252`, `:40-48`).

#### 2.6 Command semantics III — `theloom/operations` part 3 (group `theloom-operations-3`)

The reasoning-and-assurance third: the embedding lifecycle and the five retrieval/discovery
commands, the symbolic adapters, the natural-language solver, the nine Plan-Traverse-Realize
synthesis commands, the guard/invariant/spec/AC-3/capability suite with its sandbox replayer,
and the write half of cross-session experiential memory.

Key files: `theloom/operations/semantic.py` (965 lines),
`theloom/operations/verification.py` (621), `theloom/operations/synthesis.py` (610),
`theloom/operations/solve.py` (387), `theloom/operations/work_memory.py` (175),
`theloom/operations/symbolic.py` (86).

Conventions: operations as thin adapters over an engine core; a typed soft-fail envelope
for external-dependency operations; duck-typed document-store views; validate-then-write
with compensating rollback; deterministic spread sampling instead of first-N truncation.

Invariants worth knowing:
- One retrieval binding backs every semantic read in the group
  (`theloom/operations/semantic.py:144-165`, used at `:554`, `:600`, `:687`, `:744`).
- Embedding is opt-in and content-hash idempotent
  (`theloom/operations/semantic.py:327-344`).
- An embedding failure is recorded on the entity, never raised to the caller
  (`theloom/operations/semantic.py:341-344`, counted at `:408-413`, `:436-442`).
- Graph-mutating discovery and repair commands default to dry run
  (`theloom/operations/semantic.py:492`, guarded write at `:497-499`).

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

The package that turns things outside the graph into graph content. Its dominant path is
deterministic, LLM-free codebase extraction: tree-sitter parses each file into file, class,
function and variable records with containment, call, type and import links; a whole-project
second pass joins what no single-file parse can resolve; a third links Markdown docs into
the code they name; one module owns every name, observation prefix and evidence string so
writers and readers cannot drift; an incremental path replays a git diff by superseding
rather than deleting. A second, unrelated path does LLM document extraction.

Key files: `theloom/extraction/treesitter.py` (1,386 lines),
`theloom/extraction/resolution.py`, `theloom/extraction/doclinks.py`,
`theloom/extraction/encoding.py`, `theloom/extraction/codebasediff.py`.

Conventions: two-pass extraction; one module builds and parses every encoded string; plan
the whole update, guard it, then write; git — not the filesystem — decides what is in the
codebase; an append-only run log shared by every extraction path.

Invariants worth knowing:
- An incremental update supersedes records; it never deletes them
  (`theloom/extraction/codebasediff.py:462-472`, `:69-70`).
- A callee that does not resolve to exactly one reachable target produces no link
  (`theloom/extraction/resolution.py:431-451`).
- A structural link belongs to a changed file when either endpoint does
  (`theloom/extraction/codebasediff.py:298-320`).
- The structural diff never retracts a link structural extraction did not emit
  (`theloom/extraction/codebasediff.py:78-88`, `:266-282`).

#### 2.14 Document ingestion — `theloom/documents` (group `theloom-documents`)

Turns files, directories, raw strings and URLs into embedded, searchable chunk rows inside
the same FalkorDB instance as the graph — honouring the one-store invariant. It owns format
detection and parsing into a uniform block normal form, size-aware chunking with sentence
overlap and an atomic-block escape hatch, an SSRF-hardened fetcher, a declared chunk-metadata
shape, and event-sourced persistence into a dedicated per-prefix chunk graph.

Key files: `theloom/documents/ingestion.py`, `theloom/documents/chunker.py`,
`theloom/documents/chunkstore.py`, `theloom/documents/parsers.py`,
`theloom/documents/ssrf.py`, `theloom/documents/metadata.py`.

Conventions: three-phase chunking with an atomic-block escape hatch; chunk storage as a
graph space rather than a second store; a deny-by-default egress guard revalidated on every
redirect hop; a structural error taxonomy translated at the operations boundary.

Invariants worth knowing:
- Chunk writes are event-sourced through the store's shared commit primitive
  (`theloom/documents/chunkstore.py:103`, `:177-192`).
- A document delete is pinned to the id snapshot its event names
  (`theloom/documents/chunkstore.py:154-186`).
- Chunks live in one per-prefix chunk graph, global across knowledge graphs
  (`theloom/documents/chunkstore.py:56`, `:69-78`).
- Chunk event payloads carry coordinates, never chunk text
  (`theloom/documents/chunkstore.py:202-221`).

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

A store-agnostic library of predicates that decide whether a graph, or a single proposed
mutation, keeps the model's structural promises. `checks.py` holds the read-side guards,
the five builtin invariants and the shared cycle detector; `guards.py` holds the mutation
gate that entity and relation creation call before writing; `metrics.py` holds the coverage
and coupling generators shared by the capability command and the DSL; `capability_spec.py`
layers a fluent DSL whose violations carry suggested actions that feed proposal generation.

Key files: `theloom/verification/checks.py`, `theloom/verification/guards.py`,
`theloom/verification/metrics.py`, `theloom/verification/capability_spec.py`,
`theloom/verification/propagation.py`.

Conventions: predicate tables as the public registry of rules; uniform violation envelopes;
one verdict shared across write and read surfaces; deterministic iteration order as part of
the output contract.

Invariants worth knowing:
- Guards abstain when a field is absent rather than reporting a violation
  (`theloom/verification/checks.py:41-45`, `:60-62`, `:78-80`).
- The polarity partition is enforced on write and mirrored on read from one message
  (`theloom/verification/guards.py:64-71`).
- Entity gates warn; relation gates block (`theloom/verification/guards.py:41-52`).
- Retracted entities read back but cannot become relation endpoints
  (`theloom/verification/guards.py:81-107`).

#### 2.18 Prose in, prose out — `theloom/synthesis` (group `theloom-synthesis`)

Turns a graph into prose and then grades that prose back against the graph. The spine is
Plan-Traverse-Realize: the planner picks an anchored ego-subgraph and groups it into
regions; the traverser walks those regions emitting one evidence unit per entity with a
decayed confidence and a provenance trail; the realizer linearizes each region causally and
renders narrative, outline, causal chain, evidence map, proposal or raw text. A fidelity
module then scores the generated text against its sources. A second subsystem here
implements counterexample-guided inductive synthesis over a seeded PRNG.

Key files: `theloom/synthesis/planner.py`, `theloom/synthesis/traverser.py`,
`theloom/synthesis/realizer.py`, `theloom/synthesis/cegis.py`, `theloom/synthesis/llm.py`.

Conventions: a staged pipeline over plain wire dictionaries; an optional LLM with a
deterministic template fallback at every call site; prompt-injection defence by sanitizing
inputs then wrapping them in a data tag; a Protocol-narrowed store dependency.

Invariants worth knowing:
- Synthesis output is fully deterministic when no LLM is configured
  (`theloom/synthesis/llm.py:215-218`, `theloom/synthesis/realizer.py:314-317`).
- The seeded PRNG is bit-exact 32-bit, so a seed determines the candidate graph exactly
  (`theloom/synthesis/generator.py:28-60`).
- Verification touches no store; only a successful commit does
  (`theloom/synthesis/cegis.py:129-163`, `:368`).
- The refinement loop always terminates (`theloom/synthesis/cegis.py:382-418`).

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

The first sixth carries the suite-wide infrastructure everything else depends on — the
namespaced live-store fixture chain and the shared test doubles — plus the behavioural
contracts of the outermost layers: the CLI JSON protocol and its typed error codes, the
registry's single construction path, multi-graph and visualization wire shapes, the composite
framework's never-throw envelope, the event-sourcing of cross-graph bridges and document
chunks, and the pure algorithmic foundations.

Key files: `tests/conftest.py`, `tests/fakes.py`, `tests/test_bridges.py`,
`tests/test_chunk_events.py`, `tests/test_claude_examples_contract.py`.

Conventions: a per-test namespaced live store with self-teardown; one shared doubles module
instead of per-module stubs; commands exercised through the registry rather than their
handlers; documentation harvested and validated as a machine-checked contract.

Invariants worth knowing:
- Every live-store test is namespaced and leaves the store as it found it
  (`tests/conftest.py:35-45`).
- Documented invocations must validate against the live CLI input models
  (`tests/test_claude_examples_contract.py:136-160`).
- A chunk write and its event append are one unit in both failure directions
  (`tests/test_chunk_events.py:179-250`).

#### 2.34 Comprehension and proposal surfaces — `tests` part 2 (group `tests-2`)

The executable specification for the commands that answer a question or propose a change
rather than store a fact. Four contracts dominate: consumption honesty (a budgeted answer
must degrade without lying), the proposal foundation pinned against a tiny in-memory fake,
two composites that were once registered but untested, and the shared budget algebra.

Key files: `tests/test_consumption.py` (597 lines),
`tests/test_consumption_budget.py`, `tests/test_entity_proposer_foundation.py`,
`tests/test_enrichment_crawl.py`.

Conventions: pure algebra split out from live-store tests; the module docstring as the
defect narrative; hand-worked golden values with the arithmetic written out; monkeypatched
fault injection to prove degradation rather than fabrication.

Invariants worth knowing:
- A truncated answer must balance: shown plus cut equals total
  (`tests/test_consumption.py:270-271`).
- Budget pressure degrades breadth evenly and never cuts the queried entity
  (`tests/test_consumption.py:278-280`, `:332-342`).
- Every populated section keeps its first row unconditionally
  (`tests/test_consumption_budget.py:40-46`).

#### 2.35 Extraction and store — `tests` part 3 (group `tests-3`)

Eight of the twelve files tell one continuous story: how source text becomes a graph, what
the resolvers refuse to guess, how a re-run retires what an older extractor got wrong, and
what the store guarantees underneath. The recurring subject is refusal under uncertainty.

Key files: `tests/test_falkor_store.py` (793 lines),
`tests/test_extraction_units.py` (553), `tests/test_extraction_resolution.py` (521).

Conventions: parametrized truth tables that spend most rows on negatives; the regression
that motivated a test written into its docstring; golden fixed-repo assertions with the
arithmetic in comments; faked at the seam, never at the server.

Invariants worth knowing:
- Structural extraction never emits a generic association link
  (`tests/test_extraction_resolution.py:451-463`).
- No extracted link points at a record the extraction did not create
  (`tests/test_extraction_resolution.py:481-499`).
- An ambiguous name produces no link at all
  (`tests/test_extraction_resolution.py:205-241`).

#### 2.36 Write path and model — `tests` part 4 (group `tests-4`)

The executable contract for the write path, the domain model beneath it, and the incremental
re-extraction that keeps the codebase graph honest: the model itself (19 entity types, 17
relation types, 5 statuses, confidence boundaries, the full transition table), then entity
CRUD with revision auto-population, merge, bulk import, document ingestion's typed-error
translation, and update-codebase's relation diffing and shrink guard against a real throwaway
git repository.

Key files: `tests/test_incremental_update.py`, `tests/test_ops_entity.py`,
`tests/test_ops_merge.py`, `tests/test_model.py`, `tests/test_ops_bulk.py`.

Conventions: operations tested at the function seam through their input models; whole-document
equality instead of spot checks; a real throwaway git repository as the incremental fixture;
monkeypatched spies pinning query shape, never elapsed time.

Invariants worth knowing:
- Re-extraction supersedes vanished symbols; it never hard-deletes them
  (`tests/test_incremental_update.py:392`).
- Relation invalidation is a bi-temporal close-out: gone from the projection, intact in
  history (`tests/test_incremental_update.py:176`).
- Structural re-extraction never touches the written layer
  (`tests/test_incremental_update.py:285`).

#### 2.37 Gates and read conformance — `tests` part 5 (group `tests-5`)

The specification for two things the system promises and cannot prove from source alone:
that every mutation is honestly accounted for, and that every read means the same thing
wherever it is answered. On the write side, the relation gate, the batch-equals-single rule,
the self-improvement saga's rollback, the embedding state machine's honest counts and
reingest's content-hash diff. On the read side, a two-adapter conformance suite for the
narrow read port including bi-temporal reads.

Key files: `tests/test_ops_relations.py` (673 lines), `tests/test_read_port.py` (595),
`tests/test_semantic_perf.py` (497), `tests/test_self_improve.py` (400).

Conventions: deterministic embeddings via an injected fake on two seams; fault injection at
a class-level seam; expected values worked out by hand in the docstring; one conformance
suite parametrized across every adapter; call-count spies standing in for performance
assertions.

Invariants worth knowing:
- Polarity is a causal-type-only field, enforced at create, batch and update alike
  (`tests/test_ops_relations.py:79-143`).
- The relation gate runs before the bridge branch and before any write
  (`tests/test_ops_relations.py:164-173`).
- A neighbourhood is hydrated in one batched read, never one read per neighbour
  (`tests/test_ops_relations.py:569-599`).

#### 2.38 Atomicity, pushdown and visualization — `tests` part 6 (group `tests-6`)

Three otherwise-unrelated frontiers joined by a common method. First, the store's hardest
guarantees: indivisibility of a mutation and its event under injected failure at four
distinct points, and exact equivalence between the server-side filter pushdown and the
Python path it replaced — with the Python filters kept as a live oracle. Second, the whole
visualization export pipeline walked in stages. Third, the work-memory contract.

Key files: `tests/test_store_pushdown.py` (543 lines),
`tests/test_store_atomicity.py` (466), `tests/test_work_memory.py` (485),
`tests/test_viz_bundle.py`, `tests/test_viz_serve.py`.

Conventions: a Python filter oracle as the semantics reference for a pushed-down query;
fault injection by monkeypatching the exact collaborator that must fail; a query-shape spy
asserting narrowing by row count, never wall clock; optional dependency gates at module
import.

Invariants worth knowing:
- A mutation and its event append are one unit — neither half survives alone
  (`tests/test_store_atomicity.py:92`, `:118`).
- Every mutation is a single Cypher statement, so a failure at execution leaves no
  half-state (`tests/test_store_atomicity.py:214`, `:235`).
- An unrollbackable event append is repaired in batch order, or named in a typed error
  (`tests/test_store_atomicity.py:313-330`).

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

#### 2.42 Declaration surface — repo root part 1 (group `repo-root-1`)

The eleven files that state what The Loom is, what it is built from, how it is run and gated,
what its words mean, and how to report a hole in it. None is imported by the package.
`pyproject.toml` is the single manifest — runtime dependencies with conservative floors, two
console entry points bound to the same callable, and the configuration for all three quality
tools. `docker-compose.yml` declares the one store service, with the persistence path and
result-set cap that two past incidents produced. Four documents state the same project to
four audiences, and the command catalog is machine-generated.

Key files: `pyproject.toml`, `docker-compose.yml`, `CONTRIBUTING.md`, `CONTEXT.md`,
`scripts/gen_bench_graph.py`.

Conventions: a registry-generated command catalog pinned by a drift test; architecture
invariants restated per audience; optional dependency extras keeping the core install thin;
ubiquitous language recorded as term plus explicit avoid-list; configuration comments
recording the incident that produced the setting.

Invariants worth knowing:
- The catalog is generated from the registry and a test fails when it drifts
  (`COMMANDS.md:3`, `CONTRIBUTING.md:85-87`).
- The green-main gate is four commands, and format-check is one of them
  (`CONTRIBUTING.md:36-48`, `CLAUDE.md:47-53`).
- The store persists to a fixed path with the result-set cap lifted
  (`docker-compose.yml:9-19`).

#### 2.43 Dependency closure — repo root part 2 (group `repo-root-2`)

`uv.lock` alone: the resolved, digest-pinned closure that turns the loose floors in the
manifest into an exact, byte-verifiable install — 4,909 lines holding 187 package blocks and
2,826 digest-pinned distribution records, all from one index. It contains no code and nothing
imports it, but it decides what every import inside the package resolves to at runtime.

Conventions: a marker-forked universal lockfile; a single-registry digest-pinned supply
chain; a platform-gated GPU stack behind platform markers; optional surfaces carved out as
extras and a dev group.

Invariants worth knowing:
- Every locked artifact is digest-pinned to a single index
  (`uv.lock:41-44`; the sole non-registry source at `uv.lock:4214`).
- The lock restates the manifest's declarations, which is what makes drift detectable
  (`uv.lock:4215-4239`, `:4259-4285`).
- The root project is locked as an editable install of the working tree
  (`uv.lock:4211-4214`).

#### 2.44 Design record — `docs` (group `docs`)

The written design record: approved specifications for the two subsystems code alone cannot
explain — the visualization surface and the codebase-mapping skill — plus the recorded scale
benchmark. Each spec fixes its contract before implementation (purpose, architecture, data
contract, CLI surface, error-code table, testing strategy, phasing, explicit out-of-scope
list) and audits itself against the numbered architecture invariants. The benchmark closes
the loop on the visualization spec's performance ambition with measured numbers and honest
caveats.

Key files: `docs/design/2026-07-11-loom-visualization-design.md`,
`docs/design/2026-08-03-map-codebase-design.md`, `docs/benchmarks/tapestry-scale.md`.

Conventions: spec self-audit against numbered invariants; a dated verified-constraints
preamble separating checked facts from design intent; an error-code and failure-behaviour
table closing every spec; benchmarks as reproducible narrative reports, not assertions.

Invariants worth knowing:
- Scale targets are reported benchmarks; no wall-clock assertion may enter CI
  (`docs/benchmarks/tapestry-scale.md:4-6`).
- The payload is a versioned contract pinned across Python and TypeScript by a drift test
  (`docs/design/2026-07-11-loom-visualization-design.md:82-101`).
- The visualization surface adds no store and never writes back to the graph
  (`docs/design/2026-07-11-loom-visualization-design.md:67-70`).

#### 2.45 Map deliverables — `docs/architecture` (group `docs-architecture`)

The committed output of the mapping pipeline — this file, the recipe sheet and the run
record. Nothing in the package imports them; they are a projection of the graph into prose
plus one machine-readable record, regenerated rather than edited.

Key files: `docs/architecture/ARCHITECTURE-MAP.md`, `docs/architecture/QUERYING.md`,
`docs/architecture/map-manifest.json`.

Conventions: a fixed subsystem slot template mirroring the four kinds of written record;
every architectural statement carrying a file-and-line anchor; a runnable recipe plus a
declared typical result shape; a self-disabling, non-blocking agent nudge hook.

Invariants worth knowing:
- The manifest's commit is the baseline for the next incremental run
  (`docs/architecture/map-manifest.json:4`).
- All three deliverables pin the same graph and the same commit
  (`docs/architecture/ARCHITECTURE-MAP.md:2-6`, `docs/architecture/QUERYING.md:7-8`).
- These files are generated; the only supported edit is a re-run
  (`docs/architecture/map-manifest.json` outputs block).

---

## 3. Load-bearing modules

Ranked by degree (how many things touch it) and by betweenness (how often the shortest route
between two parts of the system runs through it).

### By degree

| # | Module | Why it is a hub |
| --- | --- | --- |
| 1 | `CommandInput` (`theloom/operations/common.py:41-55`) | The base every command input schema extends — 156 subclasses. It sets extras-forbidden parsing and the camelCase alias behaviour for the whole CLI. |
| 2 | `pkg:typing` | External. Imported by 156 modules; a marker of a fully typed codebase, not an architectural seam. |
| 3 | `theloom/store/falkor.py` | The store implementation: 70 symbols, imported by 36 modules. Every read and write in the system ends here. |
| 4 | `tapestry/src/views/explorer/Explorer.tsx` | The largest frontend component (95 symbols, 21 imports) — the default view and the one that composes every interaction layer. |
| 5 | `theloom/model.py` | The domain vocabulary: imported by 78 modules, and the only place an entity or relation shape is defined. |
| 6 | `theloom/store/multigraph.py` | Imported by 103 modules — the facade every operation takes as its second argument to resolve which named graph to talk to. |
| 7–9 | `Chronicle.tsx` (98 symbols), `SystemsView.tsx` (77), `SemanticView.tsx` (77) | The other three canvas views; each is a single large component owning a renderer instance, its reducers and its overlays. |
| 10 | `docs/architecture/ARCHITECTURE-MAP.md` | This file. It is in the graph twice over: the written layer anchors to it, and its own text produces documentation links into the code it describes. |
| 11 | `tests/test_entity_proposer_foundation.py` | 71 symbols — the largest single test module by symbol count, carrying its own in-memory fakes. |
| 12 | `theloom/extraction/treesitter.py` | 65 symbols in one file: the parser, the per-language queries and the public extraction API. |
| 13 | `theloom/operations/semantic.py` | 43 symbols, 16 imports — the widest operations module, spanning embedding lifecycle and five retrieval commands. |
| 14 | `theloom/cli/registry.py` | 31 contained symbols, 13 imports, imported by 12 — every command in the system is declared exactly once here. |
| 15 | `tests/test_falkor_store.py` | 55 symbols pinning store CRUD, lifecycle, event log and version intervals. |

### By betweenness

`theloom/store/multigraph.py` and `theloom/store/falkor.py` top this ranking for the same
reason they top degree: they are the only route from any command to any stored fact.
`theloom/cli/registry.py` is next — the single door between the Typer surface and every
handler. `theloom/viz/bundle.py`, `theloom/operations/semantic.py`,
`theloom/operations/analysis.py`, `theloom/config.py`, `theloom/semantic/embed.py` and
`theloom/model.py` follow: each is the sole connector between an upper layer and a lower one
(payload assembly, retrieval, analytics, configuration, embedding, domain shapes).

Three of the top fifteen are documents, not code — `docs/architecture/ARCHITECTURE-MAP.md`,
`CLAUDE.md` and `README.md`. They broker because the documentation-link pass connects prose
to the code it names, and because the written layer anchors its notes to them. Two more,
`docs/superpowers/plans/2026-07-11-tapestry-phase-5.md` and `…-phase-4.md`, are stale records
for files moved out of the tree at commit `6c58715` (see §6). Their brokerage is an artifact,
not a fact about the current code.

---

## 4. Dependency cycles

Fifteen cycles. Four are multi-file; eleven are self-loops. None is a layering violation.

| Members | Verdict | Reason |
| --- | --- | --- |
| `theloom/store/falkor.py` → `theloom/store/read_port.py` → `falkor.py` | intentional | `read_port.py` declares the narrow typed read Protocol; `falkor.py` imports it to be typed by it, and `read_port.py` imports `falkor.py` back for its typechecked conformance assertion. The idiom is the point (`theloom/store/read_port.py:50-182`). |
| `read_port.py` → `theloom/store/memory.py` → `read_port.py` | intentional | The same conformance assertion for the second adapter, which is what makes the two-adapter conformance suite meaningful. |
| `CLAUDE.md` → `README.md` → `docs/superpowers/plans/2026-07-11-tapestry-phase-5.md` → `CLAUDE.md` | intentional (stale members) | Mutual documentation mentions, recorded by the doc-link pass. Harmless as a cycle, but two members no longer exist in the tree. |
| `CLAUDE.md` → `README.md` → `CONTRIBUTING.md` → `docs/architecture/ARCHITECTURE-MAP.md` → `CLAUDE.md` | intentional | The four audience documents deliberately cross-reference each other; this map closes the loop by naming its own guidance file. |
| `_extract_calls`, `_find_identifier`, `_comment_notes`, `_string_literal_vocabulary`, `_extract_require_calls` (`theloom/extraction/treesitter.py`) | intentional | Recursive tree walks over syntax nodes — the natural shape for a parser. |
| `_generic_json_to_blocks` (`theloom/documents/parsers.py:261-305`) | intentional | Recursive descent over nested JSON. |
| `_hash_at_depth` (`theloom/reification/fingerprint.py:56-82`) | intentional | Depth-indexed colour refinement is defined recursively. |
| `_resolve_references` (`theloom/symbolic/core.py:789-822`) | intentional | Recursive substitution through a nested parameter structure. |
| `_jsonify` (`theloom/cli/io.py:56-64`) | intentional | Recursive normalization of nested output before serialization. |
| `_js_string` (`theloom/synthesis/prompts.py:13-24`) | intentional | Recursive escaping. |
| `_substitute` (`tests/test_claude_examples_contract.py:96-110`) | intentional | Recursive placeholder substitution in a test helper. |

One caveat carried from the written layer: several of these recursive walkers have no depth
guard, and the graph package mixes recursive DFS with explicit stacks across modules
(`theloom/graph/analytics.py:119-142`, `theloom/graph/cycles.py:38-99`). Deep or hostile
inputs are bounded by input caps rather than by the recursion itself.

---

## 5. Communities vs. directories

Semantic clustering over a 500-record sample of the current projection returns thirteen
groups, all of size two or three. That is itself the finding: at this scale, embedding-space
proximity tracks *file locality* and *note duplication*, not cross-cutting communities. Nine
of the thirteen are same-file variable neighbourhoods (`exportSvg`, `Chronicle`, `Explorer`,
`systems`, `replay`, `DetailPanel`, `smoke.spec`). Directory structure and community
structure agree, which for a codebase organized one concern per package is the expected
answer.

The four that disagree are worth reading:

- **Same idiom, different directories.** `FakeStore.__init__` in
  `tests/test_entity_proposer_foundation.py` clusters with `FakeVectorStore.__init__` in
  `tests/test_exploration_foundation.py`. Both hand-roll a store double; the suite has a
  shared doubles module (`tests/fakes.py`) that neither uses. The seam is a convention, not a
  directory.
- **The render-loop idiom crosses all four canvas views.** "Refs as the live channel into the
  render loop" appears as a separate convention in `chronicle`, `explorer`, `semantic` and
  `systems`, and the clustering pairs those descriptions with each other rather than with
  anything in their own directories. The real module boundary here is horizontal — an
  interaction kernel that lives in four copies.
- **Two counting surfaces claim to agree.** The Overview roll-up and the Explorer facet
  counts cluster together as a pair of risk notes: they advertise agreement while deliberately
  counting different populations (`tapestry/src/views/overview/stats.ts:4-9`).
- **The animation contract is stated twice.** The wrapped raised-cosine pulse is described
  once in the Systems view's written layer and once again as a near-identical restatement.
  See §7 — this is duplication in the notes, not in the code.

Structurally, the graph is one connected mass: 17 components, of which the largest holds
6,481 of 6,497 records. The sixteen singletons are not code islands — fifteen of them are the
`tapestry/src/design` written layer, orphaned during enrichment (§6), and the sixteenth is
`tapestry/src/views/explorer/Explorer.css`, which no import link reaches.

---

## 6. Risks & tensions

344 recorded risks; these are the ones to read first. Each is a real tension, not a bug
report — two things the code wants that cannot both be fully true.

1. **Fifteen design-layer notes are orphaned and unreachable.** Everything written about
   `tapestry/src/design` in one enrichment batch — the purpose, four conventions, six
   invariants, four risks — carries no link to any file, so `explore` on
   `tapestry/src/design/tokens.css` will never surface it. A duplicate, linked copy of the
   same material exists (`tapestry/src/design/tokens.css` neighbours). Symptom of a partial
   enrichment write; fixed by re-running the map for that group.
2. **Eight stale file records survive a file move.** `docs/superpowers/plans/*` and
   `docs/superpowers/specs/*` were moved to `docs/design/` and `docs/benchmarks/` at commit
   `6c58715`, but their records are still active, still carry documentation links, and two of
   them rank in the top fifteen by betweenness. Any query that ranks or traverses documents
   will over-weight paths that no longer exist.
3. **Hard-delete escape hatches inside an event-sourced, bi-temporal store.** Several
   operations expose a `hard=True` path that destroys history — the docstring itself calls it
   "the only path that loses history" (`theloom/operations/entity.py:323-340`;
   `theloom/operations/inference.py:235-237`; `theloom/operations/work_memory.py:162-169`).
   The tests pin both the invalidating and the destroying behaviour
   (`tests/test_ops_entity.py:138` vs `:152`).
4. **Every analytics and algebra command hydrates the entire graph in memory.**
   `theloom/operations/analysis.py:60-63` reads all entities and all relations with no filter,
   reached from at least six commands. Correct and deterministic; it sets a hard ceiling on
   graph size for those commands.
5. **The symbolic engine's never-raises guarantee has a hole outside the main thread.** The
   watchdog uses signals, so calling `core.run` from a worker thread raises rather than
   returning the error envelope (`theloom/symbolic/core.py:1009`, `:1015`, `:1023-1025`).
6. **Inferred links enter a graph whose consumers treat every link as fact.** The
   unique-name rule in resolution and the whole doc-link pass emit links marked inferred
   (`theloom/extraction/resolution.py:437-448`, `theloom/extraction/doclinks.py:15`), but
   downstream traversal does not distinguish them.
7. **First ingest appends blindly while reingest diffs.** Ingesting the same file twice
   duplicates its chunks, because chunk ids are freshly generated per run
   (`theloom/documents/ingestion.py:141-146`, `theloom/documents/chunker.py:224`).
8. **Config handling is fail-open for parse errors and fail-loud for field errors.** A typo in
   a brace silently reverts host, port and default graph to defaults
   (`theloom/config.py:143-147`); a typo in a port number is a hard stop (`:114-122`). A
   truncated config is the dangerous case.
9. **The bi-temporal bound applies to records but not to derived sections.** A payload built
   `asOf` some past instant carries present-day analytics and projection
   (`theloom/viz/bundle.py:128-135` vs `theloom/viz/analytics.py:56`,
   `theloom/viz/semantic.py:64`), and the bound is validated by date parsing but applied by
   string comparison (`theloom/viz/bundle.py:107` vs `theloom/viz/temporal.py:15`).
10. **Blanket exception capture in composites buys resilience and costs diagnosability.**
    Section failures are captured as message-only text
    (`theloom/composites/framework.py:53-56`), and per-target failures inside a section can
    vanish entirely under `error: null` (`theloom/composites/influence_map.py:138-197`).
11. **`run-inference`'s dry run still writes a trace record.** The guarded relation write is
    skipped, but the trace entity is created unconditionally
    (`theloom/operations/inference.py:347-373`). Dry-run defaults also disagree across the
    mutating commands in one group (`theloom/operations/reification.py:102` and
    `theloom/operations/epistemic.py:807` versus `theloom/operations/extraction.py:190`).
12. **The whole science stack loads on every CLI invocation.** `registry.py:33` imports a
    composite that pulls the optimal-transport stack and torch, so `loom version` pays roughly
    a second of import latency — the floor for every scripted or agent-driven call.
13. **The proposal pipeline's fourth step filters nothing**, and its LLM strategy is enabled
    by default and unreachable in practice (`theloom/semantic/entity_proposer.py:554-576`,
    `:108`, `:121-123`). Violation semantics also travel as prose and are recovered by regex
    (`:63-66`, `:202`, `:290`).
14. **Verification is inconsistent about unknown names.** An unknown invariant name is a hard
    error in one command and a silent skip in another
    (`theloom/operations/verification.py:202-208` vs `:294-297`); an unrecognised coupling
    metric silently falls back to degree centrality (`theloom/verification/metrics.py:65`).
15. **Two of the frontend's proof surfaces are structurally duplicated.** Seven browser specs
    each re-implement the Python renderer's substitution in their own setup block
    (`tapestry/e2e/smoke.spec.ts:17-22` and six near-identical copies), and the shortcut sheet
    is a hand-maintained copy of bindings defined elsewhere
    (`tapestry/src/views/HelpOverlay.tsx:26`, `:42`).
16. **The command count is hand-copied into two documents while the catalog is generated**
    (`COMMANDS.md:5` vs `CLAUDE.md:8-9` vs `README.md:12-13`), and the repository layout is
    described three times with two copies already drifted (`CLAUDE.md:65-67`,
    `README.md:362-364`, `CONTRIBUTING.md:112-114`).

Resolved since the previous edition: the contributor home path baked into the committed
deliverables (previously `map-manifest.json:3`, `QUERYING.md:13`, `ARCHITECTURE-MAP.md:1387`)
is gone — the manifest now records `"projectPath": "."` and both documents use placeholders.

---

## 7. Open seams

Pairs the graph finds semantically close but structurally unconnected — places where two
parts of the system are talking about the same thing without a link between them.

- **Duplicate invariants written twice, once orphaned.** "Entity identity is never encoded by
  colour alone" exists as two records, one linked to `tapestry/src/design/tokens.css` and one
  attached to nothing (similarity 0.86). "PNG export must call refresh synchronously before
  reading the canvases" likewise exists in two near-identical wordings (0.82). Both are
  artifacts of the design group being enriched twice.
- **The read port's own test names diverge from the port's method names.**
  `test_get_relations_narrows_by_relation_type` clusters with
  `test_read_relation_narrows_by_relation_type` and
  `test_get_relations_narrows_by_direction` (0.76–0.79) — three tests of one narrowing rule
  whose names imply three different surfaces.
- **`in_edge_ids` / `out_edge_ids` / `node_edges` in `theloom/graph/hydrate.py`** (0.76–0.79)
  are three adjacency accessors with no shared implementation; the same is true of
  `Embedder.embed_document` / `embed_documents` (0.78).
- **The same convention is stated separately in four view directories.** "Refs as the live
  channel into the render loop" and "Refs mirror store state so reducers read current values"
  (0.72–0.77) describe one kernel implemented four times. Likewise "Scale-gated degradation
  with explicit node-count thresholds" and "Scale-gated level of detail" (0.76), and two
  descriptions of per-graph namespaced storage (0.76).
- **Explorer selection state has four near-synonymous names** — `selection`, `select`,
  `selected`, `selectionRef` (0.76–0.77) — inside one component.
- **Inference trace list and get** (`theloom/operations/inference.py`, 0.76) and the
  extraction filter include/exclude tests (0.76) are symmetrical pairs with no shared helper.
- **`Event` and `EventLog`** (`theloom/store/events.py`, 0.75) sit close in meaning; the
  record type and its append-only log are defined independently.

---

## 8. Coverage & methodology

**Coverage.** 45 of 45 module groups were read and written up; none was skipped. The groups
are, by their identifiers in the graph:

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
repo-root-1  repo-root-2  docs  docs-architecture
```

Ten further labels survive from earlier runs and still carry records: `docs-1` … `docs-4`,
`root-1`, `tests-fixtures`, and the coarser frontend partition `tapestry-src-1` …
`tapestry-src-4`. Their content overlaps the current groups and describes files that have
since moved; prefer the identifiers listed above. The mixed vintage is visible in §3 and §6:
records for `docs/superpowers/` are still active although the directory no longer exists.

**Not parsed.** 43 files could not be parsed by tree-sitter and are recorded as file entries
carrying no symbols — chiefly data, lockfiles and stylesheets, which have no symbol grammar in
this pipeline. They still participate in documentation links and containment.

**Graph and commit.** Graph `codebase-the-loom`, commit
`21466d5250d7ce760079705305a422077e36f17d`, working tree clean at extraction, mode `full`.

**How to re-run.** `/map-codebase <repo-root>`. The run reads the commit recorded in
`docs/architecture/map-manifest.json` and re-extracts only what changed since; a group is
re-written only when one of its files moved. Re-running is the only supported way to edit any
file in `docs/architecture/`.

**How to interrogate the graph afterwards.** Start with
`docs/architecture/QUERYING.md`, which carries a runnable recipe per question. The two
highest-yield calls are `loom entity-deep-dive '{"name": "<symbol>", "compact": true,
"graph": "codebase-the-loom"}'` for everything known about one symbol, and
`loom hybrid-search '{"query": "<what you are looking for>", "graph": "codebase-the-loom"}'`
when you only know roughly what you want. `loom explore`, `loom find-callers`,
`loom find-callees` and `loom blast-radius` answer where-defined, who-calls, what-it-calls
and what-breaks-if-I-change-this in one call each.

**The visualization.** `docs/architecture/codebase-map.html` is a self-contained page holding
the 400 highest-degree records and the 1,921 relationships among them, with analytics and
event-replay sections attached. It is generated and gitignored; regenerate it by re-running
the map.
