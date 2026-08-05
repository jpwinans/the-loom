---
repo: the-loom
commit: 067a5b833e3f9e9ca898288403312140169f8df5
graph: codebase-the-loom
generated: 2026-08-05
mode: full
---

# The Loom — Architecture Map

## 1. Executive overview

The Loom is a knowledge-graph substrate with a single JSON-in / JSON-out command
line interface. One store — FalkorDB — holds the graph, the entity vectors, the
document chunks and the full-text index, and every mutation is a single atomic
statement paired with an append to an event log, so history is real and *state as
of time T* is a first-class read. Above that store sits a layered Python package:
a Pydantic domain model that is the single source of truth for every record shape,
a semantics-free storage layer, an operations layer that adds the rules (polarity
belongs to causal edges only, retracted entities cannot be relation endpoints,
updates invalidate and never overwrite), pure algorithm libraries for graphs,
semirings, symbolic math and synthesis, and a command registry from which the
entire CLI and its published catalog are generated. A second, self-contained
workspace — Tapestry — is a single-file React/sigma.js visualization that reads a
schema-versioned bundle assembled by the same Python operations it visualizes.
The two halves meet at exactly one artifact: a built HTML template with a data
sentinel that the Python side substitutes at emit time.

### Stats

| Measure | Value |
|---|---|
| Files described | 323 |
| External packages referenced | 63 |
| Symbols described (functions, methods, classes, types, module constants) | 4,000 |
| — functions and methods | 2,219 |
| — module-level constants and typed variables | 1,378 |
| — classes, interfaces and type aliases | 403 |
| Written architectural notes (purposes, patterns, invariants, risks) | 813 |
| Total records in the graph | 5,256 |
| Total relationships | 11,039 |
| — containment (`part_of`) | 4,497 |
| — call edges | 2,981 |
| — semantic links (`related_to`) | 1,944 |
| — import/dependency edges (`requires`) | 1,422 |
| — instance-of (pattern instances) | 195 |
| Language mix (by file) | Python 208, TypeScript 73, Markdown 14, JSON 14, CSS 9, JavaScript 2, YAML 1, TOML 1, lockfile 1 |
| Files not parsed | 40 |
| Module groups enriched | 38 of 38 |

The working tree was clean at extraction time, so this map describes commit
`067a5b83` exactly.

The 40 unparsed files are the formats the structural parser has no grammar for —
CSS, some JSON, and a handful of assets. Markdown, JSON, YAML, TOML and the
lockfile are still present as file records with written notes attached; they carry
no symbol-level detail.

---

## 2. Subsystem walkthrough

### 2.1 The Python core

#### `theloom` (root package)

The contract layer everything else is written against. `model.py` (545 lines)
defines every entity and relation shape in Pydantic and does the snake/camel
translation at the wire boundary; `config.py` resolves configuration exactly once
through one loader; `errors.py` gives every failure a structured code from birth;
`timeutil.py` owns the canonical timestamp format; `migrate.py` imports snapshots.

The shapes are stated as invariants, not conventions. Every wire timestamp is ISO
8601 UTC with millisecond precision and a `Z` suffix (`theloom/timeutil.py:12-15`,
regex-anchored at `theloom/model.py:31`). Confidence is bounded to [0,1] at both
the field and the label boundary (`theloom/model.py:341`, `:445`, `:265-277`).
Unknown fields are rejected everywhere — every wire model forbids extras
(`theloom/model.py:332-335`). The five-state lifecycle is explicit: retracted is
terminal, and only *investigating* may return to active
(`theloom/model.py:285-309`). A `volatile` record must carry an expiry
(`theloom/model.py:404-409`). Errors carry their code from birth and the CLI never
classifies by message text (`theloom/errors.py:12-19`, `:32-53`). Configuration
precedence is flags > env > file > defaults, resolved in one pass
(`theloom/config.py:149-218`). Snapshot import wipes the prefix first, which is
what makes it idempotent (`theloom/migrate.py:35`).

#### `theloom/store` — the one transactional store

`falkor.py` (1,208 lines) is the whole storage model and the mutation primitive;
`base.py` is the abstract interface that documents the contract; `events.py` is
the per-graph Redis Stream event log; `multigraph.py` manages named graphs and the
cross-graph bridge registry; `filters.py` is the semantics oracle for filtering.

Every mutation funnels through one single-statement commit so the write and its
event-log entry land together or not at all
(`theloom/store/falkor.py:231-268`, `:286-312`). Updates snapshot the prior
incarnation into a version node rather than overwriting it
(`theloom/store/falkor.py:536-553`); deletion invalidates by default and `hard=True`
is the only path that destroys history (`:555-612`, `:1070-1091`). Retracting an
entity also nulls its vector, which is precisely why it leaves the semantic reads
(`:594-596`). Server-side filtering is only ever a *superset* prefilter — the
Python `filters.py` path remains the semantics oracle, and the server limit is
applied only when the pushdown is provably exact (`:129-148`, `:744-772`). Any
full-scan read must page or FalkorDB silently truncates it
(`theloom/store/paging.py:1-11`, `:24-45`). Because Redis cannot roll back at
`EXEC`, all-or-nothing relation batches are bought with an explicit compensating
write (`theloom/store/falkor.py:841-894`). The vector index is written once and
sized from stored vectors, never from a query (`:367-404`, `:455-468`), and a
create-then-query is only correct behind an operational barrier (`:406-430`).

#### `theloom/operations` — the rules layer (three groups)

Twenty-two modules that wrap the semantics-free store with the domain's rules.
Group 1 is the shared plumbing and the read surface: `common.py` (the
`CommandInput` base, the name-first entity resolver), `consumption.py` (the
`explore` / `find-callers` / `blast-radius` family and the truncation contract),
`analysis.py` (16 traversal and analytics commands), `algebra.py`, `bulk.py`.
Group 2 is the write surface: `entity.py`, `relations.py`, `epistemic.py`,
`inference.py`, `merge.py`. Group 3 is the outward-facing surface:
`semantic.py` (1,121 lines, the largest), `synthesis.py`, `verification.py`,
`solve.py`, `work_memory.py`.

Addressing takes exactly one of an id or a name, and a blank name is not a name
(`theloom/operations/common.py:115-120`). Budgeted reads are honest about what
they dropped: shown plus cut equals total, and the first row of every populated
section is unconditional (`theloom/operations/consumption.py:319`, `:429-449`).
Neighbourhood reads apply their own active-status filter (`:265-281`).
Blast radius counts the seed and its members as seeds, never as fallout
(`:661-670`, `:678-689`). Every ops-layer update bumps the version and
self-references the previous version — the store deliberately does not
(`theloom/operations/entity.py:266-267`). Retracted stays terminal, gated before
any write (`:244-248`). The causal/polarity partition is an invariant of the
*stored* edge, not just of creation (`theloom/operations/relations.py:308-338`),
and a failing strict batch still persists its valid prefix (`:266-269`). Merging
supersedes rather than deletes, and a re-merge is a no-op
(`theloom/operations/merge.py:172-175`). On the read-and-search side, similarity
is `1/(1+L2)` and never raw cosine (`theloom/operations/semantic.py:115-117`),
embedding is opt-in and content-hash idempotent (`:363-389`, `:449-456`), and
`record-outcome` writes nothing at all if any citation is bad
(`theloom/operations/work_memory.py:103-123`, `:162-169`). Mutation-trace
validation never touches the target graph — it clones to a temp graph and replays
there (`theloom/operations/verification.py:624-636`, `:660-687`).

