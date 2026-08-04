---
repo: the-loom
commit: 8e33b4d1ab32146f4e979d9c4636e7e08a67d60d
graph: codebase-the-loom
generated: 2026-08-04T03:10:00Z
mode: incremental
---

# The Loom — Architecture Map

## 1. Executive overview

The Loom is a knowledge-graph substrate with exactly one way in and one way out: a
JSON-in / JSON-out command-line program. A declarative registry names 156 commands;
a generated Typer application turns that registry into subcommands at import time; a
thin protocol shell parses JSON, prints JSON, and maps typed exceptions onto six error
codes. Behind that boundary sits a single transactional store — one FalkorDB graph per
named knowledge graph, holding entity documents, relation edges, prior versions,
embedding vectors, document chunks and an append-only event stream in the same place.
Above the store sits one command-implementation tier (`theloom/operations`), a set of
pure domain libraries (graph algorithms, semiring algebra, embeddings, analogy
analysis, epistemic foraging, prose synthesis, verification rules, document ingestion,
source-tree extraction), and an orchestration tier (`theloom/composites`) that bundles
several operations into one command and returns a self-describing envelope. A second,
contributor-only workspace — Tapestry, a Vite/React/sigma.js single-page app — is
built into one self-contained HTML file that the Python package ships and that
`loom visualize` writes. Roughly a third of the tree is the test suite, which is where
the project's stated architectural invariants stop being prose and become assertions.

| | |
|---|---|
| Files mapped | 262 |
| Symbols and findings recorded | 4,211 (4,194 current, 17 superseded prior versions) |
| Recorded connections | 6,251 |
| Language mix | Python 187 files, TypeScript 73, JavaScript 2 |
| Files not parsed | 80 |
| Working tree at extraction | clean (no uncommitted changes) |
| Module groups described | 29 of 29 |
| Groups re-described this run | 0 (see §8) |

The 80 unparsed files are everything tree-sitter has no grammar for in this tree —
Markdown, JSON, CSS, YAML, TOML, HTML and lockfiles. The count rose by three since the
previous map because this map, its visualization and its manifest are themselves now
committed files. Two unparsed files are load-bearing and described here only through the
prose findings that cite them: `tapestry/src/design/tokens.css` (the colour token layer
the whole SPA resolves against) and `theloom/viz/static/tapestry.html` (the committed
build artifact).

## 2. Subsystem walkthrough

### 2.1 Package root — `theloom/`

The cross-cutting substrate everything imports and nothing imports back: the Pydantic
domain model (`model.py`) that is the single source of truth for entity and relation
shapes and enum value sets, the one configuration resolution path (`config.py`), the
typed error hierarchy (`errors.py`), the canonical timestamp format (`timeutil.py`),
plus two leaf capabilities with no store dependency — Weisfeiler-Leman structural
fingerprinting and an in-process sympy operation table. `migrate.py` reconstitutes a
snapshot folder into FalkorDB. The layer is deliberately dependency-light: model,
errors, timeutil and config import only the standard library plus pydantic, which is
what lets everything else depend on them without cycles.

Recurring shapes: enums as the sole inventory with derived runtime tuples; layered
config resolution with per-field override and no merge objects; the error code carried
on the exception class rather than inferred from prose; a dispatch table plus uniform
result envelope for symbolic operations.

Invariants worth knowing:
- Every wire timestamp is ISO 8601 UTC with a `Z` suffix, validated on load —
  `theloom/model.py:31-42`, `theloom/timeutil.py:1`.
- Models forbid unknown fields and round-trip through camelCase aliases —
  `theloom/model.py:317-320`.
- `retracted` is terminal, and same-status transitions are always valid no-ops —
  `theloom/model.py:270-294`, `:297-309`.
- A broken config file degrades to defaults; a permissive one warns but still loads —
  `theloom/config.py:107-129`.
- Symbolic evaluation never raises: every failure is a `success: false` envelope —
  `theloom/symbolic/core.py:998-1025`.

### 2.2 Persistence — `theloom/store/`

One abstract operations surface (`base.py`) and one implementation (`falkor.py`) that
maps the domain model onto a FalkorDB graph plus a Redis stream per named graph.
Entities are nodes holding the verbatim wire JSON in a `_doc` property; relations are
typed edges carrying their own document; prior incarnations are snapshotted as version
nodes bounded by `tx_from`/`tx_to`; graph metadata is a singleton node. Every mutation
is one atomic Cypher query followed by an append to the graph's event stream, so
current state is a projection and history is replayable. Embedding vectors live in the
same store. `filters.py` holds filter semantics as pure functions; `paging.py` makes
unbounded reads immune to the server's result-set truncation; `multigraph.py` is the
facade over named graphs and the cross-graph bridge registry.

Invariants worth knowing:
- Every state mutation appends an event; current state is a projection —
  `theloom/store/falkor.py:105`, `:199-200`, `:208`, `:275-285`, `:364`.
- Updates invalidate; they never overwrite history — `theloom/store/falkor.py:191-198`,
  with the as-of read at `:162-173`, declared at `theloom/store/base.py:16-17`.
- `id` and `created_at` (plus `from`/`to` on relations) survive any update —
  `theloom/store/falkor.py:55-56`, enforced at `:187-188` and `:435-436`.
- Unbounded reads must page or the server silently truncates them —
  `theloom/store/paging.py:1-11`, `:31-45`.
- Relation endpoints must exist, verified by counting the edges actually created —
  `theloom/store/falkor.py:353-363`.
- The store serves exactly the document bytes it was given —
  `theloom/store/falkor.py:5-8`, `:98-104`, `:211-214`.
- Named graphs are registry-tracked and name-validated; the default graph cannot be
  deleted — `theloom/store/multigraph.py:33`, `:164-172`, `:178-184`.

### 2.3 The command surface — `theloom/cli/`

The entire user- and agent-facing boundary. `registry.py` enumerates 156 commands
across 21 categories as frozen descriptors, each pairing a name, category, one-line
summary, a Pydantic input model, and a handler of shape `(validated_model, MultiGraph)`.
`app.py` turns that list into Typer subcommands at import time, so no command is ever
hand-defined and the surface cannot drift from its declaration. `io.py` parses JSON
from the positional argument or standard input, prints results as indented JSON on
stdout, and serializes `{error, code}` to stderr with exit 1. `docs.py` projects the
same registry into `COMMANDS.md`, so the catalog is a derivation rather than a parallel
artifact. The package holds no domain logic.

Invariants worth knowing:
- Every command exists because a descriptor exists — `theloom/cli/app.py:108-109`,
  `theloom/cli/registry.py:1580`, `:1587-1597`, with the two documented exceptions at
  `theloom/cli/app.py:54-78`.
- Input validation happens exactly once, at dispatch —
  `theloom/cli/registry.py:1587-1597`.
- stdout carries only the result document; diagnostics go to stderr with exit 1 —
  `theloom/cli/io.py:67-84`, `theloom/cli/app.py:96-102`.
- Input arrives from the argument or a pipe, never from an interactive prompt —
  `theloom/cli/io.py:30-53`.
- Unknown input keys are stripped, not rejected — `theloom/cli/registry.py:69-72`.
- Handlers are stream-free, so the whole surface is testable in-process —
  `theloom/cli/registry.py:106-107`, `:1587-1597`.

### 2.4 Command implementations — `theloom/operations/`

The single tier between the registry and the store. Each command resolves to one
module-level handler here; a handler declares its wire contract as a Pydantic input
class, resolves a target graph, calls the store and/or a domain library, and returns a
wire-ready dictionary. Everything that is command semantics rather than storage
semantics lives here: revision and version bookkeeping, auto-dating of confidence and
provenance, verification-gate enforcement, polarity defaulting, dry-run previewing,
batch error collection.

Invariants worth knowing:
- `update-entity` always bumps version and self-references the previous version —
  `theloom/operations/entity.py:243-244`, documented at `:5-8`.
