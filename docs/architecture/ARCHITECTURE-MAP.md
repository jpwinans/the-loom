---
repo: the-loom
commit: 41619c1f1c0de89f3778067043266736eefaac78
graph: codebase-the-loom
generated: 2026-08-04
mode: full
---

# The Loom — Architecture Map

## 1. Executive overview

The Loom is a knowledge-graph substrate with exactly one way in: a JSON-in/JSON-out
command-line tool. A user pipes a JSON document at `loom <command>`, gets a pretty-printed
JSON document back on stdout, and — on failure — a one-line `{error, code}` document on
stderr with exit status 1. Everything behind that seam is arranged as a stack. The command
layer (`theloom/operations`) holds one module per command family, each supplying an input
schema and the behaviour a caller experiences: revision metadata, verification gates,
dry-run previews, per-item error collection. Beneath it, the persistence kernel
(`theloom/store`) is the only code that talks to FalkorDB and Redis, and it is where the
project's three load-bearing promises are actually kept — one transactional store, an
append-only event log per graph, and updates that snapshot the prior version rather than
overwrite it. Beside those two layers sit the pure algorithm packages that own no I/O at
all: graph analytics, a semiring path algebra, an analogy-and-absence "computational
creativity" engine, a Plan-Traverse-Realize text synthesis pipeline, a rule engine for
guards and invariants, a foraging-signal package, an embedding layer, and a tree-sitter
code extractor. Above the command layer, `theloom/composites` sequences many single
commands into one partially-failable envelope. To one side, `theloom/viz` projects a live
graph into a versioned JSON payload and ships it three ways — raw, as a single
self-contained HTML file, or over a read-only HTTP server — and `tapestry/` is the
React/sigma.js single-page app that payload fills in. The whole surface is declared as
data: one registry lists 156 commands, the command-line program is a mechanical fold over
that list, and the command catalog is generated from the same list.

| | |
|---|---|
| Source files parsed | 264 |
| Language mix | Python 189, TypeScript 73, JavaScript 2 |
| Files not parsed | 80 |
| External packages referenced | 61 |
| Symbols extracted | 3,416 (1,750 functions/methods, 1,285 constants, 381 classes/interfaces) |
| Module groups described | 29 |
| Written findings (patterns / invariants / tensions) | 201 / 286 / 179 |
| Records in graph (current) | 4,437 |
| Records in graph (incl. superseded versions) | 5,052 |
| Connections | 10,961 (`related_to` 5,705, `part_of` 3,840, `requires` 1,230, `instance_of` 186) |
| Connected components | 1 |
| Cycles reported | 10 (all single-symbol self-recursion) |

The working tree was clean at extraction time; this map describes commit `41619c1f` exactly.

---

## 2. Subsystem walkthrough

### 2.1 The Python package

#### `theloom` (package root)

The shared foundation every other subsystem imports rather than re-implements.
`model.py` is the single source of truth for the domain — 19 entity types, 15 relation
types, the confidence and provenance shapes, and a five-state status lifecycle — and it
validates on load, so an invalid document cannot enter the graph. `config.py` is the one
configuration resolution path (flags, then environment, then `~/.loom/config.json`, then
defaults). `errors.py` defines the six typed error codes as exception classes.
`timeutil.py` fixes one canonical timestamp shape. Around that core sit three narrow
utilities: a snapshot-folder importer (`migrate.py`), Weisfeiler-Leman structural hashing
(`reification/fingerprint.py`), and roughly a thousand lines of in-process sympy adapters
(`symbolic/core.py`) behind 21 operations.

*How it is built:* every value set is an enum and every runtime list of those values is
derived from the enum rather than written out twice; configuration is resolved field by
field in three ordered passes instead of by deep-merging dictionaries; each error code is a
class attribute on its exception, never inferred from message text; sympy is imported inside
each operation body so importing the symbolic layer costs nothing.

*What must stay true:* every wire timestamp is ISO 8601 UTC with a `Z` suffix, validated on
load (`theloom/model.py:31-42`, `theloom/timeutil.py:12-15`). Volatile durability requires
an expiry, enforced at the type level (`theloom/model.py:389-394`, `:467-471`). `retracted`
is a terminal status and a same-status transition is always a valid no-op
(`theloom/model.py:270-294`, `:297-309`). `symbolic.core.run` never raises — every failure
becomes a `success: false` envelope (`theloom/symbolic/core.py:1001-1006`, `:1014-1025`).

*Where it strains:* configuration errors bypass the typed error protocol the rest of the
codebase honours — `LoomConfigError` is a plain exception carrying a shadow code
(`theloom/config.py:49-52` against `theloom/errors.py:52-53`) — and the loader silently
swallows an unreadable or malformed config file (`theloom/config.py:127-129`). The
structural-hashing logic exists twice on purpose, and the extracted copy has the fewer
callers (`theloom/reification/fingerprint.py:5-8`). A documented-dead LaTeX parse path is
retried on every expression parse (`theloom/symbolic/core.py:36-43`).

#### `theloom/store` — the persistence kernel

The only layer that talks to FalkorDB and Redis. It defines one abstract operations surface
(`base.py`, whose docstrings are the behavioural contract), one FalkorDB implementation that
keeps entities, relations, prior versions, embedding vectors and graph metadata inside a
single transactional store, an append-only Redis-Stream event log per graph, pure filter
predicates over model objects, a pager that makes full-scan reads immune to the server's
result-set cap, and a multi-graph facade tracking named graphs plus cross-graph "bridge"
relations.

*How it is built:* records are stored as opaque JSON in a `_doc` property — the graph
structure exists only so Cypher can find and traverse them. An update never overwrites: a
single Cypher statement creates a version node holding the prior document with system-time
bounds and then swaps the live document. Filter semantics are pure functions applied in
Python over materialised objects. Paging is a free function taking the row-runner as a
callable, so the cap-safe loop is testable without a database. Batch edge writes are grouped
by relation type, because Cypher cannot parametrise a relationship type. State that is not
graph-shaped — the named-graph set, the bridge list, the event streams — lives in plain
Redis structures with optimistic concurrency.

*What must stay true:* every mutation appends an event and current state is a projection
(`theloom/store/falkor.py:105`, `:199-200`, `:208`, `:364`). Updates invalidate; they never
overwrite history (`theloom/store/falkor.py:191-198`, `:162-173`). `id` and `created_at`
survive any update, as do a relation's `from`/`to` (`theloom/store/falkor.py:55-56`,
`:186-188`). Status changes are validated against the lifecycle table before any write
(`theloom/store/falkor.py:180-183`, `:59-68`).

*Where it strains:* hard delete (`theloom/store/falkor.py:203-209`) contradicts the
invalidate-never-overwrite promise the rest of the layer keeps. The event log is appended
after the mutation, not with it (`theloom/store/falkor.py:101-105`). Batch relation creation
is documented as one transaction but commits once per relation type
(`theloom/store/base.py:78-79` versus `falkor.py:344-363`). Parallel typed edges are
first-class on read, but update and delete address only the first
(`theloom/store/falkor.py:430-433`, `:463-466`). Every list read is a full scan filtered in
Python (`falkor.py:302`, `filters.py:37-68`), and the filter type accepts three fields the
store silently ignores (`filters.py:14-17`).

#### `theloom/operations` — the command layer