#### `theloom/cli` — the registry is the CLI

`registry.py` (1,688 lines) declares 163 command descriptors across 23 categories
plus `run_handler`; `app.py` generates the Typer commands from them; `io.py` is the
single JSON parse/format and typed-error boundary; `docs.py` projects the registry
into the published catalog.

Every command except `version` and `init` is generated from the registry
(`theloom/cli/app.py:108-109`). Input validation happens once, in `run_handler`,
and Pydantic failures become `VALIDATION_ERROR`
(`theloom/cli/registry.py:1678-1688`). Unknown input keys are ignored; only missing
required keys fail (`:72-75`). `stdout` carries exactly the result document,
diagnostics go to `stderr`, failures exit 1 (`theloom/cli/io.py:79-84`,
`theloom/cli/app.py:96-102`). `COMMANDS.md` is byte-identical to `generate_docs()`
output and is regenerated, never hand-edited (`theloom/cli/docs.py:15-36`).
Non-finite floats serialize as `null` so the output stays valid JSON
(`theloom/cli/io.py:56-69`).

#### `theloom/graph` — pure graph algebra

`hydrate.py` builds an insertion-ordered multigraph from wire documents;
`analytics.py` does centrality and components; `cycles.py` does DFS cycles,
Johnson circuits and loop classification; `paths.py` does bidirectional BFS and
bounded DFS; `motifs.py` mines frequent subgraphs.

Hydration drops dangling relations so no edge can reference an absent node
(`theloom/graph/hydrate.py:118`). Neighbour iteration is deduplicated and
order-fixed at IN-then-OUT (`:87-96`). Loop polarity is the parity of negative
edges, with missing polarity read as positive
(`theloom/graph/cycles.py:258-267`), and `classify_loop` demands a closed path of
length ≥ 3 (`:244-257`). PageRank converges to its stated tolerance or raises,
rather than returning provisional scores (`theloom/graph/analytics.py:55-68`).
Motif identity is the canonical signature; the pattern id is only a per-response
ordinal (`theloom/graph/motifs.py:38-41`, `:164-167`).

#### `theloom/algebra` — semirings and routing

`core.py` holds the semirings, extractors and the single DFS engine;
`routing.py` holds the category registry, the morphism table, query analysis and
the three execution strategies.

Traversal is a backtracking DFS, not Bellman-Ford: value and path are decoupled
(`theloom/algebra/core.py:191-207`). Adjacency emission order is part of the
public contract — ties keep first discovery (`:141-148`, `:5-7`). Relation
categorization is total, with causal as the open-world default
(`theloom/algebra/routing.py:31-49`). The only approximate morphisms are the
tropical/viterbi pair, and they are labelled as such (`:64-68`, `:94-103`).
Segmented execution short-circuits on first arrival (`:311-316`, `:399-401`), and
a missing source yields an empty result map rather than an error
(`theloom/algebra/core.py:234-235`, `:265-266`).

#### `theloom/extraction` — how a repository becomes a graph

`treesitter.py` (1,193 lines) is the per-file parser and the public API;
`resolution.py` is the whole-project join that turns names into edges;
`codebasediff.py` is the incremental replay; `selfmodel.py` drives the
self-model; `pipeline.py` is the separate LLM document path.

An incremental update supersedes entities; it never deletes them
(`theloom/extraction/codebasediff.py:371-381`). A structural edge belongs to a
changed file when either endpoint does (`:225-244`). The structural diff never
touches the semantic layer's `related_to` edges (`:66-70`, `:230`, `:242`) — this
is what keeps a re-run from wiping the written notes. Cross-file resolution
refuses to guess: an ambiguous symbol name resolves to no edge at all, filtered
first by language and callability (`theloom/extraction/resolution.py:411-422`).
Extraction output is deterministic for a given tree (`treesitter.py:1038-1066`)
and git, not the filesystem, decides what counts as in the codebase. The
self-model update refuses any repository that is not The Loom
(`theloom/extraction/selfmodel.py:30-62`).

#### `theloom/semantic` — embeddings and proposal gating

`embed.py` owns the embedding contract and the process-wide embedder singleton;
`deduplication_gate.py` is the similarity gate for proposals;
`entity_proposer.py` is the pattern-completion and LLM proposal engine.

Every stored vector is L2-normalized before it leaves the embedder
(`theloom/semantic/embed.py:83-87`). Documents and queries get different prefixes
(`:28-29`, `:90-99`), and embedding text is truncated at 30k characters
(`:45-57`). The dedup gate only compares proposals against entities of the same
type (`theloom/semantic/deduplication_gate.py:124-130`) and clamps its threshold
to [0.5, 0.99] regardless of caller input (`:94-97`). The proposer is read-only:
it returns proposals and never writes (`theloom/semantic/entity_proposer.py:96-152`),
and LLM-proposed types are allowlist-validated before they can become proposals
(`:44-61`, `:478`).

#### `theloom/verification` — the gates

`checks.py` holds the read-side guards, invariants and cycle detection;
`guards.py` is the mutation gate; `capability_spec.py` is the capability DSL;
`propagation.py` is AC-3 constraint propagation.

Polarity belongs to causal relation types only, enforced on write and mirrored on
read (`theloom/verification/guards.py:64-71`, `checks.py:92-129`). Retracted
entities read back but cannot be relation endpoints (`guards.py:97-107`). The
endpoint verdict takes a *status*, not a store, which is what stops single and
batch writes from diverging (`guards.py:81-101`). The entity gate warns without
blocking; the relation gate errors and blocks (`guards.py:41-78`). Read-side
guards judge only fields that are present — absence is the model's job
(`checks.py:41-95`). AC-3 pops its worklist LIFO, and that order is part of the
response (`propagation.py:104`).

#### `theloom/synthesis` — plan, traverse, realize

`planner.py`, `traverser.py`, `realizer.py`, `cegis.py` and `llm.py` form a staged
pipeline over plain wire dictionaries, with the LLM optional at every call site.

Output is fully deterministic when no LLM is configured
(`theloom/synthesis/llm.py:215-218`, `realizer.py:314-317`). The PRNG is a
bit-exact 32-bit mulberry32, so a seed determines the candidate graph exactly
(`theloom/synthesis/generator.py:28-60`). CEGIS verification touches no store;
only a successful commit does (`theloom/synthesis/cegis.py:368`, `:129-163`), and
the loop always terminates (`:382-418`, `:73-74`). Generated graphs contain no
self-loops and no duplicate `(from, to, relationType)` triples
(`generator.py:374-411`). Provenance is append-only and sealed at finalize
(`traverser.py:51-56`, `:77-86`).

#### `theloom/analysis` — analogy and transfer

`cwsg.py` composes the transfer pipeline; `crossdomain.py` produces the structural
role mapping every analogy starts from; `slippage.py` handles temperature-driven
concept slippage; `absence_surprise.py` scores what the analogy predicts but the
target lacks; `adaptability.py` is the accept/warn/reject gate.

Novel endpoints are `__NOVEL__` placeholders, never graph ids
(`theloom/analysis/cwsg.py:31`, `:110-119`). Only relations attached to the matched
relational core transfer (`:81-85`, `:370-393`). Cross-domain mapping is strictly
one-to-one (`crossdomain.py:196-220`). Temperature is clamped to [0,1] and lowers
the slippage threshold monotonically (`slippage.py:37`, `:54-56`). Absence
surprise reports the maximum absence, not the average
(`absence_surprise.py:359-361`). Adaptability verdicts align positionally with
proposals (`adaptability.py:167-173`).