- Causal relations carry polarity: defaulted on the single path, rejected on the batch
  path — `theloom/operations/relations.py:130-133`, `:206-211`.
- `create-relations` persists the valid prefix even when it aborts —
  `theloom/operations/relations.py:186-192`, `:226-228`.
- `merge-entities` never hard-deletes the secondary —
  `theloom/operations/merge.py:203-211`, `:174-175`, `:96-102`.
- `bulk-import` is idempotent on the `name::entityType` composite key —
  `theloom/operations/bulk.py:206-222`, `:262-273`.
- Embedding is content-hash gated and self-healing —
  `theloom/operations/semantic.py:290-298`, `:363-370`, `:313-316`.
- Similarity scores are `1/(1+L2)`, never raw cosine —
  `theloom/operations/semantic.py:114-116`, applied at `:149`.
- `validate-mutation-trace` replays on a disposable clone and always destroys it —
  `theloom/operations/verification.py:619-631`, `:655-682`.
- Documents are global: every document command accepts and ignores its graph parameter
  — `theloom/operations/documents.py:79`.

### 2.5 Orchestration — `theloom/composites/`

The top of the call stack. Each module bundles several single-purpose operations into
one command and returns an envelope in which every constituent step is a named section
carrying its own data, wall-clock duration and error. The tier owns no algorithms: it
owns sequencing, defaulting, prerequisite gating, per-section fault isolation, and
human-readable summaries. Composites range from read-only surveys
(`graph-reconnaissance`, `entity-deep-dive`, `structural-survey`, `influence-map`)
through generative pipelines (`far-analogy-retrieval`, `hypothesis-engine`,
`propose-entities`) to write-capable cycles (`gap-fill-cycle`, `verified-extract`,
`self-improve`) and a dry-run sandbox (`simulate-change`).

Invariants worth knowing:
- The section timer never propagates an exception; failure becomes `data: null` plus an
  error string — `theloom/composites/framework.py:39-53`.
- Every section value is exactly `{data, durationMs, error}`, including hand-built ones
  — `theloom/composites/framework.py:24-25`, `:49`, `:53`, `:58`.
- `simulate-change` never mutates the target graph: mutations land on a `sim-<uuid>`
  clone dropped in a `finally` block —
  `theloom/composites/simulate_change.py:100-110`, `:240-312`.
- `self-improve` is human-in-the-loop by default: no writes unless `autoApply` is true
  — `theloom/composites/self_improve.py:299-301`.
- Store resolution happens outside sections, so an unknown graph is a typed CLI error
  rather than a section failure — `theloom/composites/enrichment_crawl.py:38-40`.

### 2.6 Graph algorithms — `theloom/graph/`

The in-memory structural layer. It hydrates store query results into a small
insertion-ordered directed multigraph and runs every structural analysis the CLI
exposes: centrality and components, cycle and feedback-loop detection and
classification, shortest and bounded all-simple paths, frequent-subgraph mining,
subgraph extraction, and regex parsing of the observation strings that encode loop and
Meadows leverage-point metadata. The layer is store-free and deterministic —
enumeration order is treated as part of the output contract, not an implementation
detail.

Invariants worth knowing:
- Hydration silently drops relations whose endpoints are not in the entity list —
  `theloom/graph/hydrate.py:111-120`.
- Neighbor iteration order is part of the contract: incoming endpoints before outgoing,
  deduplicated — `theloom/graph/hydrate.py:87-96`, `:73-85`.
- PageRank fails loudly rather than returning unconverged scores —
  `theloom/graph/analytics.py:55-68`.
- Loop classification treats a missing relation polarity as `+` —
  `theloom/graph/cycles.py:248-266`.
- Circuit enumeration destructively rewrites the adjacency list it is handed —
  `theloom/graph/cycles.py:121-125`.

### 2.7 Semiring algebra — `theloom/algebra/`

The pure algebraic kernel of the query layer. `core.py` defines five semirings
(boolean, tropical, viterbi, counting, capacity), each paired with a weight extractor
that reads a relation's strength, plus one depth-bounded traversal engine parameterised
over the semiring and over how adjacency is obtained (in memory, or lazily from the
store). `routing.py` builds a second layer: relation types partition into three
categories (structural, epistemic, causal), six morphisms convert values across
category boundaries, composer rules adjust value at causal/epistemic boundaries, a
planner picks a strategy, and executors run the plans. No persistence, no CLI, no
validation.

Invariants worth knowing:
- The traversal value is a semiring aggregate, but the returned path is a single
  first-winning witness — `theloom/algebra/core.py:191-210`, stated at `:3-7`.
- Lazy adjacency emits all natural edges before all reversed incoming edges, and the
  grouping is load-bearing — `theloom/algebra/core.py:124-148`.
- Cross-type and metapath traversal is outgoing-only; direction control exists solely
  in the core engine — `theloom/algebra/routing.py:271-273`, `:484-486`.
- The morphism table is total over ordered pairs of distinct categories —
  `theloom/algebra/routing.py:63-96`, with the planner raising at `:210-212`.

### 2.8 Analogy and structural analysis — `theloom/analysis/`

Eleven modules that compute cross-domain concept mappings, transfer structure from a
source domain to a target as concrete entity proposals, and score how novel,
surprising, adaptable and interesting the result is. The pipeline is Gentner structure
mapping plus Hofstadter concept slippage plus Keane adaptability constraints plus
compression-progress interestingness, assembled as a chain of pure functions over plain
wire dictionaries. Weisfeiler-Leman component fingerprints, optimal-transport distance
between signature clouds, and approximate subgraph matching form the structural
retrieval side.

Invariants worth knowing:
- Unmapped source endpoints become namespaced `__NOVEL__` placeholders, never real ids
  — `theloom/analysis/cwsg.py:31`, `:110-119`.
- Only relations connected to the matched relational structure transfer —
  `theloom/analysis/cwsg.py:370-393`, `:81-85`.
- Concept slippage is best-effort: its failure degrades transfer, never aborts it —
  `theloom/analysis/cwsg.py:164-166`.
- Every emitted score is clamped to `[0,1]`, and zero total weight yields 0 rather than
  a division error — `theloom/analysis/analogy_confidence.py:18-19`, `:47-50`, `:76-79`.
- Component signatures are comparable only across a shared global hash ordering —
  `theloom/analysis/component_signatures.py:113-125`.

### 2.9 Epistemic foraging — `theloom/exploration/`

Independent signals that score knowledge regions (connected components) for the
`explore-frontier` composite: how stale a region is, how much value lies in bridging
out of it, how large an embedding-space void surrounds it, how much exploration bonus
it deserves, whether the forager should leave the patch (Charnov's Marginal Value
Theorem), and whether the exploration itself is pathological (six anti-pattern
detectors). Nothing here orchestrates; every public function takes regions plus data
and returns a section result, so a failing signal degrades one section rather than the
whole command.

Invariants worth knowing:
- Gain histories are bounded to the most recent 100 entries per entity and per region —
  `theloom/exploration/exploration_state.py:31`, `:34`, `:128-129`, `:143-144`.
- Cold-start regions are never told to leave; staleness stands in for missing history —
  `theloom/exploration/mvt_patch_leaving.py:82`, `:86-87`, `:110-121`.
- Region identity is the smallest entity id, so it is stable across invocations —
  `theloom/exploration/exploration_state.py:89-97`.
- Exploration state is in-memory only and starts zeroed on every construction —
  `theloom/exploration/exploration_state.py:6-22`, `:101`, `:103-104`.
- Missing signals are dropped and the remaining composite weights renormalize to 1 —
  `theloom/exploration/composite_signals.py:70-88`.

### 2.10 Prose synthesis and CEGIS — `theloom/synthesis/`