One module per command family, holding the input schema, the operation-level semantics and
the wire-shaped output for all ~156 registered commands. The store stays deliberately thin;
this layer supplies everything a caller experiences as behaviour — version bumps and
previous-version pointers, verification-gate enforcement, dry-run previews, status-universe
choices, cross-graph fan-out, per-item error collection, and soft-fail envelopes for model-
and solver-backed commands.

*How it is built:* every handler takes a validated input model plus the multi-graph facade
and returns a JSON-serializable value; no handler touches argv or stdout. Each command's
accepted JSON is declared next to its handler. Analytic commands hydrate the whole graph and
compute in process rather than pushing work into Cypher. The riskiest mutation — merging two
entities — is a pure planning phase followed by one atomic apply. Sub-domains needing their
own record types get ordinary entities whose observations carry a prefixed blob rather than
new node labels. Peer modules import each other inside function bodies, both to break cycles
and to keep cold start cheap.

*What must stay true:* `update-entity` always bumps the version and points the
previous-version field at the entity's own id (`theloom/operations/entity.py:243-244`).
Status transitions are validated before any write (`entity.py:221-224`). `create-relations`
persists the already-validated prefix even when it aborts (`relations.py:222-228`).

*Where it strains:* error classification by prose substring survives here
(`documents.py:149-158`, `relations.py:163-169`) despite the project's typed-code rule. Each
module picks its own status universe, so "the graph" means something slightly different per
command (`verification.py:37`, `epistemic.py:38-46`, `relations.py:42-44`, `bulk.py:41-43`).
Dry-run defaults disagree across mutating commands and one command writes during a dry run
(`inference.py:346-371`). The verification gate makes the store's cross-graph bridge path
unreachable (`relations.py:136-141`, `:171-174`). `extraction-rollback` hard-deletes while
the rest of the layer supersedes (`extraction.py:176-199`).

#### `theloom/cli` — the only entry point

`registry.py` declares 156 command descriptors across 21 categories; `app.py` loops over that
list to synthesize one subcommand each; `docs.py` renders the command catalog from the same
list; `io.py` owns the protocol. The package holds no domain logic — it is the boundary where
camelCase wire JSON becomes validated snake_case Python, and where typed errors become exit
codes.

*What must stay true:* stdout carries only the result document; every diagnostic goes to
stderr with exit 1 (`theloom/cli/io.py:79-84`, `app.py:99-102`). Every registry-listed
command exists as a subcommand, and only `version` and `init` are hand-written
(`app.py:108-109`, `:54-57`, `:60-78`). Input validation happens exactly once, inside the
dispatcher (`registry.py:1587-1597`). Input arrives from the argument or a pipe, never from
an interactive prompt (`io.py:30-53`).

*Where it strains:* the `serve` handler prints and blocks, breaking the handler contract the
other 155 keep (`registry.py:1445-1468`). `bulk-import`'s raw path bypasses both its declared
input model and the stdin size cap (`registry.py:185-190`, `io.py:43-47`). `update-entity`
returns two different response shapes depending on whether a supersedes relation exists
(`registry.py:167-172`). Command-name uniqueness is assumed but never enforced
(`registry.py:1580`). The deferred store import is undercut by the eagerly imported registry
(`app.py:17`, `registry.py:26-66`).

#### `theloom/graph` — the compute core

The pure, in-memory graph-algorithm layer. It hydrates wire documents into a small
insertion-ordered directed multigraph and runs every structural analysis the tool exposes:
centrality and components, cycle and feedback-loop detection with classification, shortest
and bounded simple paths, frequent-subgraph mining, subgraph extraction, and the parsers that
read loop and leverage-point metadata back out of observations. It holds no I/O and no
config; operations, synthesis, analysis, composites, reification and viz all call it.

*What must stay true:* hydration drops dangling relations so edge insertion can never fail
(`hydrate.py:111-120`). Neighbour iteration order is a public contract — in-edge endpoints
before out-edge endpoints (`hydrate.py:87-96`). PageRank fails loudly rather than returning
unconverged scores (`analytics.py:55-68`). Loop polarity is the parity of negative edges, and
a missing polarity counts as positive (`cycles.py:248-267`).

*Where it strains:* the algorithms are hand-rolled against a stated prefer-libraries
invariant — rustworkx appears exactly once, at `analytics.py:71-79`. `detect_loops` reports
`persisted: true` even when nothing was persisted (`cycles.py:353-366`). Recursion was
removed from one traversal and left in four others. One output mode emits snake_case keys
through the camelCase wire boundary (`subgraph.py:101-117`).

#### `theloom/algebra` — the path-algebra kernel

Turns the graph into a weighted algebraic structure and answers reachability, distance,
likelihood, flow and count questions with one traversal engine parameterised by a semiring.
`core.py` defines five semirings as frozen records, pairs each with a weight extractor
reading the relation strength label, and runs a depth-first traversal aggregating values
while retaining one witness path. `routing.py` partitions relation types into structural,
epistemic and causal categories, each with its natural semiring; six morphisms translate a
value across a boundary, a router emits a declarative plan, and executors run it.

*What must stay true:* adjacency iteration order is part of the output contract and ties keep
the first-discovered path (`core.py:193-207`). The morphism table is total over ordered pairs
of distinct categories (`routing.py:63-96`).

*Where it strains:* two semiring resolvers disagree on the same name by design
(`core.py:85-92` versus `:95-105`); a missing morphism is fatal in planning but silently
drops the edge in segmented execution (`routing.py:210-212` versus `:327-329`); composer
rules run only in segmented execution, so the two cross-category engines value the same
boundary differently (`routing.py:348-355` versus `:538-556`); exact tie semantics are bought
with exhaustive path enumeration and unguarded recursion (`core.py:163-218`).

#### `theloom/semantic` — the meaning layer

`embed.py` owns the entire embedding contract — model, dimensions, query and document
prefixes, the `"[type] name. observations"` text shape, content hashing, truncation, L2
normalisation — and is the single place any part of The Loom turns text into a vector.
`entity_proposer.py` derives entities that should exist from capability-spec violations;
`deduplication_gate.py` screens those proposals against existing vectors before they can
become entities.

*What must stay true:* vectors leave the embedder L2-normalised (`embed.py:74-79`); the
embedding text shape is exactly `[entityType] name. observations` (`embed.py:31-36`);
truncation snaps to a sentence boundary only in the last 20% (`embed.py:43-55`); the
similarity threshold is clamped to `[0.5, 0.99]` (`deduplication_gate.py:97`).

*Where it strains:* the proposer's documented "violation-impact filter" filters nothing
(`entity_proposer.py:554-576`); its model-driven strategy is implemented, admitted dead, and
swallows every error (`entity_proposer.py:10-13`, `:402-407`); `maxCandidates` is accepted,
defaulted and has no effect (`deduplication_gate.py:136-137`); proposal text is embedded in a
different shape and prefix than stored vectors (`deduplication_gate.py:71-80`); and
deduplication scans every stored vector in Python instead of using the index
(`deduplication_gate.py:108-131`).

#### `theloom/verification` — the rule engine

Store-agnostic predicates that decide whether graph content is well-formed: per-element
guards (confidence bounds including NaN, entity type, observations, causal polarity,
self-loops, duplicate relations), five built-in invariants, a fluent capability-spec language
that turns structural expectations into a gap list where each violation carries a suggested
action, and an AC-3 constraint solver over the 19 entity types.

*What must stay true:* entities of type `loop` legalise causal cycles (`checks.py:228`,
`:234`). Absent status means active for invariant purposes (`checks.py:23-24`). Entity
creation is annotated; relation creation is blocked (`guards.py:25-36` versus `:39-61`). The
retirement statuses form a graduated inertness ladder (`checks.py:264-297`).