#### `theloom/exploration` — where to look next

`composite_signals.py` combines the weighted signals; `guards.py` holds six
anti-pattern detectors; `exploration_state.py` holds visit and gain state;
`coverage_gap.py` is the embedding-ranked signal.

Every signal score is clamped to [0,1] (`age_staleness.py:98-99`,
`bridging_potential.py:83`). Absent signals are dropped and the weights
renormalized — never treated as zero (`composite_signals.py:70-89`). Missing
evidence scores as maximally explorable, not as zero
(`age_staleness.py:92-93`, `composite_signals.py:50-51`). Region identity is the
smallest entity id in sorted order (`exploration_state.py:89-97`), region state is
derived at query time and never persisted (`:63-76`, `:153-193`), and gain
histories are bounded ring buffers of 100 entries (`:30-34`, `:127-144`). The
quadratic coverage-gap cost is bounded by capping vectors per region at 500
(`coverage_gap.py:27-28`, `:116-122`).

#### `theloom/documents` — ingestion and chunking

`ingestion.py` orchestrates; `chunker.py` is the chunking algorithm; `parsers.py`
normalizes every format to one block shape; `chunkstore.py` persists to FalkorDB;
`ssrf.py` is the egress guard.

A chunk's content hash covers the overlap prefix, not just the body
(`theloom/documents/chunker.py:205-229`). Groups containing code or list blocks are
exempt from the size limit — atomic blocks beat it (`:165-167`, `:76-81`). Chunk
indices are zero-based, dense and contiguous after merges (`:233-234`). Every
fetch hop requires *all* resolved addresses to be globally routable, revalidated
on each redirect (`theloom/documents/ssrf.py:58-80`). Chunks live in one
per-prefix chunk graph, global across knowledge graphs
(`theloom/documents/chunkstore.py:27`). Reingest preserves chunk identity and
skips unchanged chunks (`theloom/documents/ingestion.py:300-333`), and `sourceId`
is a deterministic sha256 prefix of the resolved path, URL or caller id
(`:48-54`, `:224`).

#### `theloom/reification` — structural fingerprints

One 163-line module, `fingerprint.py`, implementing Weisfeiler-Leman colour
refinement by recursive hashing.

Fingerprints are order-independent: equal structure implies equal digest
(`theloom/reification/fingerprint.py:49-53`, `:75-78`). Depth is clamped to
[0, MAX_DEPTH_LIMIT] at every public entry point (`:93`, `:133`). The module is
pure — it hydrates and reads, never mutates (`:10`, `:15-18`). Memo cache keys
carry the depth, so one cache is safe across mixed-depth calls on one graph
(`:57`, `:81`).

#### `theloom/symbolic` — the SymPy bridge

One 1,026-line module, `core.py`, dispatching 21 operations through a registry
table with a two-level umbrella/sub-operation scheme.

`core.run` returns an envelope rather than propagating handler exceptions
(`theloom/symbolic/core.py:1017-1022`). Every successful handler returns `result`
and `latex_result` as strings (`:61-75`). The watchdog is clamped to 1–120 seconds
and always restores prior signal state (`:1008-1009`, `:1023-1025`). `op_verify`
selects its mode by parameter shape, in a fixed precedence (`:138-141`,
`:200-202`).

#### `theloom/composites` — the orchestration commands (two groups)

Multi-step commands that chain existing operations behind one timed-section
envelope. Group 1: `framework.py` (the shared envelope), `far_analogy_retrieval.py`
(five chained sections), `entity_deep_dive.py`, `gap_fill_cycle.py`,
`hypothesis_engine.py`. Group 2: `self_improve.py`, `simulate_change.py`,
`reflect.py`, `influence_map.py`, `verified_extract.py`.

`time_section` never raises — every section outcome is a three-key result
(`theloom/composites/framework.py:39-53`), a non-null section error always
accompanies `data: None` (`:49-58`), and aggregate counts are derived from the
error field rather than tracked separately (`:65-66`). `framework.py` imports
nothing from `theloom`, which is what prevents a layering leak (`:15-22`).
Composite handlers are pure orchestrators: no domain algorithm is defined in the
package (`far_analogy_retrieval.py:39-60`). `gap-fill-cycle` is the only
graph-mutating composite in its group and every write passes the structural gate
(`gap_fill_cycle.py:229-243`); a *skipped* consistency check must not veto a
commit, only an explicit failure blocks (`:226`, `:237-238`). `simulate-change`
never mutates the graph it is asked about — it clones to a disposable graph
(`simulate_change.py:100-110`, `:246-249`). `self-improve` is read-only unless
`autoApply` is explicitly true (`self_improve.py:299-301`, `:53`).

#### `theloom/viz` — one assembler, three transports

`bundle.py` is the single assembler; `schema.py` is the wire contract;
`scope.py` selects the slice; `html.py` emits the static single file;
`serve.py` is the optional live mode.

Every emitted bundle is a schema-versioned, model-validated `TapestryBundle`
(`theloom/viz/bundle.py:136-155`, `schema.py:13`). Injected bundle JSON can never
terminate the template's script block (`theloom/viz/html.py:33`), and a missing or
unbuilt frontend template fails as a typed config error (`:28-44`). Degree
truncation is deterministic and always disclosed in the metadata
(`bundle.py:66-76`, `:114-118`). The bundle ships entities of every status, not
just active ones (`scope.py:44`, `:60-72`). Live mode is read-only — every route
is a GET (`serve.py:106-214`) — and its HTTP status comes from a typed-code lookup
table, never from prose (`serve.py:27-34`, `:94-101`). A graph with fewer than
three vectors omits the semantic section entirely (`viz/semantic.py:19`, `:65-67`).

### 2.2 Tapestry — the visualization workspace

#### App shell and view-agnostic libraries

`lib/BundleContext.tsx`, `lib/data.ts`, `App.tsx`, `design/tokens.css`,
`lib/exportSvg.ts`. The organizing habit here is *pure core, impure shell*: every
browser-coupled behaviour has a testable pure module behind it.

Live / static / dev mode is decided by parsed *shape*, never by comparing against
the sentinel literal (`tapestry/src/lib/data.ts:33-45`, `lib/live.ts:1-29`). Views
never observe a null bundle or a null graph
(`lib/BundleContext.tsx:66-68`), and the bundle hooks throw outside a provider
rather than returning a default (`:109-113`). A drag's `moved` latch is sticky and
the trailing click it suppresses is consumed exactly once
(`lib/dragState.ts:46-57`, `lib/dragNodes.ts:85-159`); the normalization box is
frozen for the gesture so mid-drag re-renders cannot rescale the graph
(`lib/dragNodes.ts:95`, `:120`). Every entity type resolves to a defined colour
token (`design/palette.ts:7-39`, `design/tokens.css:99-119`). Global shortcuts
never fire while the reader is typing (`lib/keyboard.ts:20-39`). SVG export
serializes only what is on screen and re-checks edge endpoints
(`lib/exportSvg.ts:139-190`); PNG export must call `sigma.refresh()` synchronously
before reading the canvases (`:284-299`). Saved-view reads and imports never throw
(`lib/savedViews.ts:25-122`). The URL hash is a continuously maintained projection
of store state (`App.tsx:243-253`).

#### State, Chronicle and the replay engine