Turns a natural-language query plus a graph into grounded prose. The pipeline is
plan / traverse / realize: planning selects an ego subgraph around query anchors,
decomposes the query, and partitions the result into ordered regions; traversal walks
those regions emitting deduplicated evidence units with a step-by-step provenance log;
realization linearizes each region topologically over causal edges and produces text
either through a language model or through deterministic templates. A fidelity pass
scores how faithfully the text preserves the entities and relation directions it was
built from. Separately, the CEGIS engine generates type-valid candidate graphs from a
seeded generator and refines them against counterexamples.

Invariants worth knowing:
- The synthesis pipeline is read-only; the CEGIS commit is the only write path —
  `theloom/synthesis/selector.py:31-38`, `theloom/synthesis/cegis.py:440-446`.
- No synthesis command fails because the language model is absent or broken —
  `theloom/synthesis/realizer.py:307-313`, `:331-332`.
- Selection bounds are clamped regardless of caller input (depth ≤ 10, entities ≤ 1000)
  — `theloom/synthesis/selector.py:166-172`.
- The provenance collector is write-once: no step may be added after finalize —
  `theloom/synthesis/traverser.py:53-55`, `:77-86`.
- CEGIS always terminates: every loop path returns or increments the counter —
  `theloom/synthesis/cegis.py:382-419`.
- Composite fidelity is a weighted harmonic mean that collapses to zero if either rate
  is zero — `theloom/synthesis/fidelity.py:351-359`.

### 2.11 Correctness rules — `theloom/verification/`

The shared, store-agnostic predicates behind every correctness surface: per-element
guards (confidence bounds, entity type, observations, causal polarity, self-loops,
duplicate relations), the five built-in graph invariants, a capability-spec DSL that
turns structural expectations into a gap list, and an AC-3 constraint-propagation
solver over the 19 entity types. It owns no commands and no store access; the callers
wrap these predicates into behavior.

Invariants worth knowing:
- Causal relations without polarity are rejected at write time, not merely reported —
  `theloom/verification/guards.py:48-52`; the reporting form is
  `theloom/verification/checks.py:85-103`.
- Entity creation is never blocked by a guard; violations become observations —
  `theloom/verification/guards.py:25-36`, documented at `:5-7`.
- AC-3 worklist order is part of the output contract —
  `theloom/verification/propagation.py:104`.
- Lifecycle statuses carry structural obligations: superseded is causally inert,
  retracted is isolated — `theloom/verification/checks.py:264-279`, `:282-297`.

### 2.12 Document ingestion — `theloom/documents/`

Turns files, directories, raw content and URLs into embedded, searchable chunk rows in
the same store that holds the graph. The pipeline is parse → chunk → embed → upsert:
parsers normalize every supported format into a uniform block list, the chunker groups
blocks into overlapping size-bounded chunks with stable content hashes, an SSRF guard
hardens the URL fetch path, and the chunk store persists chunks as nodes with optional
vectors. Ingestion is the only writer of chunk rows; reingest is the incremental path
that diffs by content hash at the same chunk index.

Invariants worth knowing:
- A chunk's content hash covers the overlap prefix, not just the chunk body —
  `theloom/documents/chunker.py:229`, `:219`.
- Groups containing code or list blocks are never split, even past the size limit —
  `theloom/documents/chunker.py:165-167`.
- Every resolved address of a URL host must be globally routable —
  `theloom/documents/ssrf.py:58-71`, `:32-36`.
- `sourceId` is a deterministic hash prefix of the resolved path or URL —
  `theloom/documents/ingestion.py:33-35`, `:38-39`.
- Document chunks live outside every knowledge graph, in one per-prefix chunk graph —
  `theloom/documents/chunkstore.py:27`, stated at `:4-7`.

### 2.13 Source and document extraction — `theloom/extraction/`

Two independent halves sharing a namespace. The codebase half parses source trees with
the native tree-sitter bindings and emits a deterministic entity/relation document —
file to system, class-like to concept, function to procedure, variable to variable,
imports to `requires`, inheritance to `instance_of`, resolved calls to `related_to` —
then keeps that graph fresh by diffing the repository between a stored marker commit
and HEAD. The document half drives a language model over stored chunks, validates the
returned JSON against a fixed type whitelist, writes it into the target graph with
`sources` links back to a synthetic source entity, and records a run ledger.

Invariants worth knowing:
- Codebase extraction output is deterministic for a given source tree —
  `theloom/extraction/treesitter.py:560-577`, required at `:11-13`.
- Symbol names are qualified by file basename, not by path, and the first occurrence
  wins — `theloom/extraction/treesitter.py:68-71`, `:464-466`.
- Every supported source file yields exactly one system entity, even when nothing
  parses — `theloom/extraction/treesitter.py:449-459`, `:411-417`.
- Call edges survive only when caller and callee are both symbols of the same file —
  `theloom/extraction/treesitter.py:537-550`, `:461-470`. **This is the single most
  consequential fact about the map you are reading;** see §5.
- Self-model update is idempotent per commit —
  `theloom/extraction/selfmodel.py:85-92`, `:107-108`.
- Only whitelisted entity and relation types survive language-model response parsing —
  `theloom/extraction/pipeline.py:30-67`, `:69-81`.

### 2.14 Visualization backend — `theloom/viz/`

Turns a live graph into one versioned JSON document and delivers it three ways: as raw
JSON, injected into a committed single-file SPA build, or served read-only over HTTP.
It owns scoping (which slice is shown), enrichment (centrality, components, loops,
leverage points, bridges, the event stream for client-side replay, and a 2-D projection
of entity embeddings with clusters), the wire contract with the frontend, and the cost
guardrails that keep assembly bounded. It computes nothing the rest of the system
already computes.

Invariants worth knowing:
- Every scope mode ships a closed subgraph: no relation survives without both endpoints
  — `theloom/viz/scope.py:110-112`, `:76-82`, `theloom/viz/bundle.py:71`.
- Degree truncation is deterministic and order-preserving —
  `theloom/viz/bundle.py:66-70`, intent at `:47-54`.
- An `asOf` bound restricts entities, relations and the event log only; analytics and
  semantic sections stay current — `theloom/viz/bundle.py:117-123`.
- The bundle ships entities of every status, not just active ones —
  `theloom/viz/scope.py:44`, rationale at `:39-43`.
- Injected bundle JSON can never terminate the host script block —
  `theloom/viz/html.py:33`, documented at `:4-5`.
- A template missing its sentinel fails loudly as a config error —
  `theloom/viz/html.py:28-32`, `:37-44`.
- The live server exposes read paths only — `theloom/viz/serve.py:106` and the other
  route registrations, intent at `:1-2`.

### 2.15 Frontend build harness — `tapestry/`

The seam that turns the contributor-only frontend workspace into the single
self-contained HTML artifact the Python package ships. The Vite config configures the
single-file production build and fences the unit-test runner off from the Playwright
directories; a post-build Node step promotes the built page into the Python package
tree after checking the data sentinel survived bundling; two Playwright configs define
two disjoint end-to-end regimes (a static `file://` build mirroring what `visualize`
emits, and a live regime pointed at a running server). Nothing here is imported by
application code at runtime.

Invariants worth knowing:
- The build refuses to emit a template that lost the bundle sentinel —
  `tapestry/scripts/emit-template.mjs:4-7`.
- Template emission is bound to the tapestry working directory —
  `tapestry/scripts/emit-template.mjs:3`, `:8`.
- The unit-test runner never executes the Playwright suites — `tapestry/vite.config.ts:12`.
- The live smoke test pins the affordances that exist only in served mode —
  `tapestry/e2e-live/live.spec.ts:14`, `:17`, `:20-23`, `:26-27`.

### 2.16 Application shell — `tapestry/src/`

Deliberately thin. One React root wrapped in a bundle provider; one shell component
that is chrome around five interchangeable views — header brand and counts, a tab list,
a live-mode graph switcher and refresh, a theme radio group, a polite live region, the
entity-type colour ribbon, and the help modal — delegating all data work to `lib/` and
all rendering to `views/`. The design directory holds the shared visual vocabulary: the
canonical 19-value entity-type list, the CSS-custom-property indirection for its
colours, and the tri-state theme resolver.