*Where it strains:* the rule layer reaches up into the command layer's private helpers
(`capability_spec.py:80`, `:95`); the same relation rules are implemented twice
(`checks.py:85-137` versus `guards.py:48-60`); one module breaks the package's store-agnostic
property (`guards.py:22`); the inconsistency branch of the solver is unreachable through the
command surface (`propagation.py:113-120`); and "pattern consistency" is a majority vote
dressed as an invariant (`capability_spec.py:146-210`).

#### `theloom/synthesis` — query to grounded prose

A three-stage Plan-Traverse-Realize pipeline: the planner selects an anchored subgraph,
decomposes the query and partitions it into ordered regions; the traverser walks them
emitting deduplicated evidence with a step-by-step provenance trace; the realizer linearizes
each region and renders one of six formats — via a language model when configured, via
deterministic templates when not. `fidelity.py` then scores the produced text back against
the graph. A second, independent subsystem in the same package implements
counterexample-guided inductive synthesis.

*What must stay true:* selection bounds are clamped regardless of caller input — depth at
most 10, entities at most 1000 (`selector.py:166-172`). Composite fidelity is a weighted
harmonic mean that collapses to zero if either rate is zero (`fidelity.py:351-359`). Prompt
sanitisation hard-cuts and removes every angle bracket (`prompts.py:27-32`). The main
pipeline is read-only; the synthesis-commit path is the only write (`selector.py:31-38`,
`cegis.py:451-496`).

*Where it strains:* source passages are structurally unreachable — the links lookup returns a
hardcoded empty list (`links.py:13-14`). Sub-question assignment is round-robin by index, not
semantic (`orderer.py:86`). Structural fidelity infers relation direction from substring
position (`fidelity.py:247-273`). Model failure is recorded in realization but silent
everywhere else (`realizer.py:307-313` versus `decomposer.py:76-79`).

#### `theloom/analysis` — the creativity engine

Eleven pure modules implementing a named literature pipeline: cross-domain structural role
mapping, concept slippage, analogy transfer (copy, substitute, generate), absence-surprise
scoring, adaptability gating, confidence blending, interestingness, Weisfeiler-Leman
component signatures for far-analogy retrieval, and approximate subgraph matching. Nothing
here touches the store: every function takes hydrated dictionaries and returns plain
dictionaries.

*What must stay true:* unmapped source endpoints become namespaced placeholders, never real
target ids (`cwsg.py:31`, `:110-119`); absence scoring never dereferences a placeholder as a
real entity (`absence_surprise.py:236-266`); only relations connected to the matched
structure transfer (`cwsg.py:68`); slippage failure degrades transfer but never aborts it
(`cwsg.py:152-166`).

*Where it strains:* the timeout option is enforced in one module and inert decoration in two
others (`isomorphism.py:129` versus `crossdomain.py:29`); similarity and proximity primitives
are reimplemented four times inside one package; the transfer module advertises options and
constants it does not implement (`cwsg.py:16-19`, `:34-35`); slippage truncates its candidate
pool before scoring, so results depend on entity list order (`slippage.py:238`).

#### `theloom/exploration` — foraging signals

Answers "where should the next unit of attention go" by scoring connected regions on four
independent signals — age staleness, bridging potential, coverage gap, and an exploration
bonus — then folding them into one weighted score. On top sit a marginal-value-theorem
patch-leaving policy and six anti-pattern guards (echo chamber, gravity well, comfort zone,
random walk, noisy TV, breadth addiction). Orchestration is deliberately elsewhere.

*What must stay true:* signals return a section result and never propagate exceptions; every
score is normalised and clamped to `[0, 1]`; absent signals are dropped and the remaining
weights renormalised rather than zero-filled (`composite_signals.py:70-89`); unvisited
regions receive the maximum bonus (`composite_signals.py:50-53`).

*Where it strains:* three incompatible region-identity keys inside one subsystem
(`guards.py:153-160`, `:374`, `exploration_state.py:89-97`); multi-invocation foraging
semantics on a state store that resets every process (`exploration_state.py:100-109`); a
configurable threshold no code path reads (`guards.py:62`); comfort-zone diversity measured
against all 19 model types rather than the ones present (`guards.py:250-272`).

#### `theloom/composites` — the orchestration tier

One module per high-level command, each bundling many single-purpose operations into one
structured, partially-failable envelope. A composite declares its input model, resolves the
target store, then runs a fixed sequence of named sections — reconnaissance, gap detection,
provenance cascades, analogy transfer, dry-run simulation — each wrapped so one section's
failure cannot abort its siblings. `framework.py` supplies the three primitives everything
else composes with; `simulate_change.py` adds the tier's only sandboxed-write capability.

*What must stay true:* the section wrapper never propagates an exception — failure becomes
null data plus an error string (`framework.py:45-53`). `simulate-change` never mutates the
requested graph; mutations land on a temporary clone dropped in a `finally`
(`simulate_change.py:247-248`, `:309-312`). `self-improve` performs no writes unless
auto-apply is explicitly true (`self_improve.py:299-301`).

*Where it strains:* six composite handlers return shapes other than the envelope they are
named for; two registered composite commands are inert or explicitly unimplemented
(`enrichment_crawl.py:52-58`, `creativity_loop.py:114-140`); PageRank scores are published to
the wire under the key `eigenvector` (`graph_reconnaissance.py:126-132`); `gap-fill-cycle`'s
commit threshold can never open (`gap_fill_cycle.py:224-236`); blanket exception swallowing
inside sections erases the diagnostics the typed-error contract promises; and
`explore-frontier` matches advice to regions by Python object identity
(`explore_frontier.py:216`, `:221`).

#### `theloom/extraction` — the ingestion front end

Two independent halves sharing a namespace. The codebase half parses a source tree with
tree-sitter into a deterministic, model-free structural graph — files become system records,
classes and functions and constants become typed symbols, and imports, inheritance and calls
become edges — with a second whole-project pass joining the edges no single-file parse can
close. The document half routes chunk text through a synthesis model and validates the result
against a fixed type whitelist. Three operational helpers surround them: a git-driven
incremental re-extractor, a self-model refresher that keeps The Loom's own codebase graph
current via a commit marker, and a Redis-backed run store.

*What must stay true:* every supported source file yields exactly one file record, even when
nothing parses (`treesitter.py:532-542`). An ambiguous cross-file symbol produces no edge at
all (`resolution.py:305-314`). Self-model update is idempotent per commit
(`selfmodel.py:82-92`). Only whitelisted entity and relation types survive response parsing
(`pipeline.py:106-118`).

*Where it strains:* the incremental path pays full-repository cost to update a few files
(`codebasediff.py:96`, `:107-108`) and promises retraction and relation diffing it does not
perform (`codebasediff.py:1-8` versus `:107-158`). Symbol identity is basename-scoped while
resolution assumes names are meaningful (`treesitter.py:72-75`, `:690-693`,
`resolution.py:281-314`) — the single most consequential strain in this map; see §3 and §5.
The document pipeline reports a completed eight-stage run while executing four
(`pipeline.py:1-9`, `:237-251`). Git failure is silent in one helper and fatal in the other
(`codebasediff.py:30-36` versus `selfmodel.py:24-27`).

#### `theloom/documents` — document ingestion