`state/store.ts`, `state/urlHash.ts`, `views/chronicle/replay.ts`,
`views/chronicle/Chronicle.tsx`, `views/chronicle/EventList.tsx`. A pure replay
engine sits behind an impure view, and sigma reducers act as a non-destructive
projection layer.

Retraction replays as a status change, never as a node removal
(`views/chronicle/replay.ts:150-169`). An item with no creation event is present
from the start of the timeline (`:231-232`, `:250-251`). An edge is visible only
when both endpoints are (`:248-256`). The timeline's start and end are never equal
(`:208-212`). The diff window is half-open and order-independent (`:266-291`), a
node wears exactly one diff badge, and the summary counts match the canvas
(`Chronicle.tsx:386-401`, `:493-502`). `parseHash` is total — a malformed fragment
yields an empty patch (`state/urlHash.ts:15-22`). The help dialog traps focus while
open and hands it back on close (`views/HelpOverlay.tsx:70-116`).

#### Explorer, Overview and the Semantic Map

`views/explorer/Explorer.tsx` (the weave and its interaction layers),
`views/explorer/buildGraph.ts` (bundle to graph model plus every visual encoding),
`views/explorer/filters.ts`, `views/overview/stats.ts`,
`views/semantic/SemanticView.tsx`.

Visibility and emphasis never mutate the shared graph — they are computed per
frame (`views/explorer/filters.ts:45-62`). Dangling relations are skipped when
rendering but counted as a health signal (`views/explorer/buildGraph.ts:197-198`).
Entities without a confidence score always pass the confidence floor and are
reported separately (`filters.ts:36-40`, `FilterPanel.tsx:137`). `ENTITY_TYPES` is
the single ordering authority for every type list in every view
(`views/explorer/legendRows.ts:23-34`). Every sigma instance, layout driver and
listener is destroyed in its effect cleanup (`Explorer.tsx:299-307`,
`SemanticView.tsx:274-279`). Path search is undirected for reachability, but every
rendered hop keeps its true edge direction (`views/explorer/pathMode.ts:25-57`).
Pre-layout node positions are a deterministic function of the entity id
(`buildGraph.ts:138-178`).

#### Systems view and semantic geometry

`views/systems/systems.ts` (pure causal model helpers),
`views/systems/SystemsView.tsx`, `views/systems/LoopPanel.tsx`,
`views/semantic/semanticMap.ts`.

The Systems graph contains only causal-family relations and the entities they
touch (`views/systems/systems.ts:63-107`). Loop edge resolution is directed —
out-edges, never undirected edges (`:125-140`). `flowIntensity` is a wrapped
raised cosine in [0,1] with phase 1 identical to phase 0 (`:193-201`). Flow
animation exists only while a loop is isolated, and never spins an animation frame
loop under reduced motion (`SystemsView.tsx:358-370`, `:537-540`). The mount effect
releases every resource it acquired (`:316-327`, `:382-416`). The semantic scatter
is a subset: only entities with a 2-D projection coordinate appear
(`views/semantic/semanticMap.ts:33-38`), and hull and lasso geometry degrade to
empty or pass-through on degenerate input (`:71-123`).

#### Build and contract toolchain

`package.json`, `vite.config.ts`, `scripts/emit-template.mjs`,
`schema/bundle.schema.json`, `playwright.config.ts`.

The build is a three-stage gate: typecheck, then bundle, then emit
(`tapestry/package.json:8`). No template is emitted unless the data sentinel
survived bundling (`scripts/emit-template.mjs:4-8`), and
`theloom/viz/static/tapestry.html` is the only artifact that crosses from the Node
workspace into the Python package (`emit-template.mjs:8`). The bundle envelope is
closed while entity and relation payloads stay open
(`schema/bundle.schema.json:295`, `:304-319`). Vitest must never load the
Playwright specs (`vite.config.ts:10-12`). Live e2e assumes an externally started
server and runs serially (`playwright.live.config.ts:4-15`).

#### End-to-end suites and fixtures

`e2e/smoke.spec.ts`, `e2e/a11y.spec.ts`, `e2e/savedviews.spec.ts`,
`e2e/drag.spec.ts`, `e2e/export.spec.ts`, plus the committed
`fixtures/dev-bundle.json` golden bundle and the four-assertion live smoke.

The e2e suite renders through the same sentinel substitution as
`theloom/viz/html.py` (`e2e/smoke.spec.ts:18-21`). Each spec owns a distinct temp
artifact so parallel specs never clobber each other (`e2e/smoke.spec.ts:15` and
siblings). The accessibility gate is zero serious or critical violations, by
construction (`e2e/a11y.spec.ts:42-45`). A `#view` deep link only applies on a
genuinely fresh document load (`e2e/savedviews.spec.ts:125-140`). In the fixture,
analytics and semantic sections are id-keyed subsets, so views must tolerate
missing entities (`fixtures/dev-bundle.json:292-326`, `:1461-1498`); exported
relations carry polarity only for causal types while strength and evidence are
always present (`:224-290`).

### 2.3 Tests

`tests/` is four groups plus the fixture folder, and it is where most of the
system's invariants are actually pinned.

**Group 1 — CLI, consumption and contract.** Every live-store test is namespaced
and leaves the store as it found it (`tests/conftest.py:36-43`). Documented `loom`
invocations must validate against the live CLI input models
(`tests/test_claude_examples_contract.py:146-160`). Truncation is arithmetic, not
an apology: shown + cut == total (`tests/test_consumption.py:277-309`). Superseded
entities leave every read surface at once (`:165-179`, `:416-422`, `:540-548`). No
registered command may silently no-op (`tests/test_creativity_loop.py:24-29`).
Error codes come from the typed hierarchy, never from prose
(`tests/test_cli_io.py:78-85`).

**Group 2 — store, extraction and incremental update.** Every store mutation
appends exactly one typed event, in order (`tests/test_falkor_store.py:495-540`).
Deletion is bi-temporal close-out, not erasure (`:99-124`, `:369-392`).
`related_to` belongs to the semantic layer and structural extraction never emits
it (`tests/test_extraction_resolution.py:374-379`). No extracted relation may
reference a name that is not also an extracted entity (`:397-429`). Unique-name
call resolution is fenced by language, callability and ambiguity guards
(`:239-294`). Re-extraction is idempotent — a second run creates nothing and
retires nothing (`tests/test_extraction_legacy_calls.py:114-128`). A dry run
reports exactly the plan the applied run executes
(`tests/test_incremental_update.py:459-477`).

**Group 3 — operations semantics.** Deletion retracts by default and the record
stays readable (`tests/test_ops_entity.py:145-157`). Polarity belongs to causal
types only, at every writer (`tests/test_ops_relations.py:86-168`). The
verification gate runs before the bridge branch (`:211-219`). A retracted entity
is not a usable relation endpoint at any arity (`:191-208`, `:265-286`). Name
resolution refuses ambiguity rather than guessing
(`tests/test_name_addressing.py:99-151`). Parallel typed edges are native: dedup
and addressing are per relation type (`tests/test_ops_bulk.py:101-118`).

**Group 4 — atomicity, pushdown and the bundle.** A mutation and its event append
succeed or fail as one unit (`tests/test_store_atomicity.py:85-166`), and every
mutation is one Cypher statement precisely because Redis cannot roll back at
`EXEC` (`:190-244`). Server-side filter pushdown is semantically identical to the
Python path, proved over a 26-case matrix (`tests/test_store_pushdown.py:170-227`).
The committed Tapestry JSON Schema must equal the live Pydantic model
(`tests/test_viz_schema_drift.py:11-16`). Bundle HTML injection escapes the
script-close sequence (`tests/test_viz_html.py:23-28`). SSRF rejection happens
before any connection (`tests/test_ssrf.py:16-61`).