Invariants worth knowing:
- The type-to-colour helper always yields a defined CSS colour, never undefined —
  `tapestry/src/design/palette.ts:35-39`.
- The document root always carries a concrete `data-theme` of dark or light, never auto
  — `tapestry/src/design/theme.ts:15-26`, `tapestry/src/App.tsx:256-263`.
- The tab list and the theme radio group are each exactly one tab stop —
  `tapestry/src/App.tsx:303`, `:365`, `:193-208`.
- The live region stays silent for content present at first load —
  `tapestry/src/App.tsx:182-190`.
- The help modal takes focus on open, traps it, and returns it to the trigger on close
  — `tapestry/src/views/HelpOverlay.tsx:70-74`, `:82-98`, `tapestry/src/App.tsx:216-219`.

### 2.17 View state — `tapestry/src/state/`

One flat store holding every cross-cutting UI concern the five views share — active
view, theme, selection, filter facets, path-tool mode and endpoints, isolated loop,
scrubber time and diff anchor, lasso brush — each with a thin named setter. A second
module is the shareable-link boundary: it serializes a four-field subset into a URL
fragment, parses it back defensively, and funnels application of a hash through one
function so initial-mount restore and saved-view activation take identical effect. No
data fetching, no rendering, no graph logic.

Invariants worth knowing:
- Hash parsing is total: no fragment can throw — `tapestry/src/state/urlHash.ts:16`, `:17-21`.
- Applying a hash distinguishes an absent key from an explicit null —
  `tapestry/src/state/urlHash.ts:35`, `:37`.
- The filter setter merges; every other setter replaces — `tapestry/src/state/store.ts:72`.
- Clearing the path keeps path mode on — `tapestry/src/state/store.ts:75`, `:73`.

### 2.18 Shared frontend runtime — `tapestry/src/lib/`

Everything the four canvas views need and none of them owns: three-mode bundle loading
(inline JSON injected by the renderer, the dev fixture, or the live endpoint) plus the
React context that memoizes the graph model; canvas interaction primitives — node
dragging with a click-versus-drag latch, wrapped labels with a reveal policy,
roving-tabindex keyboard math, an app-wide keydown dispatcher; WYSIWYG export of any
view to SVG or flattened PNG; and per-graph saved views persisted in local storage with
a portable import/export envelope. The layer is split into DOM-free pure cores and thin
impure shells so the trickiest decisions are unit-tested without a browser.

Invariants worth knowing:
- A drag that moved must swallow exactly one trailing click —
  `tapestry/src/lib/dragNodes.ts:155-159`, `:81-85`, `:106`, `:111`.
- The gesture's moved flag is sticky until release — `tapestry/src/lib/dragState.ts:46-57`.
- Overriding the label renderer requires overriding the hover renderer too —
  `tapestry/src/lib/nodeLabels.ts:193-206`, `:208-254`.
- SVG export serializes only the supplied visibility set, re-checking edge endpoints —
  `tapestry/src/lib/exportSvg.ts:140`, `:153-154`.
- Saved-view storage never throws and never clobbers a rename target —
  `tapestry/src/lib/savedViews.ts:25-34`, `:60-70`, `:93-123`.
- Global shortcuts never fire while the user is typing or holding a modifier —
  `tapestry/src/lib/keyboard.ts:32`, `:33` with `:20-24`.

### 2.19 Overview view — `tapestry/src/views/overview/`

A read-only dashboard: a single-screen roll-up covering composition (entity- and
relation-type distributions), graph health (contradictions, dangling relations,
unscored entities), confidence coverage as a ten-bin histogram, and the most central
entities. It derives every number from the bundle, renders with token-driven CSS bars,
and its only side effects are navigation and printing.

Invariants worth knowing:
- Confidence coverage is exhaustive: scored plus unscored equals the entity count —
  `tapestry/src/views/overview/stats.ts:71-78`.
- The histogram is exactly ten bins over `[0,1]`, with a perfect score clamped into the
  last bin — `tapestry/src/views/overview/stats.ts:58`, `:76`.
- Dangling relations are counted against the bundle arrays, not the rendered model, so
  the integrity signal survives — `tapestry/src/views/overview/stats.ts:56`, `:63-65`, `:89-91`.
- The centrality table is capped at ten entities, sorted descending —
  `tapestry/src/views/overview/stats.ts:96-99`.

### 2.20 Graph Explorer — `tapestry/src/views/explorer/`

The default view. It converts a raw bundle into a directed multigraph whose node and
edge attributes carry every visual channel the renderer reads (fill by entity type,
size by degree, tint by relation family, width by strength), settles it with a force
layout, then overlays chrome: fuzzy search, a faceted filter panel, a detail panel, a
shortest-path tool with a hop trail, a colour legend, a minimap, and a saved-views and
export menu. The organising decision is that reader interaction never mutates the
graph — filtering, hovering, selecting, path highlighting and lasso brushing are all
layers inside renderer reducers over one immutable model.

Invariants worth knowing:
- Filtering is a returned visibility set, never a graph mutation —
  `tapestry/src/views/explorer/filters.ts:45-62`.
- Absent confidence passes any confidence floor —
  `tapestry/src/views/explorer/filters.ts:36-40`, stated at `:12-14`.
- An edge is visible only when both endpoints are visible —
  `tapestry/src/views/explorer/filters.ts:51-59`.
- Colour encodes the entity, never the filter state —
  `tapestry/src/views/explorer/buildGraph.ts:28-36`, `tapestry/src/views/explorer/filters.ts:6-8`.
- Dangling relations are skipped silently, not treated as errors —
  `tapestry/src/views/explorer/buildGraph.ts:191-208`.
- Path search is undirected for reachability but returns real directed edges —
  `tapestry/src/views/explorer/pathMode.ts:45-59`, `:23-33`, `:35-43`.
- Every renderer resource is released on unmount —
  `tapestry/src/views/explorer/Explorer.tsx:299-307`, `:366-369`.

### 2.21 Systems view — `tapestry/src/views/systems/`

Renders the causal slice as a systems-dynamics diagram rather than a knowledge graph: a
directed multigraph containing only causal relations and the entities they touch, edges
coloured and glyphed by polarity, a right rail of feedback loops that isolates one loop
on click, an optional signed flow pulse travelling the isolated loop in its influence
direction, and numbered leverage-point badges seated on the variables they act on. A
DOM-free model layer is separated from the rendering layer.

Invariants worth knowing:
- The systems graph contains only causal relations and their endpoints —
  `tapestry/src/views/systems/systems.ts:63-72`, `:75-89`.
- Loop edge keys are resolved through directed out-edges, never undirected edges —
  `tapestry/src/views/systems/systems.ts:125-139`.
- The flow animation runs only while a loop is isolated —
  `tapestry/src/views/systems/SystemsView.tsx:358-366`, `:350-352`.
- Reduced-motion viewers get the frozen pulse, not a disabled feature —
  `tapestry/src/views/systems/SystemsView.tsx:79-85`, `:367-370`.
- Malformed bundle rows are skipped, never fatal —
  `tapestry/src/views/systems/systems.ts:68`, `:77`, `:96`, `:135-137`, `:172-174`.

### 2.22 Chronicle view — `tapestry/src/views/chronicle/`

Bi-temporal time travel: it reimplements the store's point-in-time read semantics in
the browser over the event log shipped inside the bundle. A timeline builder reshapes
events into millisecond lookups; a projection answers, for any instant, which nodes and
edges of the current model were visible then and each node's effective status; a diff
classifies what changed between two instants as added, invalidated or changed. Dragging
the scrubber replays the graph assembling itself with no graph mutation and no server
round trip.

Invariants worth knowing:
- Replay projects; it never mutates the shared model —
  `tapestry/src/views/chronicle/replay.ts:206-232`, `Chronicle.tsx:190-242`.