Turns files, directories, raw content and URLs into embedded, searchable chunk rows inside
FalkorDB along a fixed four-stage line: parse to a uniform block list, chunk with overlap,
embed, upsert. It owns no knowledge-graph concepts — chunks are global across graphs, which
is how the project honours "one transactional store" without a separate vector store. Address
guarding, size ceilings and redirect caps are enforced at the module boundary rather than by
callers.

*What must stay true:* every resolved address of a URL host must be globally routable
(`ssrf.py:68-70`, `:32-36`, `:42-45`). A chunk's content hash covers the overlap prefix, not
just the body (`chunker.py:229`). Groups containing code or list blocks are never split, even
past the maximum size (`chunker.py:165-167`). Reingest preserves chunk identity per index and
skips unchanged chunks (`ingestion.py:286-314`).

*Where it strains:* repeat ingest duplicates chunks because ids are fresh UUIDs while only
reingest diffs (`ingestion.py:59-110` versus `:253-340`); directory ingest reports per-file
failures as zero-chunk successes (`ingestion.py:161-178`); the embedded-at timestamp is
stamped even when embedding failed (`ingestion.py:42-50`, `:78`); address validation and the
actual fetch resolve DNS separately (`ssrf.py:93`, `:95`); and reingest silently reconstructs
content from stored chunks when the source is unreachable, duplicating sentences at every
chunk boundary (`ingestion.py:353-362`).

#### `theloom/viz` — the visualization boundary

Turns a live graph into a self-describing, versioned JSON payload and delivers it through
three surfaces: raw export, a single self-contained HTML file with the payload injected into
a committed frontend build, and a read-only HTTP server. It is a pure projection layer — it
composes existing store, analysis and semantic operations into one wire shape, applies
scoping, system-time bounds, cost guardrails and a size cap, and never mutates.

*What must stay true:* injected payload JSON can never terminate the host script block
(`html.py:33`). The payload ships records of every status, not just active ones
(`scope.py:39-44`). Every payload ships a closed subgraph — no relation without both
endpoints (`scope.py:76-82`, `:110-112`, `bundle.py:71`). Degree truncation is deterministic
and preserves store order (`bundle.py:66`, `:70`).

*Where it strains:* a point-in-time promise that hard deletes can silently break
(`bundle.py:86-92`, `scope.py:67-71`); scoping narrows the graph but not the sections that
describe it (`bundle.py:109-123`); one as-of bound with three different comparison semantics
(`scope.py:56-82` versus `temporal.py:11-23`); a hand-calibrated similarity floor that couples
search scope to one embedding model (`scope.py:26-37`); and a loop guardrail that gates on
node count while the cost is exponential in cycle count (`analytics.py:46-75`).

### 2.2 The Tapestry frontend

#### `tapestry` (build and harness)

The seam where a React/sigma.js app becomes a single self-contained HTML file the Python
package ships. The build inlines every asset; a post-build script promotes the built page into
`theloom/viz/static/tapestry.html` after checking the payload placeholder survived. Two
browser-test configurations partition end-to-end coverage into the two ways the app is
delivered: a static local file and a served process.

*What must stay true:* the build refuses to emit a template that lost the placeholder
(`scripts/emit-template.mjs:4-8`). The unit runner never executes the browser suites
(`vite.config.ts:8-13`). The live suite assumes a server is already listening
(`playwright.live.config.ts:9-15`), and runs serially because the graph switch is global
state.

*Where it strains:* a generated build artifact is versioned inside the Python package
(`scripts/emit-template.mjs:8`); live-run preconditions are documented in prose, not enforced
by the configuration; both end-to-end regimes are Chromium-only while the shipped artifact is
browser-agnostic; and runner boundaries must be restated in two places whenever a suite is
added.

#### `tapestry/src` (the shell)

Boots one React root, dresses the page in the design system, and switches among five views
without owning any of their rendering. `App.tsx` is the entire chrome: brand block, a tabbed
view switcher, the live-mode graph selector, the theme control, a polite announcement region,
the entity-type ribbon, and the two effects that make a static file deep-linkable.
`design/theme.ts` collapses the tri-state theme preference into a concrete dark or light
value stamped on the document root; `design/palette.ts` mirrors the model's 19 entity types.

*What must stay true:* the type-colour accessor is total — every input yields a defined CSS
colour (`design/palette.ts:35-39`). The document root always carries a concrete theme
(`design/theme.ts:15-26`). The view switcher and the theme control are each exactly one tab
stop (`App.tsx:303`, `:365`). The announcement region never announces content present at
first load (`App.tsx:184-190`). The app renders only inside the payload provider, at a single
root (`main.tsx:6-10`). The system colour-scheme listener exists only while the theme is
automatic (`App.tsx:256-263`).

*Where it strains:* the 19 entity types are hand-mirrored in TypeScript with no automated sync
to the model (`design/palette.ts:1-27`); deep-link state is restored once at mount while live
mode can swap the graph underneath it (`App.tsx:228-240`); the shortcut sheet is hand-written
documentation rather than a projection of the real bindings (`views/HelpOverlay.tsx:27-60`).

#### `tapestry/src/lib` (shared runtime)

Owns exactly what the four canvas views all need and none should own: how a payload is
acquired (server-injected, dev fixture, or live endpoint) and shared through one context with
a memoized graph model; node drag choreography; label reveal and word wrap; SVG and PNG
export; per-graph saved views in local storage; and DOM-free keyboard arithmetic. Its
organising idea is a pure, unit-testable decision core wrapped by a thin impure shell — nine
source modules paired with eight test specs.

*What must stay true:* the payload placeholder literal must never appear in application
source (`data.ts:33-45`). The drag gesture's moved flag is sticky until release
(`dragState.ts:52-56`), and a drag that moved swallows exactly one trailing click
(`dragNodes.ts:155-159`). Overriding the label renderer requires overriding the hover renderer
too (`nodeLabels.ts:193-207`). Wrapping never exceeds the line cap and marks truncation with
an ellipsis (`nodeLabels.ts:71-101`). PNG export repaints synchronously before reading canvases
(`exportSvg.ts:297-318`). SVG export serializes only the supplied visibility set, re-checking
edge endpoints (`exportSvg.ts:140-196`). Saved-view storage never throws and never clobbers a
rename target (`savedViews.ts:26-114`). Global shortcuts never fire while the user is typing or
holding a modifier (`keyboard.ts:32-35`).

*Where it strains:* payload shape is declared in three places, only two of which are checked
against each other (`data.ts:1-28`, `schema.test.ts:6-11`); a failed fetch has no error path
and leaves the app permanently loading (`data.ts:53-61`, `BundleContext.tsx:51-68`).

#### `tapestry/src/state` (client state)

One module-level store owns every piece of cross-view interface state — active view, theme,
selection, the filter predicate, path-tool mode and endpoints, loop isolation, the time
scrubber and diff anchor, the lasso brush — alongside one named setter per field. A second
module is the deep-link codec, projecting a four-field subset into a URL fragment and
replaying a parsed fragment back into the store.

*What must stay true:* fragment parsing is total — no URL can make it throw
(`urlHash.ts:16-21`). Encode and decode round-trip exactly (`urlHash.ts:11-22`). Restore
distinguishes an absent key from an explicit null (`urlHash.ts:35`, `:37`). Only the filter
setter merges; every other setter replaces (`store.ts:72`). Clearing the path resets the
endpoints but leaves path mode on (`store.ts:75`). The reset value of each composite field is
reference-identical to its mount-time value (`store.ts:23`, `:62`, `:75`).