**Fixtures.** Snapshot seeds are byte-identical contracts, not merely valid
documents (`tests/fixtures/small/default.json:57-65`). The fixture repository is
frozen and its exact entity and relation counts are asserted; each cross-file
resolution idiom appears exactly once (`tests/fixtures/repo/src/service.py:3`,
`lib/index.ts:1`, `src/models.py:3`), and `src/policy.py` is a deliberate orphan —
defined, never imported (`tests/fixtures/repo/src/policy.py:3`).

### 2.4 Documentation and repository root

**Specs and plans (`docs/`).** Four groups of design documents and TDD-scaffolded
implementation plans. They are not decoration: a plan carries the artifact
verbatim rather than describing it, and every task is a red-green-gates-commit
cycle. The map-codebase design states the two-layer rule directly — a semantic
record is only in the map layer if it is stamped with `map_layer` and
`module_group`
(`docs/superpowers/specs/2026-08-03-map-codebase-design.md:152-155`) — and the
cartographer is read-only, so the map is a view and not a second source of truth
(`docs/superpowers/plans/2026-08-03-map-codebase.md:452-460`). Enrichment halts the
pipeline when records were attempted but none verified (`:113-120`). Every
documented `loom` example must be literal JSON because CI executes the docs as a
contract (`:19`). On the Tapestry side: `asOf` bounds entities, relations and the
event log only — analytics and semantic stay current
(`docs/superpowers/plans/2026-07-11-tapestry-phase-2.md:174-182`); every new visual
channel is redundantly encoded, never colour alone (`:98-102`); live mode never
becomes a required dependency of the core install
(`docs/superpowers/plans/2026-07-11-tapestry-phase-4.md:24-26`); and performance is
measured and recorded, never asserted in CI
(`docs/superpowers/plans/2026-07-11-tapestry-phase-5.md:104-107`).

**Repository root.** `pyproject.toml` is the sole manifest for dependencies, entry
points and all three quality gates (`pyproject.toml:10-78`); `mypy --strict` covers
only `theloom`, leaving scripts and tests as untyped territory (`:67-70`). Both
console entry points resolve to a single `main` (`:38-40`). FalkorDB persistence
depends on mounting the volume at `/var/lib/falkordb/data` — the incident that
taught this is recorded inline (`docker-compose.yml:9-14`). Seed and bench scripts
never import the embedder (`scripts/gen_bench_graph.py:15-18`). The lockfile is
the transitive closure of the declared dependencies, digest-pinned to a single
index (`uv.lock:26-27`, `:4215-4243`), and it materializes support for five Python
bands from 3.11 to 3.15 (`uv.lock:3-22`).

---

## 3. Load-bearing modules

Ranked by degree (how much of the codebase touches them) and by betweenness (how
much of the codebase must route *through* them).

| # | Module | Why it is load-bearing |
|---|---|---|
| 1 | `theloom/operations/common.py` → `CommandInput` | 149 command input classes extend it; it is the single base every CLI command's parameters inherit, plus the shared name-first entity resolver. |
| 2 | `theloom/store/falkor.py` | Highest by both degree and betweenness. 30 modules import it, it holds 76 symbols, and it is the only path from any operation to the database. |
| 3 | `theloom/store/multigraph.py` | Imported by 83 modules — the widest fan-in in the repository. Every command that needs a named graph gets it here. |
| 4 | `theloom/model.py` | Imported by 60 modules. The Pydantic domain model is the wire contract; nothing crosses the CLI boundary without it. |
| 5 | `theloom/cli/registry.py` | Third-highest betweenness. 163 command descriptors and `run_handler` — the junction between the Typer surface and every operation. |
| 6 | `tapestry/src/views/explorer/Explorer.tsx` | The largest frontend file (95 contained symbols, 21 imports). It is the Explorer weave plus all of its interaction layers. |
| 7 | `pkg:typing` | The most-required external package; a marker of how thoroughly the Python side is annotated rather than an architectural fact. |
| 8 | `theloom/viz/bundle.py` | Fourth-highest betweenness on only 4 contained symbols — it is a pure junction, the single assembler feeding all three visualization transports. |
| 9 | `theloom/operations/semantic.py` | 1,121 lines, 46 symbols: embedding, vector search, clustering and auto-relations all live behind this one module. |
| 10 | `theloom/operations/analysis.py` | 16 traversal and analytics commands (41 symbols) hydrating the graph for the pure algorithm libraries. |
| 11 | `theloom/operations/consumption.py` | 55 symbols and the whole agent-facing read surface (`explore`, `find-callers`, `find-callees`, `blast-radius`) plus the truncation contract. |
| 12 | `theloom/config.py` | High betweenness on 14 symbols — the single configuration resolution path every entry point crosses. |
| 13 | `tapestry/src/views/chronicle/Chronicle.tsx`, `systems/SystemsView.tsx`, `semantic/SemanticView.tsx` | The three other view shells; each concentrates its view's sigma lifecycle, overlays and reducers. |
| 14 | `theloom/store/base.py` | High betweenness for an abstract class: it is the documented contract the store is measured against (16 abstract methods). |
| 15 | `theloom/semantic/embed.py` | Every embedding in the system — entities, chunks, queries — passes through this singleton. |
| 16 | `tests/test_entity_proposer_foundation.py`, `tests/test_ops_relations.py` | The two highest-degree test files; they touch the widest slice of the operations surface. |

---

## 4. Dependency cycles

Eleven cycles were found. **All eleven are single-node self-loops — recursive
functions — and every one is intentional.** There are no multi-module import
cycles in this codebase.

| Cycle | Location | Verdict |
|---|---|---|
| `_hash_at_depth` → itself | `theloom/operations/reification.py` | intentional — Weisfeiler-Leman colour refinement recurses on depth. |
| `_hash_at_depth` → itself | `theloom/reification/fingerprint.py` | intentional — the extracted copy of the same recursion (see §6 on the duplication). |
| `_resolve_references` → itself | `theloom/symbolic/core.py` | intentional — `$reference` substitution recurses through nested structures. |
| `_jsonify` → itself | `theloom/cli/io.py` | intentional — recursive descent over dicts and lists to null out non-finite floats. |
| `_extract_calls` → itself | `theloom/extraction/treesitter.py` | intentional — unconditional descent through the syntax tree. |
| `_find_identifier` → itself | `theloom/extraction/treesitter.py` | intentional — same tree walk. |
| `_comment_notes` → itself | `theloom/extraction/treesitter.py` | intentional — same tree walk. |
| `_extract_require_calls` → itself | `theloom/extraction/treesitter.py` | intentional — same tree walk. |
| `_generic_json_to_blocks` → itself | `theloom/documents/parsers.py` | intentional — recursive descent over nested JSON. |
| `_js_string` → itself | `theloom/synthesis/prompts.py` | intentional — recursive string escaping. |
| `_substitute` → itself | `tests/test_claude_examples_contract.py` | intentional — recursive placeholder substitution in a test helper. |