- An element with no creation event is treated as present from the start of time —
  `tapestry/src/views/chronicle/replay.ts:207-208`, `:226-227`, documented at `:194-197`.
- An edge is visible at an instant only if both endpoints are —
  `tapestry/src/views/chronicle/replay.ts:230`.
- Diff windows are half-open and order-independent —
  `tapestry/src/views/chronicle/replay.ts:243-244`, `:248`, `:254`, `:264`.
- A timeline's span is never zero — `tapestry/src/views/chronicle/replay.ts:184-186`.

### 2.23 Semantic Map — `tapestry/src/views/semantic/`

Plots each entity at its precomputed projection coordinate, so on-screen distance
encodes semantic similarity rather than graph adjacency. It renders no edges. Cluster
membership is drawn as a soft convex-hull region per cluster, and a freehand lasso (or,
for keyboard users, a cluster menu) writes the enclosed ids into the shared brush slice,
which the Explorer then reads as a highlight layer.

Invariants worth knowing:
- Node position equals the bundle projection coordinate; the view runs no layout —
  `tapestry/src/views/semantic/semanticMap.ts:54-61`, `SemanticView.tsx:161-280`.
- A point exists only when the projection id resolves to an entity with a 2-D
  coordinate — `tapestry/src/views/semantic/semanticMap.ts:33-46`.
- Hull and lasso geometry is computed in viewport pixels, never graph space —
  `tapestry/src/views/semantic/SemanticView.tsx:243-249`, `:381-388`.
- A lasso of fewer than three points never mutates the brush —
  `tapestry/src/views/semantic/SemanticView.tsx:52-53`, `:375-393`.
- Brushing dims but never filters, so exports carry every point —
  `tapestry/src/views/semantic/SemanticView.tsx:194-199`, `:395-400`.

### 2.24 Frontend end-to-end suite — `tapestry/e2e/`

Guards the visualization the way users actually receive it: as the single
self-contained HTML file. Every spec rebuilds that artifact by injecting the committed
dev fixture into the committed template and opening the result over `file://`,
deliberately bypassing the dev server. Seven specs partition the surface — boot smoke
plus each of the five view tabs, an accessibility audit across theme × tab, keyboard
operability, the focus-trapped help modal, node dragging and post-drag click
suppression, the export filename contract, and the saved-views lifecycle.

Invariants worth knowing:
- Specs exercise the built single-file artifact, never a dev server —
  `tapestry/e2e/smoke.spec.ts:6-22` and identically in the other six specs.
- Only serious and critical accessibility violations gate the suite —
  `tapestry/e2e/a11y.spec.ts:42-45`.
- Exported files must match `<graph>-<view>-<date>.<ext>` —
  `tapestry/e2e/export.spec.ts:31-32`.
- A drag never registers as a click, so it can never pick a path endpoint —
  `tapestry/e2e/drag.spec.ts:100-134`, `:81-85`.
- Malformed saved-view imports are rejected without mutating existing views —
  `tapestry/e2e/savedviews.spec.ts:89-101`, `:41-53`.

### 2.25–2.26 Python test suite — `tests/` (two halves)

The first half pins the substrate: the domain model (19 entity types, 15 relation
types, a five-state lifecycle), the config precedence chain, the CLI protocol and typed
error codes, the store (CRUD, filters, parallel edges, event-log append, point-in-time
reads), the multi-graph manager and bridge registry, the operations layer's added
semantics, the snapshot importer, and the pure-analysis foundations. It also holds the
shared fixtures the second half consumes. The second half pins mutation operations
(merge, relations, triggers, init, session provenance), the security and pure-computation
units, and the entire visualization pipeline (scope, bundle assembly, analytics,
semantic projection, temporal replay, as-of reads, HTML rendering, schema drift, and
the live server).

Invariants worth knowing:
- Every database-touching test is namespace-isolated and self-cleaning —
  `tests/conftest.py:33-43`.
- Every store mutation appends exactly one typed event, in order —
  `tests/test_falkor_store.py:393-401`, `:404-420`.
- Point-in-time reads return historical state, not current state —
  `tests/test_falkor_store.py:438-452`, `:455-457`.
- Error codes come from typed exceptions, never from prose matching —
  `tests/test_cli_io.py:66-74`.
- Full-scan reads must survive the server's truncation cap —
  `tests/conftest.py:46-55`.
- Documented invocations must validate against the live registry schemas —
  `tests/test_claude_examples_contract.py:146-160`.
- `merge-entities` supersedes the secondary; it never hard-deletes —
  `tests/test_ops_merge.py:250-264`.
- A dry run computes the full preview with zero writes and zero events —
  `tests/test_ops_merge.py:329-355`.
- Private and reserved addresses are rejected before any connection is attempted —
  `tests/test_ssrf.py:16-34`, `:58-61`, `:83-94`.
- The committed bundle schema must equal the live export —
  `tests/test_viz_schema_drift.py:14-16`.
- Bundle truncation is degree-ranked, reproducible, and self-declaring —
  `tests/test_viz_bundle.py:64-75`, `:86-117`, `:120-128`.
- The abstract store surface is an exact, enumerated set — `tests/test_store_base.py:16-38`.

### 2.27 Extractor fixture — `tests/fixtures/repo/`

A four-file synthetic "Sample Service" that exists only as input to the tree-sitter
extractor. Nothing imports or executes it; its whole job is to present, in the smallest
hand-countable surface, one instance of every symbol and edge shape the extractor claims
to support — plain JavaScript functions with a CommonJS export, a TypeScript interface
plus class with a relative import, a Python dataclass with a method and a factory, and a
second Python module importing the first — so extraction can be asserted against exact
literal totals.

Invariants worth knowing:
- Any edit to the fixture breaks the pinned extraction totals —
  `tests/fixtures/repo/src/models.py:7,13,19`, `tests/fixtures/repo/lib/index.ts:3,7,8,10,16`.
- Fixture modules are parse-only: never imported, never executed —
  `tests/fixtures/repo/src/service.py:3`, `tests/fixtures/repo/src/models.py:3`.
- Import edges target raw specifier strings, not resolved files —
  `tests/fixtures/repo/lib/index.ts:1`, `tests/fixtures/repo/src/service.py:3`.

### 2.28 Seeding utilities — `scripts/`

Developer-only, out-of-band graph seeding. Neither file is imported by the package;
both speak to the store layer directly rather than through the command registry. One
fabricates the throwaway benchmark graph (50k entities / 100k relations by default) the
visualization guardrails were tuned against; the other builds the two tiny demo graphs
the live end-to-end regime boots against. Both are deliberately embedding-free, so no
model download or vector write ever intrudes on a benchmark timing or a CI job.

Invariants worth knowing:
- The benchmark generator can only ever write to `tapestry-bench` —
  `scripts/gen_bench_graph.py:68`, enforced at `:162-165`, `:132-151`.
- Synthetic relations carry model-default polarity for causal types and null otherwise
  — `scripts/gen_bench_graph.py:97-105`.
- Neither seed script can trigger an embedder load —
  `scripts/gen_bench_graph.py:46-63`, `scripts/seed_live_dev.py:10-16`.
- The dev seed always yields a three-node balancing loop plus a second non-empty graph
  — `scripts/seed_live_dev.py:31-42`, `:44-53`, `:55-60`.

## 3. Load-bearing modules

Two centrality measures were run over the mapped graph. Degree centrality ranks files
by how much they carry — the symbols defined inside them plus the findings anchored to
them. Betweenness ranks functions by how much of the within-file call structure routes
through them. Both rankings are unchanged from the previous map; the code they describe
did not move.

**Files by degree (top 15).** Each line notes why the file is a hub.