*Where it strains:* the fragment boundary catches malformed JSON but validates nothing
(`urlHash.ts:18`, `:34-37`); the shareable fragment carries four of the store's eleven fields;
a "pure codec" module reaches straight into browser history (`urlHash.ts:31`); and the store's
own tests assert defaults against a singleton they themselves mutate (`store.test.ts:5-13`).

#### `tapestry/src/views/explorer` (the primary view)

The node-link view, and the folder that owns the app's shared visual encoding. It turns a
payload into a graph model whose attributes carry every visual channel — fill by entity type,
size by degree, edge tint by relation family, width by strength — runs a force layout for
three seconds, then offers zoom, pan, click-select, fuzzy search, arrow-key walking, faceted
filters, shortest-path highlighting, a minimap, a detail panel, exports and saved views. Every
interaction is expressed as ordered overrides inside two render-time functions layered over an
unmutated graph.

*What must stay true:* filtering is a returned visibility set, never a graph mutation
(`filters.ts:45-62`). Absent confidence passes any confidence floor (`filters.ts:36-41`).
Dangling relations are skipped silently, not treated as errors (`buildGraph.ts:196`). An edge
is visible only when both endpoints are (`filters.ts:53-58`). Node size is a pure function of
degree, fixed only after every edge lands (`buildGraph.ts:210-215`). Path search is undirected
for reachability but returns real directed edges (`pathMode.ts:25-57`). Every renderer
resource is released on unmount (`Explorer.tsx:299-307`). Saved views and exports are scoped
per graph key (`Explorer.tsx:99`). The legend reports exactly what is on the canvas
(`legendRows.ts:23-33`).

*Where it strains:* app-wide visual encoding lives inside one view folder
(`buildGraph.ts:37-59`); colour values are duplicated between TypeScript tables and the design
token layer (`buildGraph.ts:68-96`); which entity types are present is computed three times,
three ways; hover is dead while the physics layout runs (`Explorer.tsx:253-266`); path lookup
rebuilds a whole-graph mirror on every endpoint change (`pathMode.ts:25-33`); and
`Explorer.tsx` is the folder's sole unextracted component and its largest by a factor of four.

#### `tapestry/src/views/chronicle` (time travel)

Re-implements the store's point-in-time read semantics on the client, over the event log
shipped inside the payload, with no server round trip. One pure engine reshapes events into
lookups and a sorted list, projects which nodes and edges existed at any instant plus each
node's effective status, and classifies ids changed between two instants as added,
invalidated or changed. The component is an imperative shell over the same shared graph the
Explorer renders, plus two absolutely-positioned badge layers, a time scrubber and a
virtualized event rail.

*What must stay true:* the Chronicle projects; it never mutates the graph it replays
(`replay.ts:202-235`). An element with no creation event is present from the start of time
(`replay.ts:207-208`). An edge is visible only if both endpoints are (`replay.ts:230`). Diff
windows are half-open and order-independent (`replay.ts:243-244`). A timeline's span is never
zero (`replay.ts:184-186`). A null time means the end of the timeline, resolved identically by
all three components. In diff mode a node wears exactly one badge, and the summary counts
match the badges (`Chronicle.tsx:386-401`).

*Where it strains:* merge events are narrated in the stream but invisible to the projection
(`replay.ts:167-172`).

#### `tapestry/src/views/systems` (causal loops)

Strips the graph to its causal slice, renders it with edges coloured and glyphed by polarity,
lists every feedback loop the analytics pass found, isolates a loop on click, animates a
signed pulse travelling it, and badges each variable carrying a leverage point with its level.
Same payload and same seeded node positions as the Explorer, different question.

*What must stay true:* the causal graph contains only causal relations and the entities they
touch (`systems.ts:63-89`). Loop edge keys are resolved through directed out-edges, never
undirected ones (`systems.ts:125-136`). The flow function is a wrapped raised cosine bounded
in `[0,1]` that closes the cycle (`systems.ts:193-201`). The animation runs only while a loop
is isolated (`SystemsView.tsx:537-540`). Reduced-motion viewers get the frozen pulse, not a
disabled feature (`SystemsView.tsx:360-370`). The causal graph is a keyed directed multigraph,
so parallel influences survive (`systems.ts:52`, `:96-99`). Malformed or dangling rows are
skipped, never fatal.

*Where it strains:* exports drop the overlays that carry polarity and leverage
(`SystemsView.tsx:431-458`); loop selection is keyed by list index while the resolver also
accepts ids (`LoopPanel.tsx:15-45`); causal edge colour is assigned twice by two different
rules; per-edge polarity reaches assistive technology only through the legend.

#### `tapestry/src/views/semantic` (embedding space)

A scatter of the payload's projection in which screen distance encodes semantic distance
rather than adjacency. One point per projected entity, no edges; each cluster gets a soft
convex-hull region carrying its label; a freehand lasso (or a keyboard cluster menu) carves a
selection into the shared brush set the Explorer reads as a highlight layer. Space-agnostic
geometry is separated from the rendering shell so it can be tested without a GPU.

*What must stay true:* a point exists only when a projection id resolves to an entity with a
two-dimensional coordinate (`semanticMap.ts:36-46`). Node position equals the projection
coordinate — nothing else ever moves it (`semanticMap.ts:54-61`). Hull and lasso geometry is
computed in viewport pixels (`SemanticView.tsx:243-249`, `:381-388`). A lasso of fewer than
three points never mutates the brush. An empty lasso result stores null, not an empty array
(`SemanticView.tsx:389`). Brushing dims but never filters, so exports carry every point
(`SemanticView.tsx:395-423`).

*Where it strains:* dragging a point contradicts the projection-as-layout contract, and
exports snapshot whatever positions the graph currently holds (`SemanticView.tsx:207-213`,
`:401-431`).

#### `tapestry/src/views/overview` (the dashboard)

A read-only roll-up rendered as six headline tiles, three panels and a most-central table,
answering "what shape is this weave in?" before the reader touches the Explorer. All
arithmetic lives in a single pure function folding the payload's own arrays; the component is
presentation only, and its sole mutation is navigating to the Explorer with an entity
selected.

*What must stay true:* confidence coverage is exhaustive — scored plus unscored equals the
entity count (`stats.ts:71-78`). The histogram is exactly ten bins with a perfect score
clamped into the last (`stats.ts:58`, `:76`). Dangling relations are counted against the
payload arrays, so the integrity signal survives (`stats.ts:56`, `:91`). The centrality table
is capped at ten rows sorted by descending PageRank (`stats.ts:96-99`). Every bar-width
denominator is guarded to be non-zero (`Overview.tsx:187-191`).

*Where it strains:* malformed records are silently absorbed into the tallies rather than
surfaced (`stats.ts:63-91`); the dashboard depends on Explorer internals and re-derives its
facet counts from a different source (`Overview.tsx:23`, `:184`); only the pure function is
tested, and its fixture is cast away (`stats.test.ts:17-28`).

### 2.3 Tests, fixtures and tooling

#### `tests (part 1/2)` — the substrate contract

Pins the behaviour of the lower and middle layers. Roughly a quarter of the files drive a live
FalkorDB and fix store semantics — create/read/update/delete, the five-state lifecycle,
filters, the append-only log, point-in-time reads, seed import, multi-graph handlers. The rest
are hermetic units over the domain model, the configuration precedence chain, the command-line
protocol, tree-sitter extraction and cross-file resolution, graph algorithms, and
config-routed model clients driven through a mock transport. A third role is drift policing:
tests that read artifacts outside the suite — every documented `loom` invocation, the
generated command catalog — and re-validate them against the live registry.