Related risk worth noting even though it is not a cycle: several algorithms in
`theloom/graph` and `theloom/verification` recurse in Python on graph depth with
no depth guard (`theloom/graph/cycles.py:38-119`,
`theloom/verification/checks.py:184-210`). Half of `theloom/graph` uses explicit
stacks and half uses recursion, with no stated rule for which.

---

## 5. Communities vs. directories

The strongest structural finding is not a community — it is a *wall*.

**The codebase is two disconnected islands.** The dependency graph splits into 11
connected pieces; the largest (3,730 records) is the entire Python side, and the
second (1,366 records) is the entire TypeScript side. There is not a single edge
between them. That is correct and deliberate: the two halves communicate through
a *file*, not a symbol — `tapestry/scripts/emit-template.mjs` writes
`theloom/viz/static/tapestry.html`, and `theloom/viz/html.py` substitutes a
sentinel into it. Both sides state the sentinel-uniqueness rule independently, and
neither can typecheck the other. This also confirms that the resolver's
cross-language guard is doing its job: no invented Python↔TypeScript call edges
appear anywhere in the graph.

**The documentation forms six more islands, one per plan or spec cluster.** Each
`docs/` group (sizes 23, 21, 20, 20) and the lockfile group (15) is connected only
through its own written notes — no code edge reaches them. This means the plans
and specs are *not* linked to the code they specify; the only thing tying
`docs/superpowers/plans/2026-07-11-tapestry-phase-2.md` to
`tapestry/src/views/chronicle/replay.ts` is prose. That is a real seam: a plan can
drift from its implementation with nothing structural to notice.

**Three CSS files are fully isolated singletons** — `views/HelpOverlay.css`,
`views/chronicle/Chronicle.css`, `views/explorer/Explorer.css`. They are imported
at runtime but not through a parsed import, so nothing connects them to the
components they style. Two written risks confirm this is not merely a parsing
artifact: row geometry is duplicated across JS and CSS with circular ownership
(`views/chronicle/EventList.tsx:30-31` vs `Chronicle.css:510-523`), and theme
tokens are duplicated as hard-coded hex fallbacks in four modules
(`views/explorer/buildGraph.ts:63-69`).

**Similarity clustering found almost nothing, and that is informative.** Over a
500-record sample of 5,199, only two clusters formed above threshold: three
identically-named `multi` test fixtures in `test_cli_commands`, `test_viz_analytics`
and `test_viz_temporal` (similarity 0.73), and the two empty `__init__.py` files
in `theloom/` and `theloom/operations/` (0.70). Both are duplication findings, not
communities. The absence of larger semantic clusters means the directory structure
and the actual coupling structure agree — modules named for a concern really do
contain that concern. The one exception the written notes flag is
`tapestry/src/views/explorer/Explorer.tsx:451-486`, which concentrates pure
geometry and graph logic of exactly the kind its sibling modules exist to extract.

---

## 6. Risks and tensions

218 risks were recorded across the codebase. These are the ones a reviewer should
see first.

### Correctness and safety

1. **Two commands can never succeed.** `creativity-loop` builds its config, discards
   it and raises; `enrichment-crawl` does the same. Both are registered CLI
   commands. `theloom/composites/creativity_loop.py:114-115`,
   `theloom/composites/enrichment_crawl.py:59-72`.
2. **Auto-apply writes are non-atomic, against the one-atomic-mutation invariant.**
   `self-improve` with `autoApply` performs per-relation `try/except pass` and
   `contextlib.suppress` around credit propagation, so a partial apply is
   indistinguishable from a complete one.
   `theloom/composites/self_improve.py:343-433`.
3. **A committed mutation whose event append fails leaves the log short by design.**
   The query-failure branch compensates; the event-failure branch does not.
   `theloom/store/falkor.py:302-311`.
4. **Chunk writes bypass the event log the rest of the store is built on** —
   `MERGE+SET` and raw `DELETE`, no event append.
   `theloom/documents/chunkstore.py:43-60`, `:101-113`. Related: first ingest
   appends blindly while reingest diffs, so re-ingesting a file duplicates its
   chunks (`theloom/documents/ingestion.py:74-125`).
5. **Bridges are event-sourced graph data stored outside the event-sourced store**
   — a raw Redis list with no event log. `theloom/store/multigraph.py:39-128`.
   Extraction run records have the same problem
   (`theloom/extraction/runstore.py:20-39`).
6. **The SSRF guard resolves DNS separately from the connection it protects** — a
   documented, residual time-of-check/time-of-use window.
   `theloom/documents/ssrf.py:7-9`, `:74-95`.
7. **Two files disagree on whether retraction removes an entity's vector.**
   `tests/test_store_atomicity.py:247-261` vs `tests/test_semantic_perf.py:177-191`.
8. **Inferred edges enter a graph whose consumers treat every edge as fact.** The
   unique-name call resolver's hazard is stated in its own docstring, and its
   guard is a hand-maintained blocklist of ~60 built-in names added after a
   concrete false positive. `theloom/extraction/resolution.py:19-22`, `:62-126`.
9. **`sympify` on caller-controlled strings assumes a trusted caller.**
   `theloom/symbolic/core.py:48`, `:772-776`, `:916-918`.
10. **The never-raises guarantee has a hole outside the main thread** — the signal
    installation sits above the exception-catching try.
    `theloom/symbolic/core.py:998-1016`.

### Invariant erosion

11. **Hard-delete escape hatches sit inside an event-sourced, bi-temporal store** —
    at the entity, relation, inference-rule and work-memory layers, each with its
    own justification. `theloom/operations/entity.py:318-335`,
    `theloom/operations/relations.py:364-383`,
    `theloom/operations/work_memory.py:166-169`.
12. **Soft-fail envelopes bypass the typed-error-code invariant.** `solve-problem`
    swallows into an envelope; `verification` raises typed.
    `theloom/operations/solve.py:363-364` vs
    `theloom/operations/verification.py:200-205`.
13. **An untyped `RuntimeError` escapes a codebase built on typed error codes.**
    `theloom/graph/analytics.py:68`. Similarly, a bare `assert` guards a runtime
    precondition at `theloom/extraction/pipeline.py:124-125` eleven lines above the
    correctly-typed idiom.
14. **Tests substring-match error prose while the codebase forbids classifying
    errors that way.** `tests/test_incremental_update.py:494-547`,
    `tests/test_extraction_units.py:518`.
15. **Two error-classification policies inside one operations layer** — `isinstance`
    dispatch in `documents.py:154-162`, prose reasoning documented in
    `analysis.py:1-7`.
16. **Two confidence write paths; only one records a revision.**
    `theloom/operations/epistemic.py:893-912` vs
    `theloom/operations/entity.py:266-267`.
17. **Bulk import guesses ambiguous names; the shared resolver refuses to.**
    `theloom/operations/bulk.py:318-327` vs `theloom/operations/common.py:136-142`.
    A second copy of the problem lives at `theloom/operations/entity.py:410-425`
    ("last one wins on duplicate names").
18. **The model enforces two invariants but only advises on the lifecycle table** —
    `VALID_TRANSITIONS` has no validator hook, unlike the volatile-expiry rule.
    `theloom/model.py:285-324` vs `:404-409`.

### Silent degradation

19. **Blanket exception capture buys resilience and costs diagnosability**, in at
    least four packages: `theloom/composites/far_analogy_retrieval.py:282-336`,
    `theloom/composites/influence_map.py:133-191` (three silent `continue` paths,
    none recording a skipped count), `theloom/analysis/cwsg.py:164-166`,
    `theloom/synthesis/fidelity.py:152-153`.