| # | File | Why it is load-bearing |
|---|---|---|
| 1 | `tapestry/src/views/chronicle/Chronicle.tsx` | Largest single file in the tree (98 defined symbols): the Chronicle renders the temporal projection through canvas reducers, two DOM overlay layers, playback and diff, all in one component. |
| 2 | `tapestry/src/views/explorer/Explorer.tsx` | 95 symbols. The default view's shell — renderer instantiation, five reducer layers, keyboard and camera navigation, exports and saved views. |
| 3 | `tapestry/src/views/semantic/SemanticView.tsx` | 76 symbols plus the most findings of any frontend file (17). Owns the canvas, hull overlays, lasso, exports and accessibility affordances of the embedding view. |
| 4 | `tapestry/src/views/systems/SystemsView.tsx` | 77 symbols, 16 findings. Canvas wiring, overlays, animation and exports for the causal-loop view. |
| 5 | `tests/test_entity_proposer_foundation.py` | 69 symbols — the largest test module, and the only pin on the proposal engine's structural strategy. |
| 6 | `theloom/store/falkor.py` | The single store implementation (43 symbols) and the most-discussed file in the repository (19 findings). Every architectural invariant about atomicity, bi-temporality and event logging is enforced here. |
| 7 | `tapestry/src/lib/exportSvg.ts` | 52 symbols. Sole owner of both export formats for every canvas view. |
| 8 | `tapestry/src/views/chronicle/replay.ts` | The pure temporal-projection engine — the browser-side reimplementation of point-in-time reads. |
| 9 | `theloom/operations/epistemic.py` | 46 symbols; the largest single operations module. |
| 10 | `tapestry/src/views/overview/Overview.tsx` | The dashboard; 14 findings, including the three tiles computed outside its own tested roll-up. |
| 11 | `tests/test_falkor_store.py` | The store's contract in executable form: event ordering, bi-temporal reads, parallel edges, status transitions. |
| 12 | `theloom/operations/semantic.py` | Owns embedding lifecycle, similarity scoring and every vector-backed discovery command. |
| 13 | `tapestry/src/views/chronicle/EventList.tsx` | The virtualized event rail; 43 symbols. |
| 14 | `tests/test_synthesis_units.py` | The synthesis pipeline's only unit-level pin. |
| 15 | `theloom/cli/registry.py` | The 1,600-line command table (17 findings). Every subsystem is wired here, so it imports nearly the whole package and is the file any new capability must touch. |

**Functions by betweenness (top 15).** These are the articulation points of the call
graph: `_resolve_graph_param` and `_run_pipeline` (`theloom/operations/synthesis.py`),
`extract_from_source` and `extract_from_files` (`theloom/extraction/treesitter.py`),
`run_cegis`, `verify_candidate` and `_eval_property` (`theloom/synthesis/cegis.py`),
`_search_similar` and `semantic_neighbors` (`theloom/operations/semantic.py`),
`compute_structural_prediction` and `score_absence` (`theloom/analysis/absence_surprise.py`),
`execute_segmented` (`theloom/algebra/routing.py`), and three test helpers
(`_two_isomorphic_components`, `make_four`, `_install_stub`). The pattern is
consistent: each is the funnel its module's public entry points converge on, so a
change to any of them reaches every command that module backs.

Note the measure's scope. Because call edges are recorded only within a file (§5),
betweenness here describes *intra-file* structure. It is a good guide to which function
inside a module is the chokepoint; it says nothing about cross-module routing.

## 4. Dependency cycles

Cycle detection found ten cycles. **Every one is a self-loop** — a function that calls
itself. There is not a single multi-node cycle in the graph.

| Member | File | Verdict |
|---|---|---|
| `_substitute` | `tests/test_claude_examples_contract.py` | intentional — recursive descent over a nested JSON payload substituting placeholder values. |
| `_jsonify` | `theloom/cli/io.py` | intentional — recursive JSON normalization; the non-finite-float rule at `theloom/cli/io.py:56-69` must apply at every depth. |
| `_generic_json_to_blocks` | `theloom/documents/parsers.py` | intentional — recursive walk of arbitrarily nested JSON into document blocks. |
| `_extract_calls` | `theloom/extraction/treesitter.py` | intentional — recursive syntax-tree walk. |
| `_find_identifier` | `theloom/extraction/treesitter.py` | intentional — recursive descent for a node's identifier child. |
| `_extract_require_calls` | `theloom/extraction/treesitter.py` | intentional — recursive walk for CommonJS requires. |
| `_hash_at_depth` | `theloom/operations/reification.py` | **suspect by duplication, not by recursion.** The recursion is the Weisfeiler-Leman neighbourhood hash and is correct; the problem is that a byte-identical copy lives in `theloom/reification/fingerprint.py`. |
| `_hash_at_depth` | `theloom/reification/fingerprint.py` | the second copy of the same self-recursive hash — see §6. |
| `_resolve_references` | `theloom/symbolic/core.py` | intentional — recursive expansion of symbolic references. |
| `_js_string` | `theloom/synthesis/prompts.py` | intentional — recursive JavaScript-literal serialization for prompt construction. |

Two things follow. First, none of these is an architectural problem: recursion over
trees and nested documents is the right shape for all ten, and the two recursive
traversals that *do* carry risk are named as tensions rather than cycles (unbounded
recursion depth in `theloom/graph/cycles.py:38-55` and
`theloom/verification/checks.py:157-183`, both of which raise an untyped `RecursionError`
on a deep graph rather than returning a typed error). Second, the complete absence of
multi-node cycles is not evidence of a clean import graph — it is a consequence of the
extraction not recording cross-file edges at all (§5). **A module-level import cycle
would be invisible to this map.**

## 5. Communities versus directories

Community detection over the whole graph again did not return within the analysis budget
(§8). Connected-component detection did, and its result is more informative than any
clustering would have been:

**The graph has exactly 29 connected components, and each one is exactly one module
group. Not a single component spans two directories.**

| Component size | Group | | Component size | Group |
|---|---|---|---|---|
| 516 | `tests` (part 1) | | 101 | `theloom/exploration` |
| 486 | `theloom/operations` | | 97 | `theloom/documents` |
| 320 | `tapestry/src/views/explorer` | | 83 | `tapestry/src/views/overview` |
| 289 | `tests` (part 2) | | 79 | `theloom/extraction` |
| 247 | `tapestry/src/views/chronicle` | | 77 | `theloom/semantic` |
| 228 | `tapestry/src/lib` | | 77 | `theloom/viz` |
| 204 | `theloom/synthesis` | | 75 | `theloom/graph` |
| 148 | `theloom/analysis` | | 74 | `theloom/cli` |
| 146 | `theloom/composites` | | 66 | `theloom/verification` |
| 138 | `tapestry/src/views/systems` | | 62 | `theloom/algebra` |
| 131 | `tapestry/src/views/semantic` | | 57 | `tapestry/src` |
| 131 | `theloom/store` | | 35 | `tapestry/src/state` |
| 130 | `theloom` (root, incl. `symbolic/` and `reification/`) | | 31 | `scripts` |
| 116 | `tapestry/e2e` | | 28 | `tests/fixtures` |
| | | | 22 | `tapestry` |

This is a fact about the *extractor*, not about the code. Call edges survive only when
caller and callee are both symbols of the same file
(`theloom/extraction/treesitter.py:537-550`, `:461-470`), and no import edge appears in
this graph at all: of 2,449 non-containment connections, 999 are function-calls-function
inside one file and the remaining 1,450 are prose findings pointing at the file they
describe. The directory structure and the graph's community structure agree perfectly
because the graph was never given the chance to disagree.

The real seams therefore have to be read out of the prose layer, which does name them.
The couplings that cross a directory boundary and are invisible to the component
analysis:

- **`theloom/verification/capability_spec.py` depends upward on `theloom/operations`,**
  and only function-local imports keep the cycle from closing — hoisting either import
  to module scope creates a circular import at startup
  (`theloom/verification/capability_spec.py:80`, `:95`). This is the closest thing in
  the tree to a genuine module cycle, and the component view cannot see it.