*What must stay true:* every database-touching test is namespace-isolated and self-cleaning
(`tests/conftest.py:33-43`). Every store mutation appends exactly one typed event, in order
(`tests/test_falkor_store.py:393-420`). Point-in-time reads return historical state
(`test_falkor_store.py:438-457`). Status transitions are enforced at the store and `retracted`
is terminal (`test_falkor_store.py:121-134`). Error codes come from typed exceptions, never
prose matching (`tests/test_cli_io.py:66-74`). Full-scan reads must survive the server's
truncation cap (`tests/conftest.py:46-55`). Seed import round-trips byte-identically
(`tests/test_migrate.py:39-51`).

*Where it strains:* live-store fixtures error rather than skip when the database is absent,
and no marker separates the hermetic majority (`tests/conftest.py:22-30`); truncation testing
mutates server-global configuration inside an otherwise per-test-isolated suite
(`conftest.py:46-55`); two incompatible fixture-path conventions coexist
(`test_extraction_units.py:130` versus `test_migrate.py:23`); several docstrings defer to a
golden-test gate that is not present in the repository.

#### `tests (part 2/2)` — the operations and export contract

Twenty-three modules in two tiers: fifteen integration modules driving a real store through
the multi-graph facade (merge, bulk import, entity and relation operations, bridges, triggers,
initialisation, session provenance, and the whole visualization pipeline), and eight pure
units that need no store (address guarding, the abstract store interface, synthesis and
symbolic internals, the solve pipeline, HTML injection, payload schema). Where the store is
involved the tests assert on real Cypher, real search and the real event log; where a network
or a model would be involved, exactly one seam is stubbed.

*What must stay true:* size guardrails are proved by injecting the constant, never by building
a large graph; one embedder seam is stubbed so CI never downloads a model; contracts are
pinned as exact sets and exact envelopes, so an addition is drift rather than growth.

*Where it strains:* the operations gate forbids the cross-graph relations the facade
auto-bridges, and the two halves of the suite assert opposite things
(`test_ops_relations.py:102-110` versus `test_multigraph.py:157-171`); import-or-skip keeps
the suite green precisely where coverage disappears (`test_viz_serve.py:12`,
`test_viz_semantic.py:113`).

#### `tests/fixtures` — the extractor's sample repository

A four-file synthetic service that is input data rather than code: nothing imports or executes
it, and its only consumer is the tree-sitter extractor under test. It is sized so every symbol
and edge the extractor claims to produce can be counted by hand, which is what licenses exact
literal totals instead of fuzzy shape checks. Its second job is one end-to-end instance of
each import-resolution branch — a dotted internal import, an extensionless relative specifier
resolved across a file-extension change, and an external package that must collapse into a
single package record.

*Where it strains:* the pinned literal totals make it append-hostile — adding one function
shifts three counters at once (`lib/helper.js:1-9`); the extractor supports Go and Rust and
the fixture covers neither; no fixture class declares a base, so inheritance resolution is
never proven end to end (`lib/index.ts:7`, `src/models.py:6-7`); three exported symbols are
never called, with no assertion pinning that intent.

#### `tapestry/e2e` — the browser contract

Seven browser specs that never drive a development server: each reconstructs the shipped
artifact by substituting the committed dev payload into the committed template, writes it to a
temporary file, and opens it as a local file. They partition the surface into smoke, drag,
export, saved views, keyboard, help, and a matrix accessibility audit over five panels by two
themes.

*What must stay true:* specs exercise the built single-file artifact, never a development
server (`smoke.spec.ts:17-22` and six siblings). Only serious and critical accessibility
impacts gate the suite (`a11y.spec.ts:42-45`). The injected payload is escaped exactly as the
production renderer escapes it (`smoke.spec.ts:20`). Every export is a real download named
`<graph>-<view>-<date>.<ext>` (`export.spec.ts:31-32`). A drag never registers as a click
(`drag.spec.ts:124-133`). The help overlay is a modal focus trap that restores focus to its
trigger (`help.spec.ts:38-62`). Malformed imports and rename collisions are refused without
mutating saved views (`savedviews.spec.ts:41-101`).

*Where it strains:* the specs reimplement the production payload injection instead of calling
it — seven copies of the same preamble; assertions hard-code exact counts from the committed
fixture (`smoke.spec.ts:99-313`); two waiting philosophies coexist, state polling and
wall-clock sleeps.

#### `scripts` — out-of-band graph builders

Two developer-only tools that populate the database with disposable graphs the normal command
path is too slow or too indirect to produce: a synthetic 50k-entity stress graph the
visualization benchmark was tuned against, and a tiny pair of graphs the live browser suite
drives. Neither is imported by the package, referenced by the registry, or exercised in CI.
Both reach past the command layer to the store directly, and both are strictly embedding-free,
so a developer run never downloads the model.

*What must stay true:* the bench generator can only ever write to `tapestry-bench` — the name
is a module constant, not a flag (`gen_bench_graph.py:68`, `:162-165`). Neither script can
trigger an embedder load (`gen_bench_graph.py:46-63`, `seed_live_dev.py:10-16`). Round-robin
type coverage guarantees every entity type appears (`gen_bench_graph.py:84-93`). The bench
graph contains no self-loops (`gen_bench_graph.py:114-117`). The seed produces one three-node
causal cycle whose signs multiply to reinforcing (`seed_live_dev.py:44-53`).

*Where it strains:* relations have a batch write primitive and entities do not, so entity
seeding is a per-entity round trip (`gen_bench_graph.py:168`); wipe-and-recreate collides with
the store's default-graph protection in one script and not the other
(`gen_bench_graph.py:162-164`); script-written relations carry neither strength nor evidence,
which the command layer would reject (`gen_bench_graph.py:119-128`).

---

## 3. Load-bearing modules

Ranked by how many other things touch them, with the brokers — modules sitting on the most
paths between otherwise separate regions — noted.

| Rank | Module | Connections | Why it is load-bearing |
|---|---|---|---|
| 1 | `len` constant, `tapestry/src/views/semantic/SemanticView.tsx:92` | 289 | **Not real.** All 288 incoming edges read "calls `len`, the project's only symbol of that name" — every `len(...)` call in the Python codebase was joined to a TypeScript local constant because it was the one project symbol with that name. See §5. |
| 2 | `CommandInput`, `theloom/operations/common.py` | 145 | The base class every command input model inherits; the single point where the camelCase wire boundary is declared. |
| 3 | `tapestry/src/views/explorer/Explorer.tsx` | 136 | The largest frontend file (1,052 lines) and the only view folder whose helpers the other four views import — it owns colour, family classification and node placement for the whole app. |
| 4 | `tapestry/src/views/chronicle/Chronicle.tsx` | 126 | The time-travel shell: one renderer instance, two overlay layers, three sibling components and the shared graph all meet here. |
| 5 | `pkg:typing` | 115 | External package required by nearly every Python module. |
| 6 | `theloom/store/multigraph.py` | 114 | **Top broker.** The facade every command resolves its store through, and the owner of the named-graph registry, bridges, chunk store, run store and event log. Nothing reaches persistence without passing through it. |
| 7 | `tapestry/src/views/semantic/SemanticView.tsx` | 107 | Second-highest broker — though partly inflated by the `len` artifact it hosts. |
| 8 | `tapestry/src/views/systems/SystemsView.tsx` | 105 | The causal view's wiring: render functions, two overlay layers, an animation loop and a theme listener. |
| 9 | `theloom/model.py` | 98 | The domain model, imported by 53 modules. Every entity and relation shape in the system is declared here once. |
| 10 | `theloom/store/falkor.py` | 97 | The store implementation, and the third-highest broker — versioning, the event append and the vector index all live here. |
| 11 | `tests/test_entity_proposer_foundation.py` | 81 | The largest single test module by symbol count. |
| 12 | `theloom/operations/semantic.py` | 74 | 38 KB of embedding and search commands; the only in-tree caller of the vector scan. |
| 13 | `theloom/cli/registry.py` | 72 | The 156-descriptor declaration; it imports all 16 handler modules, which is why it also ranks high as a broker. |
| 14 | `theloom/operations/epistemic.py` | 70 | The 17 epistemic queries plus credit propagation, 36 KB. |
| 15 | `tests/test_falkor_store.py` | 69 | The store's behavioural contract in one file. |