20. **Machinery decoded from observations disappears silently when malformed** —
    inference rules and traces that fail to parse are dropped from the list.
    `theloom/operations/inference.py:132-181`.
21. **A timed-out subgraph search looks identical to a complete one.**
    `theloom/analysis/isomorphism.py:243-264`.
22. **Directory ingest records per-file errors and then strips them before
    returning.** `theloom/documents/ingestion.py:176-193`.
23. **`extraction-rollback` reports counts it cannot guarantee** — two swallowed
    excepts and a hardcoded `deletedLinks: 0`.
    `theloom/operations/extraction.py:260-290`.

### Dead or inert surface

24. **Elaborate scoring machinery that computes to a constant zero in template
    mode.** `theloom/composites/gap_fill_cycle.py:89-104`,
    `theloom/composites/hypothesis_engine.py:234-236`.
25. **Stateless-by-design exploration state leaves UCB and MVT informationless per
    run** — the bonus is a constant because visits are always zero.
    `theloom/exploration/exploration_state.py:100-113`,
    `composite_signals.py:50-51`.
26. **Step 4 of the proposal pipeline filters nothing**, though the surrounding
    comment promises filtering and a `filteredCount` is reported.
    `theloom/semantic/entity_proposer.py:554-576`, `:128-131`.
27. **`maxCandidates` is accepted, documented, and inert** — the slice is a no-op.
    `theloom/semantic/deduplication_gate.py:136-137`.
28. **The LLM reasoning strategy is on by default and unreachable in practice**, as
    its own docstring admits. `theloom/semantic/entity_proposer.py:10-13`, `:108`.
29. **LaTeX is an output format but never a working input format** — the parse
    branch is dead. `theloom/symbolic/core.py:7-8`, `:36-43`.
30. **Source passages are structurally supported but permanently empty** —
    `links.py` returns `[]` and the store it reads is process memory.
    `theloom/synthesis/links.py:13-14`.
31. **Cross-graph bridge creation is code the CLI can never reach** — the gate runs
    first and always refuses. `theloom/operations/relations.py:201-208`.
32. **Document commands accept a `graph` parameter and ignore it**, in eight places.
    `theloom/operations/documents.py:84-146`.

### Performance and scale

33. **Full-graph in-memory hydration versus server-side filtered reads** — the
    analytics and algebra helpers list everything, unfiltered.
    `theloom/operations/analysis.py:60-63`, `theloom/operations/algebra.py:51-54`.
34. **`analyze-category` clusters with all-pairs cosine on up to 10,000 chunks** in
    pure Python. `theloom/operations/documents.py:354-359`, `:265-266`.
35. **The dedup gate brute-forces every vector in Python while the store owns a
    vector index.** `theloom/semantic/deduplication_gate.py:108-130`.
36. **Every capability check re-lists the entire graph** — four independent full
    listings per validate. `theloom/verification/capability_spec.py:82-152`.
37. **Cross-graph lookup costs a scan over every graph, once per store
    construction.** `theloom/store/multigraph.py:186-225`.
38. **Full-graph cloning buys perfect isolation at O(graph) cost per simulation.**
    `theloom/composites/simulate_change.py:100-110`. Best-effort cleanup can leave
    `sim-<uuid>` graphs visible in the ecosystem view (`:309-312`).
39. **Metapath expansion has no cycle guard and no frontier cap.**
    `theloom/algebra/routing.py:525-596`.
40. **The incremental update is incremental only in its writes** — it re-extracts
    and re-lists the whole project first.
    `theloom/extraction/codebasediff.py:407`, `:170`, `:229`.
41. **Chunk queries apply the row limit before the category filter**, so a filtered
    query can under-return. `theloom/documents/chunkstore.py:68-96`.

### Duplication and drift

42. **Two copies of the Weisfeiler-Leman hash** — the extracted shared module and
    the frozen inline copy in `reify-patterns`, kept in sync by hand.
    `theloom/reification/fingerprint.py:5-8`.
43. **The `multi` test fixture is copy-pasted into eleven modules** (twice over —
    once in group 3, once in group 4), while `conftest.py` stops at `namespace`.
    `tests/conftest.py:17-55` vs `tests/test_viz_bundle.py:16-18` and ten siblings.
44. **Seven copies of the artifact-building `beforeAll` re-implement the Python
    renderer** in the e2e suite. `tapestry/e2e/smoke.spec.ts:17-22` and six
    siblings.
45. **Two cosine implementations in one package, one silently zero on dimension
    mismatch.** `theloom/semantic/embed.py:109-115` vs
    `theloom/semantic/deduplication_gate.py:43-55`.
46. **Two semiring resolvers with deliberately divergent semantics**, one of which
    its own docstring calls "an intentional quirk, kept".
    `theloom/algebra/core.py:85-105`.
47. **Structural helpers duplicated rather than shared across the analysis layer.**
    `theloom/analysis/absence_surprise.py:54-91` vs `slippage.py:59-70`.
48. **The generated catalog and the prose docs disagree on the size of the CLI.**
    `COMMANDS.md:5` versus `README.md:12` and `CLAUDE.md:8-12`.
49. **`z3-solver` is declared optional and depended on unconditionally**, and
    declared twice with divergent version floors. `pyproject.toml:31`, `:54`,
    `uv.lock:4284`, `:4294`.
50. **The document-AI stack is non-optional**: a default `uv sync` installs torch,
    transformers and onnxruntime through `docling`. `uv.lock:692-697`, `:739-755`.
51. **Python 3.14+ resolves the UMAP path onto 2021-era sdist-only numba and
    llvmlite.** `uv.lock:2100-2119`, `:1455-1466`.

### Layering

52. **Verification depends upward on the operations layer's private helpers** —
    `from theloom.operations.verification import _coverage / _coupling` inside
    check closures. `theloom/verification/capability_spec.py:80`, `:95`.
53. **The pure algorithm layer writes to the store for loop persistence.**
    `theloom/graph/cycles.py:20`, `:293-334`.
54. **Search scope depends on a private cross-module internal** —
    `from theloom.operations.semantic import _search_similar`, while the sibling
    server route uses the public function. `theloom/viz/scope.py:18` vs
    `theloom/viz/serve.py:17`.
55. **A registry handler writes the protocol stream itself** — the `serve` handler
    calls `output_success` and flushes before blocking, bypassing the one place
    that owns the protocol boundary. `theloom/cli/registry.py:1551-1557`.
56. **Component signatures depend on another module's private symbol.**
    `theloom/analysis/component_signatures.py:32`, `:80`.
57. **One clean abstract interface that no consumer actually programs against** —
    16 abstract methods in `store/base.py`, and `get_store` returns the concrete
    class. `theloom/store/base.py:41-158`, `theloom/store/multigraph.py:186-187`.
58. **The pipeline works in untyped wire dicts while the project holds the domain
    model as its single source of truth** — `Doc = dict[str, Any]` repeated across
    six synthesis modules and the whole algebra layer.
    `theloom/synthesis/selector.py:19` and siblings, `theloom/algebra/core.py:22`.

### Visualization-specific

59. **`asOf` bounds entities, relations and events but not analytics or
    semantics** — a time-travelled bundle carries present-tense centrality.
    `theloom/viz/bundle.py:86-125`. Scoped or truncated entity sets have the same
    problem (`:111-125`, `viz/analytics.py:62-73`).