- **`tapestry/src/views/explorer/buildGraph.ts` and `layout.ts` are shared
  infrastructure living inside one view's folder** — imported by the Semantic, Systems,
  Chronicle and Overview views and by the bundle context
  (`tapestry/src/views/explorer/buildGraph.ts:1-12`, `layout.ts:1-13`). Four views
  depend on a directory whose name says they should not.
- **`theloom/graph/cycles.py` reaches the store** from inside a layer whose stated
  contract is store-free (`theloom/graph/cycles.py:20`, `:293-334`).
- **`theloom/composites/framework.py` claims neutral placement but ships inside the
  composites package**, and `theloom/exploration/*` imports it across that boundary
  (`theloom/composites/framework.py:11-13`).
- **`scripts/` bypasses the CLI and the operations layer entirely**, constructing a
  store connection directly — the only in-repo consumers that do
  (`scripts/gen_bench_graph.py:158-165`, `scripts/seed_live_dev.py:20-29`).
- **`tapestry/src/views/overview/` imports from `../explorer/buildGraph`** and re-derives
  the Explorer's facet counts from a different substrate
  (`tapestry/src/views/overview/Overview.tsx:23`, `stats.ts:6-9`).

## 6. Risks and tensions

144 tensions are on record, each with a source citation. The following are the ones a
reviewer should read first, ordered by the recorder's confidence.

1. **Config errors bypass the typed error-code protocol they claim to honour** (0.85) —
   `theloom/config.py:49-52` versus `theloom/errors.py:52-53` and
   `theloom/cli/io.py:73-77`. `CONFIG_ERROR` is effectively unreachable from the config
   loader; a caller branching on the code cannot distinguish a misconfigured client from
   a store failure. Reproducible: `GRAPH_PORT=notanumber loom list-entities …`.
2. **Error classification by prose contradicts the typed-error-code invariant** (0.85) —
   `theloom/operations/documents.py:149-158` branches on substrings of the lowercased
   message; `theloom/operations/analysis.py:487-491` does the same. Rewording an error
   message for clarity can change its exit code.
3. **PageRank scores are published under the wire name `eigenvector`** (0.85) —
   `theloom/composites/graph_reconnaissance.py:5-6`, `:126-132`, duplicated in
   `theloom/composites/entity_deep_dive.py:3-5`. Only a docstring corrects the name; the
   frontend Overview surfaces these as "central entities".
4. **The verification gate makes the store's cross-graph bridge path unreachable** (0.85)
   — `theloom/operations/relations.py:6-11`, `:158-174`. Bridges can be read but not
   written through any supported command; `bridgesCreated` is hard-coded to 0.
5. **Source passages are structurally unreachable: the links lookup is a stub** (0.85) —
   `theloom/synthesis/links.py:13-14`. Every downstream evidence map degenerates to a
   bare entity list, and fidelity checks never see passage text.
6. **Threshold parameters are accepted but inert** (0.83) —
   `theloom/composites/gap_fill_cycle.py:20-23`, `:87-102`, `:224`. The safety knob is
   disabled and the ungated `autoCreate` path is the only one that commits.
7. **Six composites bypass the composite envelope they are named for** (0.82) —
   `theloom/composites/framework.py:26-27` versus the docstrings of
   `analogy_transfer.py`, `propose_entities.py`, `creativity_loop.py` and three others.
   No generic client can consume "a composite result".
8. **Two registered composite commands are structurally inert or unimplemented** (0.80) —
   `theloom/composites/creativity_loop.py:114-141`, `:6-11`. `loom --help` and
   `COMMANDS.md` advertise capability a user cannot obtain.
9. **The stack policy delegates graph algorithms to a library; this module hand-rolls
   most of them** (0.80) — `theloom/graph/analytics.py:19`, `:71-79` is the only library
   call against five hand-written textbook algorithms. The deviation is deliberate
   (enumeration order is treated as output) but is invisible from `STACK.md`.
10. **Two semiring resolvers with deliberately different behaviour** (0.80) —
    `theloom/algebra/core.py:85-92` (strict) versus `:95-105` (permissive). The same
    semiring name yields different edge weights depending on which command path a caller
    took, and a misspelled name is an error on one path and a silent tropical traversal
    on the other.
11. **Fingerprint logic exists twice on purpose, and the newer copy has the fewest
    callers** (0.80) — `theloom/reification/fingerprint.py:5-8`. Two implementations of a
    hash that is supposed to be canonical must be kept bit-identical by hand.
12. **Verbatim snapshot import writes state without writing history** (0.80) —
    `theloom/migrate.py:11-19`, `:50-58`. After an import the store holds records whose
    existence no event explains, and the pre-import history is deleted rather than
    superseded.
13. **The 19 entity types are hand-mirrored in TypeScript with no automated sync** (0.80)
    — `tapestry/src/design/palette.ts:1-29`. Adding a type in Python is a three-file
    change whose omissions fail quietly as a plausible-looking UI.
14. **A failed live bundle fetch has no error path and leaves the app permanently
    loading** (0.80) — `tapestry/src/lib/data.ts:54-55`,
    `tapestry/src/lib/BundleContext.tsx:51-53`, `:66-68`. A server error is
    indistinguishable from a slow load.
15. **Re-ingesting a file duplicates its chunks** (0.80) —
    `theloom/documents/ingestion.py:69-96`. `ingest-document` is additive while
    `reingest-document` diffs; the caller must know which to use.
16. **Document embeddings are best-effort and their absence is never recorded** (0.80) —
    `theloom/documents/ingestion.py:42-50`, `:76`. A transient outage yields a document
    that is listed and counted but invisible to vector search, and reingest will report
    the chunks unchanged and skip them.
17. **SSRF validation and the actual fetch resolve DNS separately** (0.80) —
    `theloom/documents/ssrf.py:8-9`, `:92-95`. The residual time-of-check/time-of-use gap
    is documented; the transport injection point that would close it exists and is used
    only by tests.
18. **Proposal "step 4" claims to filter and filters nothing** (0.80) —
    `theloom/semantic/entity_proposer.py:128-131` versus `:554-576`. Consumers reading
    `filteredCount` as a quality signal are misled.
19. **Blanket exception swallowing inside composite sections erases diagnostics** (0.80) —
    `theloom/composites/graph_reconnaissance.py:152-153`,
    `theloom/composites/entity_deep_dive.py:168-169`. A structurally broken graph and a
    healthy sparse graph render identically.
20. **The serve handler prints and blocks, breaking handler purity** (0.80) —
    `theloom/cli/registry.py:1445-1468`. A failure after the handshake emits a success
    document on stdout *and* an error document on stderr, then exits 1.
21. **Batch relation creation can commit partially and then report a missing endpoint**
    (0.80) — `theloom/store/falkor.py:348-364` against the contract at
    `theloom/store/base.py:77-79`. The per-type loop appends before the count check, so a
    caller that sees `NOT_FOUND` cannot assume nothing was written.
22. **Test error assertions split between typed codes and matched prose** (0.80) —
    `tests/test_ops_relations.py:92`, `:99` assert on message substrings rather than the
    typed code, which is the practice §2.3 exists to forbid.

Recurring themes across the full tension set, each with multiple citations: duplicated
logic that must be kept identical by hand (fingerprints, cosine similarity in four
places inside `theloom/analysis`, guard rules in two shapes in `theloom/verification`,
three CSS-variable readers in the Explorer folder, the injection routine copied into
seven end-to-end specs); parameters accepted and silently ignored (`timeoutMs`,
`purpose`, `generalizationBias`, `commitThreshold`, 3-D memory filters); unbounded
recursion in paths large graphs will reach; and exports that are advertised as WYSIWYG
but drop the overlay channels that carry meaning redundantly.

## 7. Open seams