Three more modules rank high as brokers without ranking high by connection count, which is the
signature of a seam rather than a hub: `theloom/viz/bundle.py` (the single assembler behind all
three delivery surfaces), `theloom/operations/analysis.py` (the only path from commands into
the pure graph algorithms), and `tapestry/src/lib/BundleContext.tsx` (the one place the
frontend acquires a payload).

---

## 4. Dependency cycles

The cycle detector reports **ten cycles, and every one has a single member** — a function that
calls itself. No cycle spanning two or more modules was found.

| Cycle | Verdict | Reason |
|---|---|---|
| `_hash_at_depth` — `theloom/operations/reification.py:93-119` | intentional | Recursive neighbourhood hashing; depth is the recursion bound. |
| `_hash_at_depth` — `theloom/reification/fingerprint.py:56-82` | intentional, but note the duplicate | Same algorithm as the row above; the duplication is deliberate and documented (`fingerprint.py:5-8`), and the extracted copy has the fewer callers. |
| `_resolve_references` — `theloom/symbolic/core.py:789-822` | intentional | Walks a nested symbolic expression. |
| `_generic_json_to_blocks` — `theloom/documents/parsers.py:261-305` | intentional | Descends arbitrarily nested JSON into document blocks. |
| `_jsonify` — `theloom/cli/io.py:56-64` | intentional | Recursive JSON coercion at the output boundary. |
| `_js_string` — `theloom/synthesis/prompts.py:13-24` | intentional | Recursive string encoding in the port-parity shim. |
| `_extract_calls` — `theloom/extraction/treesitter.py:126-132` | intentional | Syntax-tree descent. |
| `_find_identifier` — `theloom/extraction/treesitter.py:221-228` | intentional | Syntax-tree descent. |
| `_extract_require_calls` — `theloom/extraction/treesitter.py:316-326` | intentional | Syntax-tree descent. |
| `_substitute` — `tests/test_claude_examples_contract.py:96-110` | intentional | Recursive placeholder substitution in the documentation-drift guard. |

The absence of module-level cycles is not luck: the command layer explicitly imports peer
modules inside function bodies to break them, and the pure algorithm packages (`graph`,
`algebra`, `analysis`, `exploration`, `verification`) depend downward only. The one place to
watch is `theloom/verification/capability_spec.py:80,95`, which reaches *up* into
`theloom/operations`' private helpers with function-local imports — a latent cycle currently
hidden by the deferral, and recorded as a tension in its own right.

Two notes so the terms are not confused: `theloom/verification/checks.py:228,234` treats
entities of type `loop` as legalising a causal cycle in the *data*, and
`theloom/graph/cycles.py` is where those data-level feedback loops are found and classified.
Neither concerns the import graph.

---

## 5. Communities vs. directories

Every extracted record lands in **one connected component** — all 4,437 of them. There is no
isolated island in this codebase, which is expected for a repository whose frontend ships
inside the backend package.

Embedding-space clustering finds almost no structure at the default similarity threshold. Over
a 150-record sample, only six groups formed, none larger than three members:

| Group | Members | Reading |
|---|---|---|
| 0 | `App.tsx`, `Chronicle.tsx`, `replay.test.ts` | Real: the shell and the time-travel view share vocabulary. |
| 1 | `DIM_FALLBACK` in `explorer/` and in `semantic/` | The duplicated colour-fallback tables named in §2.2 — one constant living in two view folders. |
| 2 | `container` and `color` in `Explorer.tsx` | Noise: two adjacent locals in one file. |
| 3 | `multi` fixture in `test_cli_viz_commands` and `test_viz_analytics` | Real: the byte-identical per-module store fixture copied across fifteen test modules. |
| 4 | `theloom/operations/semantic.py`, `theloom/operations/symbolic.py` | Same directory; agrees with the folder structure. |
| 5 | `TraverseSynthesisInput` (in `operations/`), `traverse_synthesis` (in `synthesis/traverser.py`) | Real and interesting: an input schema and the function it drives sit in different packages — exactly the command/algorithm seam. |

The honest reading is that **the folder structure already is the community structure** here:
meaning is carried by directory, and the embedding layer adds little on top of it. The
interesting disagreements are all duplications rather than misplacements — a colour table in
two view folders, a store fixture in fifteen test modules, three cosine-similarity
implementations inside `theloom/analysis`, two structural-hash implementations across
`theloom/operations` and `theloom/reification`, and two relation-rule implementations across
`theloom/verification/checks.py` and `guards.py`.

One community *is* spurious and worth acting on. The single most connected record in the entire
graph is a TypeScript local constant named `len` at
`tapestry/src/views/semantic/SemanticView.tsx:92`, wearing 288 incoming edges from Python
functions across `scripts/`, `tests/` and `theloom/`. Each edge's evidence reads "calls `len`,
the project's only symbol of that name". This is the cross-file resolver's unique-name rule
(`theloom/extraction/resolution.py:305-314`) meeting basename-scoped symbol identity
(`theloom/extraction/treesitter.py:72-75`) — a language builtin shadowed by a
coincidentally-named local in another language. It welds the TypeScript frontend to the Python
backend where no dependency exists, and it distorts every centrality reading that includes it.
The same mechanism will fire for any single-occurrence name that collides with a builtin.

---

## 6. Risks & tensions

179 tensions were recorded across 29 module groups. These are the ones a reviewer should read
first, most consequential first.

1. **Cross-file symbol resolution matches on bare names.** Symbol identity is basename-scoped
   and the resolver emits an edge whenever exactly one project symbol carries the name
   (`theloom/extraction/treesitter.py:72-75`, `:690-693`;
   `theloom/extraction/resolution.py:281-287`, `:305-314`). The `len` artifact above is the
   visible consequence; the invisible consequence is that every graph built by
   `extract-codebase` carries false edges of this shape.
2. **The incremental extraction path pays full-repository cost and under-delivers.**
   `codebasediff` re-extracts the whole project and then filters to changed files
   (`codebasediff.py:96`, `:107-108`), materialises the full entity list, and — despite its own
   docstring — performs neither retraction nor relation diffing, reporting zeroed statistics as
   a completed run (`codebasediff.py:1-8`, `:107-158`). A deleted-file status is computed and
   never read (`:41`).
3. **`simulate-change` snapshots the wrong graph.** The before-snapshot is taken from the
   default graph while the clone reads the requested one, and the code comment says so outright
   (`theloom/composites/simulate_change.py:244-249`). Any simulation against a non-default
   graph diffs two unrelated states.