60. **Two different `asOf` comparison strategies inside one bundle** — parsed in
    `scope.py:56-82`, raw string comparison in `temporal.py:21-23`.
61. **The sentinel guard checks presence, not uniqueness, while the consumer
    replaces every occurrence.** `tapestry/scripts/emit-template.mjs:4`.
62. **The build step writes outside its own workspace using cwd-relative paths.**
    `tapestry/scripts/emit-template.mjs:3`, `:8`.
63. **The frontend owns a contract file it cannot regenerate** — no schema script
    among its npm scripts. `tapestry/schema/bundle.schema.json`,
    `tapestry/package.json:6-12`.
64. **The build gate typechecks `src` and the Vite config but not the Playwright
    configs or the emit script.** `tapestry/tsconfig.app.json:24`.
65. **The URL fragment is untrusted input asserted into a typed shape.**
    `tapestry/src/state/urlHash.ts:18`, `:34-37`.
66. **Export is documented as WYSIWYG but cannot capture the DOM overlays**, and
    PNG and SVG capture two different pictures.
    `tapestry/src/lib/exportSvg.ts:2`, `:24-30`,
    `tapestry/src/views/systems/SystemsView.tsx:431-443`.
67. **The label level-of-detail threshold is tuned to leave a test fixture
    untouched.** `tapestry/src/views/systems/SystemsView.tsx:53-59`.
68. **Optional extras skip silently, so local green and CI green mean different
    things.** `tests/test_viz_serve.py:1-14`.
69. **Optional-extra typing erases the server from `mypy --strict`** — FastAPI is
    `Any`, in the one part of the codebase that faces the network.
    `docs/superpowers/plans/2026-07-11-tapestry-phase-4.md:1726-1731`.

### Documentation drift

70. **Frozen plans drift from the code they specified, with nothing marking them
    superseded.** `docs/superpowers/plans/2026-07-11-tapestry-phase-1.md:184-231`
    against the live `theloom/viz/schema.py`. Phase 3 corrects Phase 2's errors by
    listing them, leaving Phase 2 wrong on disk
    (`.../tapestry-phase-2.md:435-437`).
71. **Spec and plan disagree on full re-run semantics** — the spec says always
    supersede, the plan's agent says incremental only.
    `docs/superpowers/specs/2026-08-03-map-codebase-design.md:100-105` vs
    `docs/superpowers/plans/2026-08-03-map-codebase.md:189-190`.
72. **The `--include` flag the spec removed is still threaded through the plan's
    workflow**, in five places. `.../map-codebase-design.md:37-39` vs
    `.../map-codebase.md:509`, `:531`, `:566`, `:663`, `:667`.
73. **A checkbox plan that no one ever checks** — 78 unchecked markers, zero
    checked. `docs/superpowers/plans/2026-07-11-tapestry-phase-1.md:3`.

---

## 7. Open seams

Similarity analysis over a 500-record sample surfaced 20 near-duplicate pairs that
have no relationship connecting them. Most are lexical noise (local variables
inside one function, parallel test names), but four are worth acting on:

1. **`DEFAULT_TRANSFER_PRIORS` and `DEFAULT_TRANSFER_PRIOR` in the same module**
   (`theloom/analysis/absence_surprise.py`, similarity 0.77). Two constants whose
   names differ by one character, in one file — a near-certain source of a future
   wrong-constant bug.
2. **Three `multi` fixtures across `test_viz_temporal`, `test_viz_analytics` and
   `test_viz_asof`** (0.75–0.76), and again in `test_cli_commands` /
   `test_simulate_change` (0.71). This is the copy-pasted-fixture risk seen from
   the other side: the similarity signal found the duplication before a human did.
   The same holds for the four `..._writes_nothing_when_the_event_append_fails`
   tests in `test_store_atomicity` (0.74–0.75).
3. **`theloom/operations/__init__.py`, `theloom/analysis/__init__.py` and
   `theloom/algebra/__init__.py`** (0.71–0.72). All empty. The packages export
   nothing, so every consumer imports the concrete module — worth deciding
   deliberately rather than by accident.
4. **The pattern "Optional LLM with a deterministic template fallback at every call
   site" and the invariant "Synthesis output is fully deterministic when no LLM is
   configured"** (0.71) describe the same design decision from two directions and
   are not linked. The same relationship exists between
   `OPERATION_SEMIRINGS` and the `Semiring` type, and between `TROPICAL` and
   `TROPICAL_STRENGTH_MAP`, in `theloom/algebra/core.py`.

Two larger seams come from the component analysis in §5 rather than from
similarity: **the documentation clusters have no structural link to the code they
specify**, and **the three isolated CSS files have no link to the components that
import them**.

---

## 8. Coverage and methodology

**Coverage.** All 38 module groups were enriched with written architectural notes;
none were skipped. The groups are: `docs` (4 parts), `repo root` (2 parts),
`tapestry` (2 parts), `tapestry/e2e`, `tapestry/src` (4 parts), `tests` (4 parts),
`tests/fixtures`, `theloom`, `theloom/algebra`, `theloom/analysis`, `theloom/cli`,
`theloom/composites` (2 parts), `theloom/documents`, `theloom/exploration`,
`theloom/extraction`, `theloom/graph`, `theloom/operations` (3 parts),
`theloom/reification`, `theloom/semantic`, `theloom/store`, `theloom/symbolic`,
`theloom/synthesis`, `theloom/verification`, `theloom/viz`.

**Known defects in this map's own inputs.** Two data-quality problems were found
and are reported rather than silently cleaned:

- 25 written notes in the `theloom/composites (part 1/2)` group are exact
  duplicates — the group's purpose, 8 patterns, 9 invariants and 7 risks each
  appear twice. This is a double-write during enrichment, not a real duplication in
  the code.
- The concept `theloom root package purpose` is stamped into two groups,
  `theloom` and `theloom/composites (part 2/2)`. The second stamping is wrong;
  `theloom/composites (part 2/2)` has its own correct purpose note alongside it.

**Files not parsed:** 40. These are formats with no grammar available to the
structural parser. CSS, most JSON and Markdown files appear as file records with
written notes but no symbol-level detail; the three CSS files listed in §5 are
isolated for this reason.

**Sampling.** Similarity clustering (§5) and the open-seam analysis (§7) ran over a
500-record sample of 5,199. Centrality (§3), cycle detection (§4) and component
detection (§5) ran over the whole graph.

**Provenance.** Every architectural assertion above traces to a record in the
`codebase-the-loom` graph at commit `067a5b83`: the structural layer was extracted
deterministically by tree-sitter, and the purposes, patterns, invariants and risks
were written per module group, each carrying a file-and-line anchor. Nothing in
this document was invented at write time; where a claim is a measurement (counts,
rankings, component sizes) it is reported as measured.

**Re-running.** `/map-codebase /Users/jameswinans/Dropbox/Development/the-loom`
re-runs the pipeline. The next run reads `commit` from
`docs/architecture/map-manifest.json` as its diff baseline, so an incremental run
touches only the files changed since `067a5b83`.

**Interrogating the graph directly.** See `QUERYING.md` in this directory for the
full cheat sheet. The fast paths are
`loom explore '{"name": "<symbol>", "graph": "codebase-the-loom"}'` for a
definition plus its callers, callees and imports in one call;
`loom entity-deep-dive` for a full profile; `loom blast-radius` for what breaks if
you change something; and `loom hybrid-search` for finding your way in by
description. The interactive view of the same graph is `codebase-map.html` in this
directory.