The automated similar-but-unconnected pass over the whole graph again did not return
within the analysis budget (§8). A targeted pass was run instead and re-verified for this
map: nearest-neighbour lookup in embedding space from one representative file per module
group. Because *no* cross-file edge exists in this graph, every pair below is unconnected
by construction — the interesting signal is which pairs the embeddings place closest
across a directory boundary, since those are the couplings the folder layout does not
express.

| Similarity | Pair |
|---|---|
| 0.742 | `theloom/verification/checks.py` ↔ `theloom/operations/verification.py` |
| 0.736 | `tapestry/src/views/systems/systems.ts` ↔ `tapestry/src/views/explorer/pathMode.ts`, `layout.ts`, `Explorer.tsx` |
| 0.729 | `tapestry/src/views/systems/systems.ts` ↔ `tapestry/src/views/chronicle/Chronicle.tsx` |
| 0.722 | `tapestry/src/App.tsx` ↔ `tapestry/src/lib/BundleContext.tsx` |
| 0.711 | `theloom/algebra/routing.py` ↔ `theloom/operations/algebra.py` |
| 0.706 | `theloom/cli/registry.py` ↔ `theloom/config.py` |
| 0.704 | `theloom/model.py` ↔ `theloom/cli/app.py` |
| 0.703 | `theloom/semantic/embed.py` ↔ `theloom/operations/semantic.py` |
| 0.703 | `theloom/exploration/guards.py` ↔ `theloom/verification/guards.py` |
| 0.703 | `theloom/documents/ingestion.py` ↔ `theloom/operations/documents.py` |
| 0.699 | `tapestry/src/views/semantic/semanticMap.ts` ↔ `tapestry/src/views/explorer/pathMode.ts` |
| 0.692 | `theloom/semantic/embed.py` ↔ `theloom/viz/semantic.py` |
| 0.690 | `theloom/synthesis/cegis.py` ↔ `theloom/operations/synthesis.py` |
| 0.688 | `theloom/extraction/treesitter.py` ↔ `theloom/operations/extraction.py` |
| 0.686 | `theloom/graph/cycles.py` ↔ `theloom/composites/multi_graph_landscape.py` |
| 0.685 | `theloom/store/falkor.py` ↔ `tests/test_falkor_store.py` |
| 0.653 | `scripts/gen_bench_graph.py` ↔ `theloom/graph/*` (analytics, paths, cycles, hydrate) |

Three readings are worth drawing out.

**The library/operations pairs are the intended seam, and they are the tightest pairs in
the tree.** Each domain library has exactly one operations module wrapping it, and
embedding space finds each pair independently. That is a healthy signal: the
one-handler-per-command layering is real.

**Two pairs are seams that should probably be one module.**
`theloom/verification/checks.py` ↔ `theloom/operations/verification.py` is the highest
similarity in the whole sample (0.742) and is also the subject of a recorded tension:
`capability_spec.py` reaches into private, underscore-prefixed functions of
`operations/verification.py` through function-local imports
(`theloom/verification/capability_spec.py:80`, `:95`). Similarly
`theloom/exploration/guards.py` ↔ `theloom/verification/guards.py` (0.703) are two files
with the same name, the same shape, and no relationship — one detects exploration
pathologies, the other gates mutations, and nothing connects or distinguishes them
except their directory.

**The Systems view is semantically nearer to four sibling views than to anything in its
own folder.** It sits within 0.007 of the Explorer's `pathMode.ts`, `layout.ts` and
`Explorer.tsx` and of the Chronicle's shell. This matches the recorded tension that the
Explorer folder holds shared infrastructure four other views import
(`tapestry/src/views/explorer/buildGraph.ts:1-12`, `layout.ts:1-13`). The embeddings say
what the imports already say: `buildGraph.ts` and `layout.ts` belong in a shared
`views/` layer, not inside one view.

## 8. Coverage and methodology

**Coverage.** 29 of 29 module groups are described in full; none is silent. This was an
**incremental** run against the previous map's commit (`8e7eedb`), and **no group needed
re-describing**: the only files that changed between that commit and `8e33b4d` are
`.claude/agents/codebase-enricher.md` and the three map artifacts in
`docs/architecture/`, none of which tree-sitter parses and none of which belongs to a
source module group. Every subsystem description, invariant and tension in this document
therefore still carries the confidence and citations recorded when its group was last
read, and every one of them was re-read from the graph for this map. 80 files could not
be parsed by tree-sitter — every Markdown, JSON, CSS, YAML, TOML, HTML and lockfile in
the repository, three more than last time because this map, its visualization and its
manifest are now committed. Those files appear here only where a prose finding cites them
(notably `tapestry/src/design/tokens.css` and `theloom/viz/static/tapestry.html`, both
load-bearing, both named in tensions about hand-maintained cross-language mirrors). The
working tree was clean at extraction, so this map describes commit `8e33b4d` exactly.

**What the structural layer does and does not contain.** Symbols were extracted with
tree-sitter: 262 files, 1,712 functions and methods, 1,281 variables and constants, 375
classes and types. Containment (`part_of`, 3,770 connections) is complete. Call edges are
recorded only between two symbols of the same file, and no import edge is present in this
graph at all. Consequently: (a) file- and module-level dependency structure is **not** in
the graph, (b) the ten detected cycles are all self-recursion and a module import cycle
would be invisible, and (c) the 29 connected components are directories by construction.
Every cross-module coupling reported in §5 and §7 is asserted by a prose finding with a
file:line citation, not by an edge.

**What the semantic layer contains.** 29 purpose concepts (one per group), 165 patterns,
226 invariant claims and 144 tensions, each carrying a `module_group` tag, a confidence
score, and — for claims and tensions — an `anchor` citing specific files and line ranges.
Every statement in sections 2, 4, 5, 6 and 7 of this document traces to one of those
entities. Confidence on claims and patterns runs 0.80–0.95; on tensions, 0.65–0.85; on
purpose concepts, 0.90–0.92. The store also holds 17 superseded prior versions of
findings revised during enrichment, which is why the raw graph total (4,211) exceeds the
current-state total (4,194).

**Analyses that did not complete.** `find-clusters` and `semantic-gaps` were each given
the full analysis budget at the requested scope of 500 entities and neither returned —
the same outcome as the previous run, on the same graph. Their absence is covered above
by two substitutes: connected-component detection for §5, and per-entity
nearest-neighbour lookup across representative files for §7, spot-re-verified for this
map (`theloom/verification/checks.py` 0.742, `theloom/exploration/guards.py` 0.703,
`tapestry/src/views/systems/systems.ts` 0.736 all reproduced exactly). Both substitutes
are graph operations run against the same graph; neither is an estimate. If you want the
full clustering, run
`loom find-clusters '{"maxEntities": 500, "graph": "codebase-the-loom"}'` with a very
generous timeout and expect it to be slow on this graph.

**Reproducing this map.** Re-run `/map-codebase /Users/jameswinans/Dropbox/Development/the-loom`.
The manifest beside this file records the commit this map describes; the next run reads
that commit as its incremental baseline and re-enriches only the groups whose files
changed.

**Interrogating the graph directly.** The map is a view; the graph is the record.

```bash
# what is this file, what does it contain, what is anchored to it
loom entity-deep-dive '{"entityId": "<uuid>", "graph": "codebase-the-loom"}'

# find the entity id by name (file entities are named "file:<path>")
loom read-entities-by-name '{"names": ["file:theloom/store/falkor.py"], "graph": "codebase-the-loom"}'

# search across symbols and findings at once
loom hybrid-search '{"query": "bi-temporal invalidation", "limit": 10, "graph": "codebase-the-loom"}'

# every recorded tension, worst first
loom list-entities '{"entityType": "tension", "graph": "codebase-the-loom"}'

# what is semantically near a file but unconnected to it
loom semantic-neighbors '{"entityId": "<uuid>", "limit": 10, "graph": "codebase-the-loom"}'
```

The interactive map is `codebase-map.html` beside this file: 400 entities and 549
connections, with the analytics and temporal sections included.