4. **Two registered composite commands cannot work.** `enrichment-crawl` raises with the
   message that its model path "is not built" (`enrichment_crawl.py:52-58`), and
   `creativity-loop` ships stub sections returning empty lists over an unused store handle
   (`creativity_loop.py:91`, `:114-140`). Both are listed as available commands.
5. **`gap-fill-cycle`'s commit gate can never open.** The consistency value is a literal and the
   gate requires a pass the neutral simulation data makes impossible, so the threshold path
   guarantees zero commits (`gap_fill_cycle.py:224`, `:229-236`, `:74-102`).
6. **PageRank is published on the wire as `eigenvector`.** Two composites compute PageRank and
   publish it under a key naming a different algorithm (`graph_reconnaissance.py:126-132`,
   `entity_deep_dive.py:122-133`).
7. **Hard delete contradicts the invalidate-never-overwrite invariant, and the visualization
   layer inherits the breakage.** `delete_entity` issues a detach-delete
   (`theloom/store/falkor.py:203-209`); `theloom/viz/bundle.py:86-92` documents that
   point-in-time payloads can therefore be wrong, because scoping starts from what exists now
   (`scope.py:67-71`).
8. **The event log is not transactional with the mutation it records.** The append happens after
   the write query returns (`theloom/store/falkor.py:101-105`, `theloom/store/events.py:9-12`),
   so a crash between the two loses history the projection already reflects.
9. **Error classification by prose substring survives inside the command layer**
   (`theloom/operations/documents.py:149-158`, `relations.py:163-169`), against the project's
   own typed-error-code rule — and configuration errors bypass the typed hierarchy entirely
   (`theloom/config.py:49-52`).
10. **The verification gate makes the store's cross-graph bridge path unreachable**
    (`theloom/operations/relations.py:136-141`, `:171-174`), and the two halves of the test
    suite assert opposite things about it (`tests/test_ops_relations.py:102-110` versus
    `tests/test_multigraph.py:157-171`).

Two systemic performance shapes are worth naming, both encountered while generating this map.
`_search_similar` re-fetches every stored vector *and* re-lists every entity document on each
call (`theloom/operations/semantic.py:128-140`), so `find-clusters` and `semantic-gaps` are
sample-times-corpus full scans — a 500-record sample did not complete in 75 minutes on this
graph. And `list_entities` is a deliberate full graph scan filtered in Python
(`theloom/store/falkor.py:302`, `theloom/store/filters.py:37-68`), a correctness-first choice
the store's own docstrings own.

---

## 7. Open seams

Similar-but-unconnected pairs, from a 120-record sample. Two caveats first, because they change
how the list should be read: the gap finder takes the *first* N records in store order rather
than a spread sample (`theloom/operations/semantic.py:859`), and this run's first 120 are
dominated by `tapestry/e2e/` and `scripts/`. At the 0.6 similarity floor the result is
therefore mostly local variables that co-occur inside one spec file.

Only three pairs in the top twenty are architecturally meaningful:

| Similarity | Pair | Reading |
|---|---|---|
| 0.755 | `_CAUSAL_TYPES` ↔ `_NON_CAUSAL_TYPES` (`scripts/gen_bench_graph.py`) | Two halves of one partition declared separately — adjacent to the recorded tension that the non-causal fallback makes the `related_to` share larger than the comment claims. |
| 0.745 | `tapestry/playwright.config.ts` ↔ `tapestry/playwright.live.config.ts` | The two end-to-end regimes are near-duplicate configurations with no shared base — the runner-boundary duplication already recorded as a tension. |
| 0.719 | `tapestry/e2e/help.spec.ts` ↔ `tapestry/e2e/keyboard.spec.ts` | Both specs cover keyboard focus management through the same seven-line fixture-injection preamble; they are candidates for one shared helper. |

The remaining seventeen pairs (`hx0`/`hy0`, `html`/`input`, `box`/`panel`, and similar) are
same-file locals whose embedding text is nearly identical because their surrounding
observations are. They are a property of the sample, not a finding. A meaningful gap scan of
this graph would need the sampler fixed to spread across store order, or the corpus filtered to
files and types before scoring.

---

## 8. Coverage & methodology

**Coverage.** 29 of 29 module groups were described — no group was skipped. The groups are:
`scripts`, `tapestry`, `tapestry/e2e`, `tapestry/src`, `tapestry/src/lib`,
`tapestry/src/state`, `tapestry/src/views/chronicle`, `tapestry/src/views/explorer`,
`tapestry/src/views/overview`, `tapestry/src/views/semantic`, `tapestry/src/views/systems`,
`tests (part 1/2)`, `tests (part 2/2)`, `tests/fixtures`, `theloom`, `theloom/algebra`,
`theloom/analysis`, `theloom/cli`, `theloom/composites`, `theloom/documents`,
`theloom/exploration`, `theloom/extraction`, `theloom/graph`, `theloom/operations`,
`theloom/semantic`, `theloom/store`, `theloom/synthesis`, `theloom/verification`,
`theloom/viz`.

**Gaps a reader should know about.**

- **80 files were not parsed.** The extractor covers Python, TypeScript, JavaScript, Go and
  Rust source only; CSS, JSON, Markdown, YAML, TOML and lockfiles are outside it. That means
  `tapestry/src/design/tokens.css` — the file every colour claim in §2.2 ultimately depends on
  — is not in this map, nor is `pyproject.toml`, nor `docker-compose.yml`.
- **`theloom/reification` and `theloom/symbolic` have no group of their own.** Both are
  described inside the `theloom` package-root group, which is where their files were assigned.
- **Clustering and gap analysis ran on reduced samples.** `find-clusters` covered 150 of 4,437
  records and `semantic-gaps` covered 120, after full-sample runs at 500 each failed to
  complete within 75 minutes (see the performance note closing §6). Sections 5 and 7 should be
  read as indicative, not exhaustive.
- **The graph carries records from a previous incremental run.** A prior grouping labelled
  `tests-1` coexists with this run's `tests (part 1/2)` and `tests (part 2/2)`, and 615 of the
  5,052 stored records are superseded versions rather than current state. Every statement in
  this map was taken from the current projection.
- **The extraction artifact described in §5 inflates centrality.** The rankings in §3 are
  reported as measured; the `len` row is flagged rather than removed, because removing it would
  hide the defect.

**Provenance.** Graph `codebase-the-loom`, commit
`41619c1f1c0de89f3778067043266736eefaac78`, full-mode run, clean working tree. Structure was
extracted by tree-sitter with no language model involved; the purposes, patterns, invariants
and tensions were written per module group, and each carries a file-and-line anchor.

**Re-running.** `/map-codebase /Users/jameswinans/Dropbox/Development/the-loom`. The manifest
beside this file records the commit above; the next run reads it as its starting point and
re-describes only what changed.

**Interrogating the graph directly.**

```bash
loom entity-deep-dive '{"entityId": "<id>", "graph": "codebase-the-loom"}'
loom hybrid-search '{"query": "how are relations validated", "graph": "codebase-the-loom"}'
loom list-entities '{"entityType": "tension", "graph": "codebase-the-loom"}'
loom analyze-centrality '{"metric": "betweenness", "limit": 15, "graph": "codebase-the-loom"}'
```

The interactive view of the same graph is `codebase-map.html` in this directory (400 records,
1,742 connections, with the analytics and timeline sections included).
