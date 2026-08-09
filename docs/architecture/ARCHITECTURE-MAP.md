---
repo: the-loom
commit: 11e6e831a003b72e9ac196ddf0387bde35361693
graph: codebase-the-loom
generated: 2026-08-09
mode: incremental
---

# The Loom — Architecture Map

> A prose projection of the `codebase-the-loom` graph. Every statement below traces
> to a record in that graph; the `file:line` citations are the anchors those records
> carry. Nothing here is a second source of truth — when this document and the code
> disagree, re-run the map.

## 1. Executive overview

The Loom is a knowledge-graph substrate with exactly one product surface: a
JSON-in / JSON-out command-line program. You hand `loom` a command name and a JSON
payload; it validates that payload against a declared schema, resolves a named graph
inside a single FalkorDB instance, performs one atomic write (or a read), and prints
one JSON object. Everything else in the repository exists to make that sentence true
at scale: a Pydantic domain model that is the sole authority on shape, a store layer
that pairs every graph mutation with an append to an event log so history is real and
queryable, a semantic layer that turns records into vectors and decides what "similar"
means, a family of pure algorithm kernels (path algebra, graph analytics, structural
fingerprinting, analogy transfer, symbolic math) that never touch persistence, an
orchestration tier that bundles ten operations into one round trip for an agent, a
code and document extraction pipeline that fills the graph from external material, and
a read-only visualization subsystem that ships the same graph three ways — as JSON, as
a self-contained HTML page, and as a local HTTP service the contributor-only React SPA
in `tapestry/` renders live.

| Metric | Value |
|---|---|
| Files in the graph | 428 |
| External packages referenced | 68 |
| Records (total) | 7,300 |
| — structural (files, functions, classes, variables) | 5,667 |
| — written layer (purposes, conventions, invariants, risks) | 1,633 |
| Connections (total) | 16,183 |
| — containment (`part_of`) | 6,589 |
| — calls | 4,879 |
| — imports (`requires`) | 2,052 |
| — cross-references (`related_to`, `references`, `instance_of`) | 2,663 |
| Language mix | Python 302 · TypeScript 74 · Markdown 24 · JSON 14 · CSS 9 · JavaScript 2 · TOML/YAML/lock 3 |
| Files not parsed | 43 |
| Module groups described | 55 of 55 |

> **Working tree was dirty at extraction.** The commit stamped in the front matter is
> `11e6e831`, but uncommitted changes were present when the graph was built (this run's
> `git status` showed a modified `.gitignore` at the repo root, plus locally-uncommitted
> edits to this document set itself). Anchors pointing into tracked, unmodified files are
> exact; if you are reading this from a different working state, treat line numbers as
> approximate rather than authoritative.

**43 files were not parsed.** The extractor covers Python, TypeScript, JavaScript,
Markdown, JSON, CSS and a handful of config formats; anything outside that set
(binary fixtures, images, HTML build artifacts, lockfiles the parser declines) is
recorded as a file record with no symbols inside it, or not at all.

---

## 2. Subsystem walkthrough

Each subsection names the module group it describes, the graph id you can use to
address that group's written layer (`module_group: <id>`), what the code is for, the
files that carry the weight, the conventions it follows, and the promises it makes —
each promise with the `file:line` where it is enforced.

### 2.1 The Python package

#### theloom — the foundation layer (`theloom`)

The six modules every other subsystem imports and which import almost nothing
themselves. `model.py` is the declared single source of truth for every domain shape:
20 record types, 18 connection types, the five-state lifecycle table, confidence and
provenance, and the three Memory Machine axes — and it validates on load. `errors.py`
fixes the six structured error codes as class attributes so an error is never
classified by reading its prose. `config.py` is the one configuration resolution path
(flags beat environment beats `~/.loom/config.json` beats defaults). `timeutil.py`
fixes the single canonical wire timestamp. `migrate.py` imports snapshot folders.
Together they turn four of the six stated architecture invariants from convention into
executable code.

*Key files:* `theloom/model.py` (593 lines), `theloom/config.py` (373 lines),
`theloom/errors.py`, `theloom/timeutil.py`, `theloom/migrate.py`.

*Conventions:* enums are the wire contract and runtime inventories derive from them;
one alias-carrying base model owns the snake/camel boundary; the error code is a class
attribute on a typed exception hierarchy; the config loader is layered precedence with
per-key type guards; create-input mirror models keep caller input separate from stored
shape; legacy tolerance arrives as read-time coercion, never as a second shape.

*Promises:*
- Volatile durability requires an expiry, enforced on both the input and the stored shape — `theloom/model.py:452-457`, `:530-534`.
- Every wire timestamp is ISO 8601 UTC, millisecond precision, `Z` suffix — `theloom/model.py:38`, `:41-49`; produced by `theloom/timeutil.py:12-15`.
- `retracted` is terminal, and a same-status transition is a valid no-op — `theloom/model.py:333-357`, `:360-372`.
- An unset status means active; absence is never a distinct state — `theloom/model.py:459-462`.
- Unknown wire fields are rejected, never silently dropped — `theloom/model.py:380-383`.
- Confidence is bounded to 0..1 at the field and at the label function — `theloom/model.py:389`, `:313-325`.
- Every record carries authorship; the server supplies a default session rather than leaving it absent — `theloom/config.py:66-72`, consumed at `theloom/operations/entity.py:259`.
- Causal connections carry polarity; structural and epistemic ones do not — `theloom/model.py:279-306`.

#### theloom/store — the persistence core, part 1/2 (`theloom-store-1`)

Where the architecture doctrine becomes mechanism. `base.py` declares the abstract
store surface. `falkor.py` implements it over one FalkorDB graph per named Loom graph,
storing each record as its verbatim wire JSON plus derived index properties.
`commit.py` and `events.py` are the single write primitive every mutation in the
codebase passes through: one graph query and one event append inside one Redis
transaction, with explicit compensation in whichever direction a half-failure runs.
`filters.py` holds filter semantics as pure functions so the real store and the
in-memory adapter answer identically. `paging.py` defends every full-scan read from
server-side result truncation. `multigraph.py` is the facade that resolves a graph name
(and a belief world) into a store. `bridges.py` holds cross-graph connections, which
belong to no single graph, as their own bi-temporal records.

*Key files:* `theloom/store/falkor.py` (1,407 lines), `commit.py`, `events.py`,
`multigraph.py`, `base.py`.

*Promises:*
- Every mutation commits its graph write and its event append as one unit — `theloom/store/commit.py:108-122`, `:10-20`.
- Deletion invalidates; `hard=True` is the only path that destroys history — `theloom/store/falkor.py:540-601`, `:1269-1290`.
- An update snapshots the prior incarnation before swapping the document, in one statement — `theloom/store/falkor.py:521-538`, `:496-519`.
- A batch of connections is all of them or none of them — `theloom/store/falkor.py:860-913`.
- Server-side `LIMIT` and `count` run only when the query prefilter alone decides membership — `theloom/store/falkor.py:133-152`, `:753-791`.
- Full-scan reads must page, or the server silently returns a wrong answer — `theloom/store/paging.py:3-11`, `:24-45`.
- `MultiGraph.get_store` is the single resolution path for both graph and world — `theloom/store/multigraph.py:200-229`.
- Every graph a write lands on is registered, which is what makes a session a real boundary — `theloom/store/commit.py:86`, `:120-121`; `multigraph.py:267-330`.
- A bridge is unique on (from, to, type) among live records, and removal invalidates — `theloom/store/bridges.py:77-81`, `:139-196`.

#### theloom/store — the machinery around the adapter, part 2/2 (`theloom-store-2`)

`space.py` is the shared base every store inherits: one named graph, its connection,
one append-only stream, the commit primitive, truncation-immune paging, and the
vector/range index lifecycle. `read_port.py` states the narrow typed read half as a
structural `Protocol`, so readers are typed by what they need rather than by a concrete
class. `receipts.py` and `worldctx.py` are two matching per-dispatch side channels: one
collects the event ids a mutation earned and surfaces them as `eventIds`, the other
carries the ambient belief-world id. `refs.py` is a generic TTL-bearing registry;
`worlds.py` builds branchable belief worlds on top of it — a world is a ref plus a
reserved graph segment, read as an overlay over a frozen historical view of its
ancestors and written copy-on-write.

*Promises:*
- A mutation is one statement plus its event append, committed as one unit — `theloom/store/space.py:73-96`.
- Reserved graphs (leading underscore) are never auto-registered — `theloom/store/space.py:62-64`.
- Forking a world writes no record data; fork is constant-time in graph size — `theloom/store/worlds.py:771-817`.
- A vector index width comes from stored vectors, never from a query vector — `theloom/store/space.py:244-258`.
- Shallowest layer with an opinion wins; a fork never writes into its parent — `theloom/store/worlds.py:326-346`, `:37-39`.
- Every ref write is one hash-set plus one event append in a single transaction — `theloom/store/refs.py:135-148`.
- Receipts are additive: a read-only response is byte-identical to pre-receipts output — `theloom/store/receipts.py:110-130`.
- Reaping keeps a ref listable as history; only purge erases it — `theloom/store/refs.py:220-260`.
- Read ordering is part of the read-port contract, not an implementation detail — `theloom/store/read_port.py:50-57`, `:140-161`.

#### theloom/cli — the outer boundary (`theloom-cli`)

The CLI package turns one declarative registry into a Typer application, enforces the
JSON protocol, and derives every piece of self-documentation from that same registry so
the published contract cannot drift from the implementation. `registry.py` declares 180
commands across 28 categories as frozen specification rows and exposes `run_handler` as
the single dispatch seam where validation, belief-world scoping and write-receipt
collection are applied uniformly. `app.py` generates one subcommand per descriptor plus
the two hand-written ones (`version`, `init`). `io.py` owns the wire protocol.
`schema.py` is the one place that walks a model's JSON Schema, feeding both the catalog
generator and validation-error enrichment.

*Promises:*
- Every registry command is generated; the registry is the only CLI surface — `theloom/cli/app.py:139-140`, `theloom/cli/registry.py:1851-1878`.
- No command returns a bare top-level array — `theloom/cli/registry.py:1913-1917`.
- The `world` parameter is wired once, for all commands, at `run_handler` — `theloom/cli/registry.py:1910-1912`.
- Every store event appended during a dispatch is returned as `eventIds` — `theloom/cli/registry.py:1902`, `:1911`, `:1918`.
- Success goes to stdout; errors go to stderr as one-line `{error, code}` with exit 1 — `theloom/cli/io.py:72-92`, `theloom/cli/app.py:120-133`.
- Non-finite floats serialize as `null` — `theloom/cli/io.py:56-64`.
- Discovery paths never touch the store — `theloom/cli/app.py:117-119`, `:83-88`, `:31-38`.
- Stdin input is capped at 100 MB — `theloom/cli/io.py:17`, `:41-53`.

#### theloom/operations — traversal, analytics, routing, bulk, calibration, consumption, part 1/4 (`theloom-operations-1`)

Each module owns a family of commands and does the same three things: declare an input
model deriving from `common.CommandInput`, pull a store from the facade, and shape a
plain dict back out. Domain algorithms are not implemented here — they are delegated.
`common.py` is the layer's foundation (`CommandInput` is the base class of essentially
every command input in the repo, and `resolve_entity_ref` is the single name-first
addressing path). `consumption.py` plus its two extracted algorithm modules are the
agent-facing surface: `explore`, `find-callers`, `find-callees`, `blast-radius`.
`calibration.py` closes the confidence loop by grading resolved claims at the confidence
they were asserted with.

*Promises:*
- Exactly one of `id` or `name`; a blank name is a missing argument, never a wildcard — `theloom/operations/common.py:167-172`.
- Ambiguous names are refused with candidates, never guessed — `theloom/operations/common.py:193-199`.
- Neighbourhood reads are current-state reads — `theloom/operations/consumption.py:254-269`, `blast_radius_traversal.py:131`, `:152`.
- A truncated response accounts for every row: shown + cut == total — `theloom/operations/consumption_budget.py:111-132`.
- Every populated `explore` section keeps its first row regardless of budget — `theloom/operations/consumption_budget.py:64-68`.
- Calibration scores assertion-time confidence; an unreadable history excludes the claim — `theloom/operations/calibration.py:365-372`.
- `resolve-claim` is all-or-nothing via compensating deletes — `theloom/operations/calibration.py:239-272`.
- `bulk-import` is idempotent on name+type and cannot smuggle polarity onto a structural edge — `theloom/operations/bulk.py:221-237`, `:208-217`.

#### theloom/operations — lifecycle, epistemics, documents, inference, extraction, part 2/4 (`theloom-operations-2`)

The handlers for the record lifecycle, epistemic queries, document ingestion,
deterministic inference, codebase and document extraction, merge, init and cross-graph
queries. Each adds operation-level semantics the store deliberately does not know
about — revision bookkeeping, verification-gate warnings, confidence auto-dating,
status-transition legality, credit-propagation arithmetic, forward-chaining rule
evaluation — and returns a wire-ready dict or a `{items, count, notices}` envelope.

*Promises:*
- `update-entity` always increments version and sets `previousVersionId` to the record's own id — `theloom/operations/entity.py:344-345`.
- Status transitions are checked for legality before any write — `theloom/operations/entity.py:322-325`, `merge.py:149-153`.
- `merge-entities` never hard-deletes the secondary and is a no-op when already merged — `theloom/operations/merge.py:188-197`, `:174-177`.
- Document commands accept `graph` but never apply it, and always say so — `theloom/operations/documents.py:73-90`.
- A dry-run inference run mutates nothing at all, not even the record that it happened — `theloom/operations/inference.py:497-502`.
- Derived-connection polarity comes only from the model's causal defaults, never from a rule's conclusion — `theloom/operations/inference.py:526`.
- `propagate-credit` persists by default, and `applied` reports real writes rather than intent — `theloom/operations/epistemic.py:906`, `:1042-1072`.
- Re-extracting a codebase retires the legacy call edges the new ones replace — `theloom/operations/extraction.py:117-156`.
- `extraction-rollback` keys deletion by connection type or it erases an older run's edge — `theloom/operations/extraction.py:303-317`.

#### theloom/operations — response contract, relations, semantic, receipts, adapters, part 3/4 (`theloom-operations-3`)

The thin handler modules between input parsing and the deeper mechanism packages.
`notices.py` is the single source of truth for every notice code the build can emit;
`relations.py` owns the connection write surface with its verification gate and polarity
partition; `semantic.py` owns retrieval and the embedding lifecycle; `receipts.py` and
`reification.py` own event replay and structural fingerprint reads; `solve.py`,
`symbolic.py`, `prompt_loader.py`, `portability.py` and `sessions.py` are adapters.

*Promises:*
- A notice code cannot ship without a cataloged meaning — `theloom/operations/notices.py:197-202`, `:73-176`.
- Envelope keys are additive; `count` is always the rows returned — `theloom/operations/notices.py:209-226`.
- The polarity partition is an invariant of the stored edge, not just of creation — `theloom/operations/relations.py:383-413`.
- `embedding-reconcile` refuses to write against a non-main belief world — `theloom/operations/semantic.py:556-575`.
- `reify-patterns` is idempotent via the fingerprint marker — `theloom/operations/reification.py:113-121`, `:165-201`.
- `export-graph` refuses a >200 MB write unless forced — `theloom/operations/portability.py:100-104`.
- Reaping an already-reaped session reports `applied: false`, never a phantom delete — `theloom/operations/sessions.py:76-89`.

#### theloom/operations — synthesis, verification, worlds, work memory, part 4/4 (`theloom-operations-4`)

The four heaviest handler modules, each owning a whole capability surface:
`synthesis.py` wires the nine Plan-Traverse-Realize commands over one graph or a merged
read-only view of several; `verification.py` implements the guards, the five default
invariants, the property DSL, capability metrics, constraint propagation and
deterministic constrained generation; `worlds.py` exposes branchable belief worlds as
event-log replay; `work_memory.py` records the experiential layer.

*Promises:*
- A usage record never exists without its citations — `theloom/operations/work_memory.py:162-169`.
- A repeated citation id counts exactly once — `theloom/operations/work_memory.py:103`.
- `verify-fidelity` refuses rather than grading unscoped text against the whole graph — `theloom/operations/synthesis.py:620-652`.
- Cross-graph synthesis is read-only by construction — `theloom/operations/synthesis.py:366-372`.
- `verify-graph` never runs the second tier of invariants on a first-tier-inconsistent graph — `theloom/operations/verification.py:201-212`.
- `validate-mutation-trace` never mutates the graph it validates — `theloom/operations/verification.py:627-654`.
- `merge-world` applies, contests, or provably no-ops every candidate change — `theloom/operations/worlds.py:574-751`.

#### theloom/graph — the pure graph-algorithm kernel (`theloom-graph`)

Hydrates the store's wire documents into a small in-memory directed multigraph and runs
every structural computation the rest of the system needs over it: centrality and
components, cycle detection and feedback-loop classification, shortest and bounded
all-simple paths, motif mining, subgraph extraction, and the parsers that read loop and
leverage-point metadata back out of observation strings. With one deliberate exception
the layer is side-effect free.

*Promises:*
- `hydrate_graph` is the only safe constructor; dangling connections are dropped — `theloom/graph/hydrate.py:111-119`.
- Neighbour iteration is deduplicated and IN-before-OUT ordered — `theloom/graph/hydrate.py:73-96`.
- Feedback-loop detection sees only causal connections and their endpoints — `theloom/graph/cycles.py:23`, `:219-240`.
- Loop polarity is the parity of negative edges; absent polarity counts as positive — `theloom/graph/cycles.py:243-267`.
- Each motif instance is enumerated once, from its smallest member — `theloom/graph/motifs.py:86`, `:92-93`.
- PageRank never returns non-converged scores — `theloom/graph/analytics.py:66-68`.

#### theloom/algebra — the semiring path engine (`theloom-algebra`)

Defines the five weight algebras the graph can be traversed under (boolean
reachability, tropical shortest-cost, viterbi best-probability, counting, capacity
bottleneck), the extractors that turn an ordinal strength label into a numeric weight,
and a single depth-bounded traversal engine parameterized by semiring, extractor, mode
and direction. On top sits a type-category system: every connection type is classified
structural / epistemic / causal, each category has a canonical semiring, and
cross-category paths are joined by an explicit six-entry morphism table.

*Promises:*
- Adjacency emission order is part of the output contract — `theloom/algebra/core.py:3-7`, `:141-148`.
- Value accumulates via `plus`; the stored path is replaced only on a strict single-path win — `theloom/algebra/core.py:192-210`.
- Traversal is depth-bounded, default 10 — `theloom/algebra/core.py:25`, `:171-172`.
- The morphism table is total over ordered cross-category pairs — `theloom/algebra/routing.py:71-104`.
- An unknown source id yields an empty result, never an error — `theloom/algebra/core.py:234-235`, `:265-266`.

#### theloom/analysis — analogy and novelty scoring (`theloom-analysis`)

Given plain lists of records and connections, this layer answers four questions with no
store access and no I/O: which records in one domain correspond to records in another;
what new records the mapping implies (copy-with-substitution-and-generation, with
concept slippage as an optional creative step); how much to believe the result; and
where in the graph to look for far analogies at all.

*Promises:*
- The package never touches the store or performs I/O — `theloom/analysis/absence_surprise.py:27-28`, `component_signatures.py:30-33`.
- The systematicity filter transfers only connections touching the matched relational structure — `theloom/analysis/cwsg.py:81-85`, `:370-393`.
- The `__NOVEL__` prefix namespaces unmapped endpoints and is stripped exactly once — `theloom/analysis/cwsg.py:31`, `:143-145`.
- Component signatures are comparable only under a shared global hash ordering — `theloom/analysis/component_signatures.py:113-142`.
- The fingerprint primitive is shared with reification, not re-implemented — `theloom/analysis/component_signatures.py:32`.
- Cross-domain mapping is hard-capped at 100 records per domain — `theloom/analysis/crossdomain.py:16`, `:167-174`.

#### theloom/semantic — embedding and retrieval (`theloom-semantic`)

The one place that turns records into vectors, decides what "similar" means, and
decides when a vector is stale. It owns the embedder contract (fastembed
nomic-embed-text-v1.5, 768 dimensions, document/query prefixes, content hash,
L2-normalized output); the single vector-search core every semantic read funnels
through; the embedding state machine; and pure ranking stages. Alongside sit two
consumers of the same geometry: `landscape.py`, which measures the embedder's own
similarity bands live against a probe corpus and derives thresholds from what it just
observed rather than from constants, and `deduplication_gate.py`.

*Promises:*
- One similarity scale: cosine is converted in exactly one function — `theloom/semantic/search.py:58-65`.
- Vectors are L2-normalized at embed time, which validates the identity — `theloom/semantic/embed.py:84-88`.
- Vector search returns only active records by default; superseded records keep their vectors — `theloom/semantic/search.py:136`, `:102-109`.
- Re-embedding is decided solely by content-hash equality on a completed embed — `theloom/semantic/embedding_state.py:49-61`.
- Reconcile plans without a store; only one function writes — `theloom/semantic/embedding_state.py:129-156`.
- The observation anchor structurally cannot contain the record's own name — `theloom/semantic/landscape.py:471-487`.
- Approximate-nearest-neighbour ordering is untrusted; only a short window proves exhaustion — `theloom/semantic/search.py:126-156`.
- Incomparable vectors score 0.0 instead of raising — `theloom/semantic/embed.py:121-135`.

#### theloom/synthesis — Plan-Traverse-Realize and CEGIS, part 1/2 (`theloom-synthesis-1`)

Given a natural-language query and a store, this half selects a relevant subgraph,
decomposes the query into ordered sub-questions, groups and ranks regions by k-core
structure, assembles a plan, linearizes each region into a causally topological reading
order, and realizes it as text in one of six formats — deterministically by template, or
via a language model whose failure always degrades back to the template. Alongside sits
the verification-facing half: fidelity scoring and counterexample-guided synthesis of
new type-valid structure.

*Promises:*
- A language-model failure degrades synthesis output, never fails the command — `theloom/synthesis/realizer.py:311-317`.
- Every fidelity grounding decision carries a full audit trail, including negatives — `theloom/synthesis/fidelity.py:512-531`, `:609-622`.
- Semantic grounding cutoffs are live-calibrated per record, never constants — `theloom/synthesis/fidelity.py:284-302`, `:372-412`.
- CEGIS verifies in memory; the store is resolved only to commit — `theloom/synthesis/cegis.py:436-448`.
- Generation is reproducible from an explicit seed — `theloom/synthesis/generator.py:41-60`, `cegis.py:49-59`.
- Sub-question dependencies are always acyclic — `theloom/synthesis/decomposer.py:41-58`, `:138-141`.
- Anchor records are never dropped by the centrality cap — `theloom/synthesis/selector.py:142-150`.

#### theloom/synthesis — the traversal half, part 2/2 (`theloom-synthesis-2`)

`traverser.py` walks the planned subgraph region by region and turns it into the
evidence the realizer writes from: each visited record becomes an evidence unit with its
document, the in-region connections touching it, a multiplicative path confidence, its
verbatim source passages, and its region id. In parallel it records a timestamped
provenance trail of every visit, traversal and skip, so a narrative can be audited step
by step back to the walk that produced it.

*Promises:*
- Traversal can never address a record or connection outside the plan — `theloom/synthesis/traverser.py:224-227`, `:158`.
- Evidence units are unique by record id; duplicates merge rather than drop — `theloom/synthesis/traverser.py:193-213`.
- No provenance step can be appended after finalize — `theloom/synthesis/traverser.py:54-55`, `:77-86`.
- A missing record is skipped and recorded, never fatal — `theloom/synthesis/traverser.py:112-114`, `:161-164`.

#### theloom/verification — the rule engine (`theloom-verification`)

Pure, store-light functions that decide whether a graph, or a single pending mutation,
satisfies the Loom's structural promises. Four jobs behind one package: mutation gates
that run inside create-entity / create-relation, read-side guards and the five built-in
invariants, capability checks that treat "what the graph should be able to do" as
verifiable structure, and constraint propagation over typed connection constraints.
Nothing here mutates; every function returns violation documents.

*Promises:*
- Coverage and coupling violations come from one generator shared by the DSL and the command — `theloom/verification/metrics.py:22-84`.
- Polarity belongs to causal connection types only, worded once — `theloom/verification/checks.py:24-28`, `:93-130`.
- Retracted records read back but cannot become connection endpoints — `theloom/verification/guards.py:81-107`.
- Record gates warn in-band; connection gates block — `theloom/verification/guards.py:41-78`.
- Declared feedback loops are exempt from the causal-cycle invariant — `theloom/verification/checks.py:256-269`.

#### theloom/composites — one-call orchestration, part 1/2 (`theloom-composites-1`)

Each module bundles several already-built primitives into a single command and returns a
structured envelope with per-section timing and error metadata, so an agent gets a whole
workflow for one round trip instead of ten. The layer's discipline is delegation: no
composite reimplements an algorithm. It is also deliberately deterministic — where a
documented contract asked for a model call, the composite declines it explicitly and
reports the declination rather than faking a score.

*Promises:*
- The section runner never raises; a failing section becomes `data: null` plus an error string — `theloom/composites/framework.py:53-69`.
- `consolidate` writes only into its dream world; main is never touched — `theloom/composites/consolidate.py:832`, `:820-826`.
- Dream findings are capped at confidence 0.35 with an inference or speculation basis — `theloom/composites/consolidate.py:82`, `:158-159`.
- `belief-blast-radius` always purges its fork and always reports `applied: false` — `theloom/composites/belief_blast_radius.py:101-106`.
- The enrichment crawl never infers a causal connection type — `theloom/composites/enrichment_crawl.py:84`, `:152-161`.
- The crawl is dry-run unless explicitly disabled, and its count reflects real writes — `theloom/composites/enrichment_crawl.py:199`, `:391-418`.
- Creativity-loop termination is earned, not a fixed cycle count — `theloom/composites/creativity_loop.py:79-81`, `:375-381`.

#### theloom/composites — surveys, reasoning cycles, memory, part 2/2 (`theloom-composites-2`)

Thirteen more one-call commands in three families: read-only surveys that bundle
existing operations and report per-section timing (structural reconnaissance, semantic
landscape, per-entity depth, ecosystem survey, verified extract); reasoning cycles that
generate and evaluate candidate graph content (hypothesis engine, what-if simulation,
self-improvement); and the memory/session surface (reflection over recorded outcomes,
and the waking surface that summarizes what happened since last session).

*Promises:*
- `simulate-change` never mutates the graph it simulates against — `theloom/composites/simulate_change.py:246-249`, `:100-110`.
- A composite always returns an envelope; a failed section is data, not an exit code — `theloom/composites/framework.py:42-56`, `:106-108`.
- `self-improve` writes nothing unless auto-apply is explicitly true — `theloom/composites/self_improve.py:73`, `:340-348`.
- An applied proposal is all-or-none — `theloom/composites/self_improve.py:439-466`.
- "Could not evaluate" never outranks "evaluated badly" — `theloom/composites/self_improve.py:285-336`.
- A reflection verdict never outlives the evidence for it — `theloom/composites/reflect.py:276`, `:352-365`.
- `since-last-session` fits a context window or says it was trimmed — `theloom/composites/since_last_session.py:147-186`.

#### theloom/extraction — turning external material into records (`theloom-extraction`)

Two independent extractors that share nothing but a run log: a deterministic,
model-free codebase extractor (tree-sitter parse per file, then a whole-project join
that resolves imports, cross-file calls, base classes and documentation mentions into
edges), and a language-model document pipeline. Around the codebase extractor sit its
lifecycle pieces: one encoding module that both writes and parses every graph string the
extractor emits, a git-diff-driven incremental updater that supersedes rather than
deletes, a run store that makes rollback possible across invocations, and a self-model
updater keyed on a stored HEAD marker.

*Promises:*
- An ambiguous symbol name resolves to no edge at all — `theloom/extraction/resolution.py:449-451`.
- Language builtins are never call targets — `theloom/extraction/resolution.py:432-435`, `:70-141`.
- Line numbers are 0-based in code and 1-based in stored strings — `theloom/extraction/encoding.py:17-23`.
- Structural extraction never emits a generic `related_to`, and the update never retracts one — `theloom/extraction/treesitter.py:16-17`, `codebasediff.py:78-80`.
- The incremental update supersedes and invalidates; it never deletes — `theloom/extraction/codebasediff.py:473-486`.
- A visibly collapsing update is refused before anything is written — `theloom/extraction/codebasediff.py:356-371`, `:533-539`.
- Git decides codebase membership; non-code must additionally be tracked — `theloom/extraction/treesitter.py:1056-1067`, `:1116-1119`.
- Extraction output is deterministic: the walk is sorted — `theloom/extraction/treesitter.py:1203-1211`.
- Document chunk text is fenced as data, not instructions — `theloom/extraction/pipeline.py:80`, `:166`.

#### theloom/documents — the document side (`theloom-documents`)

Turns files, directories, raw content and URLs into embedded, searchable chunk rows
inside the same store that holds the knowledge graph. A fixed pipeline runs parse →
chunk → embed → upsert. Parsers normalize pdf/docx/markdown/html/txt/json into one block
shape; the chunker groups blocks by strategy, splits oversized groups on sentence
boundaries and adds overlap; embedding is best-effort; the chunk store persists each
chunk as a node with metadata and an optional vector, event-sourced like every other
mutation. Documents are deliberately global rather than graph-scoped, and URL ingestion
is hardened against server-side request forgery before any byte is fetched.

*Promises:*
- Reingest diffs by content hash at the same chunk index; unchanged chunks are neither re-embedded nor rewritten — `theloom/documents/ingestion.py:338-355`.
- An updated chunk keeps the id it was stored under — `theloom/documents/ingestion.py:342-345`.
- A delete removes exactly the chunks its event names — `theloom/documents/chunkstore.py:184-216`.
- Chunks live outside every knowledge graph, in a per-prefix chunk graph — `theloom/documents/chunkstore.py:56`, `:69-78`.
- Every fetch hop is validated and a single non-global resolved address rejects the URL — `theloom/documents/ssrf.py:58-80`, `:89-103`.
- Chunks are stored even when embedding fails, with the failure reason — `theloom/documents/ingestion.py:60-69`, `:143-146`.
- Code and list blocks are atomic: the chunker never splits them — `theloom/documents/chunker.py:76-84`, `:159-167`.

#### theloom/exploration — foraging signals (`theloom-exploration`)

The foundation beneath the explore-frontier composite. It answers "which region of the
graph is most worth visiting next" by computing four independent normalized signals over
connected components — age staleness, bridging potential, coverage gap, and an
exploration bonus over visit counts — then blending them with a renormalizing weighted
score. On top sit two behavioral policies: a patch-leaving policy that advises when to
abandon a region whose marginal gain has fallen below the cross-region average, and six
guards that flag pathological exploration behavior.

*Promises:*
- Every signal returns a section result and never raises — `theloom/exploration/age_staleness.py:111` and siblings.
- All scores are normalized to [0,1] — `theloom/exploration/composite_signals.py:53`, `:89`.
- Region identity is the smallest record id — `theloom/exploration/exploration_state.py:89-97`.
- Exploration state is in-memory only and always starts zeroed — `theloom/exploration/exploration_state.py:6-22`, `:100-109`.
- A cold-start region is never told to leave — `theloom/exploration/mvt_patch_leaving.py:82-121`.
- Gain histories are bounded at 100 entries with oldest-first eviction — `theloom/exploration/exploration_state.py:31`, `:128-144`.

#### theloom/reification — structural fingerprinting (`theloom-reification`)

The single, store-free implementation of Weisfeiler-Leman ego fingerprinting. Given a
hydrated graph it assigns each node a hash of its rooted neighborhood up to a depth, so
nodes occupying the same structural position collapse onto the same digest. It is its
own package because two independent subsystems need bit-identical hashes — pattern
crystallization and component signatures — and extracting the hashing here turned that
agreement from a convention two files had to maintain into a property of the import
graph.

*Promises:*
- Fingerprints are independent of insertion order — `theloom/reification/fingerprint.py:50-52`, `:75-78`.
- Identity is the first 16 hex characters of SHA-256, produced at one chokepoint — `theloom/reification/fingerprint.py:26-27`, `:80`.
- Depth is clamped at both public entry points — `theloom/reification/fingerprint.py:93`, `:133`.
- Grouping output is deterministic, filtered and bounded — `theloom/reification/fingerprint.py:159-162`.

#### theloom/symbolic — the computer-algebra kernel (`theloom-symbolic`)

Wraps sympy behind one uniform entry point that dispatches a string operation name to
one of 21 handlers covering algebra, calculus, discrete math, linear algebra, number
theory, combinatorics, differential equations, geometry, and a multi-step chain that
pipes results between steps. It touches no graph, no store and no config: parameters in,
a plain result dict out, with every failure converted into an envelope rather than an
exception. This is the concrete instance of the prefer-libraries invariant for symbolic
math.

*Promises:*
- The entry point never raises; every failure is a `success: false` envelope — `theloom/symbolic/core.py:1014-1022`, `:1001-1006`.
- The alarm and prior signal handler are always restored — `theloom/symbolic/core.py:1009`, `:1023-1025`.
- Operation timeout is clamped to 1–120 seconds — `theloom/symbolic/core.py:1008`.
- A chain fails fast and returns the partial step results — `theloom/symbolic/core.py:940-945`.

#### theloom/viz — the visualization subsystem (`theloom-viz`)

Turns a live graph into the versioned bundle the SPA renders, and ships that document
three ways from one assembler: as JSON, as a self-contained static HTML file with the
bundle injected into a committed single-file build, and as a read-only HTTP service.
Everything here is read-only projection. The pipeline is scope → sections → schema →
emission; heavy lifting is delegated, never reimplemented.

*Promises:*
- Injected bundle content can never terminate the script block — `theloom/viz/html.py:56`.
- Degree truncation is deterministic and always disclosed — `theloom/viz/bundle.py:94`, `:100-104`.
- A historical bound applies to records, connections and events only; other sections stamp themselves current — `theloom/viz/bundle.py:132-163`.
- The bundle ships every record status, not just active ones — `theloom/viz/scope.py:43`, `:68`.
- Live mode is read-only and never binds a port in tests — `theloom/viz/serve.py:108`–`:220`, `:47-48`.
- The wire contract is versioned Python and its JSON Schema is committed for the frontend — `theloom/viz/schema.py:13`, `:92-104`.
- A bounded historical read is one store call, not an N+1 reconstruction — `theloom/viz/scope.py:55-73`.
- Scope mode is validated against a closed set before any store work — `theloom/viz/scope.py:22`, `:79-82`.

### 2.2 The Tapestry frontend

#### tapestry — frozen inputs, part 1/2 (`tapestry-1`)

The determinism boundary around the SPA: the golden bundle fixture that stands in for a
live server during development and browser tests, the exact dependency pin set that
makes the build reproducible, and the single browser test that runs against a real
server process. Everything else in `tapestry/` is tested against the fixture rather than
a database.

*Promises:*
- The fixture must be a bundle the Python assembler could emit, byte-for-byte — `tapestry/fixtures/dev-bundle.json:1-14`, enforced at `tests/test_viz_schema_drift.py:29-49`.
- Every id the fixture's derived sections cite resolves inside the same bundle — `tapestry/fixtures/dev-bundle.json:3-14` against `:15-290`.
- Live-only chrome must not appear in the static single-file build — `tapestry/e2e-live/live.spec.ts:14-23`.
- Every package in the frontend build is pinned to an exact version with an integrity hash — `tapestry/package-lock.json:4`.

#### tapestry — build, types, harness, wire contract, part 2/2 (`tapestry-2`)

Everything that turns `tapestry/src` into the single self-contained HTML template the
Python package ships: a four-stage pipeline (type-check, build, single-file inlining,
emit-template copy into the Python tree), the vitest configuration, two browser-test
configurations splitting static from live, solution-style TypeScript projects, and the
generated-from-Pydantic JSON Schema both sides agree on.

*Promises:*
- The build aborts if the built template loses the injection sentinel — `tapestry/scripts/emit-template.mjs:4-7`.
- The committed template must be byte-identical to what the source build emits — `tapestry/package.json:8`, enforced at `.github/workflows/ci.yml:49`.
- The bundle wire shape is closed everywhere except record and connection payloads — `tapestry/schema/bundle.schema.json:319`, `:330`, `:338`.
- Type-checking gates the build, and both projects are strict — `tapestry/package.json:8`, `tsconfig.app.json:18-22`.

#### tapestry/e2e — the browser suite (`tapestry-e2e`)

The only layer that tests the artifact users actually receive. Instead of driving a dev
server, every spec reconstructs the shipped page in its setup — reading the committed
template and the committed fixture, performing the same sentinel substitution the Python
renderer performs, and opening the result over `file://`. Seven specs partition the
surface by concern: smoke across all five view tabs, accessibility across every panel in
both themes, drag semantics, export filenames, the focus-trapped shortcuts dialog,
keyboard operability, and saved views with deep links.

*Promises:*
- Every spec exercises the shipped artifact, never the dev server — `tapestry/e2e/smoke.spec.ts:6-22` and six siblings.
- The suite's escaping must stay byte-identical to the Python renderer's — `tapestry/e2e/smoke.spec.ts:20` against `theloom/viz/html.py:56`.
- Each spec writes its own uniquely named temp page so parallel specs never clobber — `tapestry/e2e/smoke.spec.ts:15` and siblings.
- The accessibility gate is zero serious/critical violations across both themes — `tapestry/e2e/a11y.spec.ts:42-45`, `:87`.
- A completed drag never registers as a selecting click — `tapestry/e2e/drag.spec.ts:122-133`.
- The help dialog is focus-trapped and restores focus to its trigger — `tapestry/e2e/help.spec.ts:39-62`.

#### tapestry/src — the application shell (`tapestry-src`)

Owns everything around the five graph views rather than any view itself: the root mount
that makes a loaded bundle a precondition for every descendant, the header (brand,
counts, the optional historical note, the live indicator with graph switcher, tablist,
help trigger, theme control), the entity-colour ribbon, one polite live region, and the
pane that switches views. It also wires the cross-cutting behaviour no view can own
alone — URL-hash deep linking, saved-view resolution, theme resolution and OS-preference
tracking, roving focus, and the global shortcut.

*Promises:*
- The live region announces bundle reloads but never the first load — `tapestry/src/App.tsx:189-195`.
- Live-only header controls render only when the server injected the live marker — `tapestry/src/App.tsx:287-345`.
- The closed help overlay renders nothing at all — `tapestry/src/views/HelpOverlay.tsx:74`.
- Hash writes use `replaceState`, so deep links never accumulate history entries — `tapestry/src/App.tsx:252-254`.
- Every component below the root renders only with a loaded bundle — `tapestry/src/main.tsx:6-10`.

#### tapestry/src/design — the visual contract (`tapestry-src-design`)

Three files hold every colour, type scale, spacing step, radius and motion duration the
frontend may use, plus the two runtime helpers that select between light and dark and
that map a record type onto its categorical colour slot. Colour is handed out as a token
*reference*, never a resolved value, so entity colour follows the theme automatically.
The module also carries the accessibility policy in prose: colour is never the sole
encoding, and each colour channel is reserved to one meaning.

*Promises:*
- The colour helper is total: every input yields a defined colour — `tapestry/src/design/palette.ts:35-39`.
- The entity-type tuple fixes the model enum order as the UI ordering authority — `tapestry/src/design/palette.ts:7-27`.
- Theme resolution is browser-safe and degrades to light headlessly — `tapestry/src/design/theme.ts:6-18`.
- One attribute is the single switch every token block keys off — `tapestry/src/design/theme.ts:24-26`, `tokens.css:163`.

#### tapestry/src/lib — the shared substrate (`tapestry-src-lib`)

Everything the four canvas views and the shell need but none of them owns: getting a
bundle into the browser and sharing it (three-branch loader — live API, inline injected
JSON, dev fixture — plus a context that gates children on a loaded bundle); canvas
interaction primitives the rendering library does not provide (click-hold node dragging,
wrapped multi-line labels with an interaction-driven reveal policy); WYSIWYG export to
SVG or PNG; and small shell utilities. The consistent design move is to split each
concern into a DOM-free pure core and a thin impure edge, so the tricky decisions are
unit-tested without a browser.

*Promises:*
- A drag gesture, once past threshold, stays a drag until release — `tapestry/src/lib/dragState.ts:52-57`.
- A drag restores exactly the layout state it interrupted — `tapestry/src/lib/dragState.ts:70-72`.
- The wire type must match the committed bundle schema field-for-field — `tapestry/src/lib/schema.test.ts:180-227`.
- Every bundle load failure raises an error naming its source — `tapestry/src/lib/data.ts:82-115`.
- The header never claims a graph the rendered data does not belong to — `tapestry/src/lib/BundleContext.tsx:99`.
- Deployment mode is discriminated by parsed shape, never by the sentinel literal — `tapestry/src/lib/live.ts:1-27`.
- Global shortcuts never fire while the reader is typing — `tapestry/src/lib/keyboard.ts:20-33`.
- Saved-view names are unique per graph and imports never throw — `tapestry/src/lib/savedViews.ts:37-123`.

#### tapestry/src/state — the whole client-side state (`tapestry-src-state`)

No server session and no router: two modules are the entirety of the SPA's state. One
flat store holds every cross-view dimension with a one-line setter each, so any
component subscribes to exactly the slice it renders. The other is the deep-link codec
and the only writer from a URL into that store — it serializes a deliberate subset
(view, selection, filters, time) into a hash, parses it back total-functionally, and
applies it.

*Promises:*
- Hash parsing is total: any corrupt hash yields an empty patch — `tapestry/src/state/urlHash.ts:16-21`.
- One function is the single write path from a URL hash into the store — `tapestry/src/state/urlHash.ts:24-38`.
- Restore distinguishes an absent key from an explicit null — `tapestry/src/state/urlHash.ts:35`, `:37`.
- Filters merge; every other setter replaces wholesale — `tapestry/src/state/store.ts:72` versus `:68-79`.

#### tapestry/src/views/explorer — the Graph Explorer (`tapestry-src-views-explorer`)

The SPA's primary view: a full-bleed force-directed weave of the whole graph. It owns
three responsibilities that reach beyond its own tab — the bundle-to-model builder that
produces the single graph model every view shares (record type as node fill,
connectivity as size, connection family as edge tint, strength as width); the canvas
instantiation with wheel-zoom, hover-neighbourhood focus, click-select, arrow-key
walking, fuzzy search, saved views and export; and a set of small pure modules
(filters, path mode, legend rows, layout) each with its own unit-test file.

#### tapestry/src/views/overview — the dashboard (`tapestry-src-views-overview`)

A read-only roll-up answering "what shape is this weave in" before anyone opens the
Explorer: six headline tiles, composition bars, a graph-health panel, a confidence
histogram, and a most-central table whose rows navigate into the Explorer. All
arithmetic is confined to one pure pass over the bundle; the component only sorts,
formats and paints. Optional sections degrade to an em dash rather than erroring.

#### tapestry/src/views/systems — the causal-loop diagram (`tapestry-src-views-systems`)

Re-reads the weave as a systems-dynamics model: a causal-only subgraph, edges coloured
by polarity on a diverging channel with a glyph at every midpoint, the analytics pass's
feedback loops in a right rail where selecting one isolates it, a signed pulse
travelling the isolated loop in its influence direction, and a numbered badge on each
variable carrying a leverage point. Pure model helpers live apart from the renderer so
the maths is unit-testable without a DOM.

#### tapestry/src/views/chronicle — bi-temporal time travel (`tapestry-src-views-chronicle`)

The browser-side answer to the event-sourced core. It reimplements as-of read semantics
client-side over the event log shipped inside the bundle: a timeline builder flattens
events into millisecond lookups, a state projection says which nodes and edges existed
at an instant and each node's effective status, and a differ classifies what changed
between two instants as added, invalidated or changed. The React layer renders that
projection over the *same* shared graph model the Explorer uses, purely through
reducers and overlay badges — nothing is ever mutated, so dragging the scrubber replays
the weave assembling itself.

#### tapestry/src/views/semantic — the Semantic Map (`tapestry-src-views-semantic`)

A scatter that plots each record at its precomputed embedding-projection coordinate, so
screen distance reads as meaning-distance rather than link-distance. It is the only
canvas view with no edges and no force layout: the projection *is* the layout, so the
map mounts ready. On top of the point field it adds convex-hull outlines around each
cluster (a neutral region channel, never a record-type hue) and a freehand lasso that
brushes enclosed points into the shared highlight slot the Explorer reads, plus a
keyboard-reachable cluster-brush menu producing the same selection.

### 2.3 The test suite

Twelve groups, 121 test files at the top level plus fixtures. The suite is the
executable half of this map: many of its modules are decision records for a specific
change, pinning *why* a threshold, a conjunction or a refusal exists, with the failure
they were written against named in the docstring.

- **`tests-1`** — shared scaffolding plus the first alphabetical slice. A session-scoped live database connection and a per-test namespace let every integration test write to a throwaway graph against one shared server; a fakes module supplies the only supported test doubles so no test defines its own stub.
- **`tests-2`** — the CLI protocol boundary: input parsing, typed error codes, the registry's single construction path, the generic schema flag, the composite framework primitives, and the consumption commands an agent reads a codebase with.
- **`tests-3`** — the codebase-extraction pipeline end to end (cross-file resolution and its precision guards, doc-to-code linking, the string codec, path filters, bi-temporal retirement of legacy edges, run records and rollback), plus two cross-cutting CLI invariants proven by walking the registry.
- **`tests-4`** — the substrate contract bed: the store itself (CRUD, bi-temporal history, event log, vector-index readiness), the code extractor and its incremental path, the domain model, config-routed model clients, the pure graph algorithms, and the folder importer.
- **`tests-5`** — the operations-layer acceptance suite: the seam where a JSON request becomes a graph mutation.
- **`tests-6`** — contract and conformance: the connection-write verification gate, the read-port conformance suite every adapter must satisfy, the write-receipt mechanism, the dry-run honesty contract, the embedding state machine, and the fingerprint goldens.
- **`tests-7`** — semantic search, store invariants and calibration: the atomicity promise under injected failure at four distinct points, filter pushdown proved equivalent to a Python oracle, and the one vector-search core exercised at the seam rather than once per caller.
- **`tests-8`** — three subsystems that convert graph state into a judgement or an artifact: synthesis fidelity, the visualization pipeline, and work memory.
- **`tests-9`** — the acceptance suite for branchable belief worlds and belief blast radius; one 1,328-line module proves the whole world subsystem end to end.
- **`tests-fixtures-multi`** — a four-file snapshot seed standing in for a whole multi-graph deployment, the smallest input that exercises every branch of the folder importer.
- **`tests-fixtures-repo`** and **`tests-fixtures-repo-src`** — a miniature multi-language repository that is the fixed golden input for extraction: not sample application code but a hand-tuned parser input where every construct exists to exercise one extractor behavior.

### 2.4 Contract and documentation

- **`root-1` — `CLAUDE.md`.** The repo-root agent contract: the single file every session loads before touching the codebase. Six load-bearing invariants, the toolchain incantations, the package layout, a graph-first research protocol with a copy-pasteable first query and an escalation ladder, and the writing conventions. Its instructions explicitly override default behavior, so every statement is a constraint rather than advice. Its invariant claims are anchored at `CLAUDE.md:18-21` (one transactional store), `:22-25` (invalidate, never overwrite), `:26-28` (the model as source of truth), `:29-32` (typed error codes), `:33-34` (one config path), `:121-122` (the registry is the source of the CLI).
- **`root-2` — `COMMANDS.md`.** The published contract surface, generated from the registry and checked in. Every command appears with its summary and a flattened table of every input field. Field descriptions carry real behavioral disclosure, so the catalog doubles as the behavioral contract. The checked-in file is byte-identical to generator output, enforced at `tests/test_generate_docs.py:43-49`.
- **`root-3` — the contract layer.** `pyproject.toml` (dependency floors, the `loom`/`the-loom` console entry points both bound to `theloom.cli.app:main`, and the lint/type/test gate), `docker-compose.yml` (the single FalkorDB service — its data volume must mount at `/var/lib/falkordb/data`, not `/data`, or the RDB snapshot is written nowhere durable — with `RESULTSET_SIZE -1` so a full-scan read errors instead of silently truncating), and the prose contract in `CONTEXT.md` / `CONTRIBUTING.md` / `README.md` / `STACK.md` / `SECURITY.md`. Two operator scripts build throwaway graphs, guarded by hard-coded non-default names (`scripts/gen_bench_graph.py:65-68`, `scripts/seed_live_dev.py:24-27`) and never touch the embedder, so the graphs they seed carry no vectors. As of commit `11e6e83`, `STACK.md` closed the dependency-rationale gap this map flagged at the prior run: it now carries rows for `numpy` (`STACK.md:38`), the `fastapi`/`uvicorn` `viz-serve` extra and the `umap-learn` `viz-umap` extra (`STACK.md:67-68`), and explicitly flags `readability-lxml` as declared-but-unused (`STACK.md:70-73`). One entry the rewrite did not resolve, and the one root-3 risk worth tracking now, is below (Tier 4, risk 35).
- **`root-4` — `uv.lock`.** The resolved, hash-pinned dependency closure: 187 package entries, every artifact carrying a checksum and an upload timestamp, locked universally across every supported interpreter and platform rather than per-environment.
- **`docs`** — the written-decision layer: numbered architecture decision records, dated design specs written before implementation, and recorded benchmark numbers with their reproduction recipe. Prose, not code: nothing imports these files.
- **`docs/agents`** — three prose files that bind the repo's generic, portable agent skills to this repo's concrete infrastructure, read at run time as configuration rather than imported as code. `domain.md` tells an exploring skill which domain prose to read first (`CONTEXT.md`, `docs/adr/`) and how to surface a contradiction with an ADR; `issue-tracker.md` names GitHub Issues on `jpwinans/the-loom` as the canonical tracker, gives the exact `gh` invocations, and defines the `/wayfinder` map-and-children protocol including blocking edges and the frontier query; `triage-labels.md` maps the five canonical triage roles onto literal `gh issue edit` label strings. The indirection is what let the 2026-08-09 Jira-to-GitHub migration land by editing these three files rather than any skill. *Promises:* a triage label must already exist on the repo before it can be applied (`docs/agents/triage-labels.md:24-26`); blocking edges use the blocker's numeric database id, never its number or node id (`docs/agents/issue-tracker.md:51`); a wayfinder frontier ticket has zero open blockers and no assignee (`issue-tracker.md:52-53`); the repo is single-context — exactly one `CONTEXT.md` and one `docs/adr/` — and an absent domain doc is passed over silently (`docs/agents/domain.md:5`, `:12-17`, `:32-35`); the six `CLAUDE.md` invariants are standing ADRs, so contradicting one must be surfaced, never silently overridden (`domain.md:43-51`, `:14-17`). *Known tension:* GitHub is declared canonical while `TL-…` identifiers referencing the prior Jira tracker remain live and unresolvable through `gh` (`issue-tracker.md:5-12`, `triage-labels.md:5-8`); PRs are excluded from triage yet share one number space with issues (`issue-tracker.md:27`, `:35`).
- **`docs/architecture`** — the committed output of the mapping pipeline (this file, the query cheat sheet, and the manifest whose `commit` field is the baseline the next run diffs against). The map is itself a node in the graph it measures.
- **`examples`** — contributor-facing guides to the four agent skills the repository ships. No runnable code: each folder answers what the skill does, how to invoke it, and how it drives the CLI. It is also the honest-expectations layer, carrying cost, runtime and concurrency caveats the slash-command help text does not.

---

## 3. Load-bearing modules

Ranked by degree (how much of the codebase touches it directly) and by betweenness (how
much of the shortest-path traffic between unrelated parts flows through it). Scores are
normalized to the whole graph.

### By degree

| # | Record | Score | Why it is a hub |
|---|---|---|---|
| 1 | `theloom/store/multigraph.py` | 0.0254 | The facade every command passes through to get a store: it resolves a graph name and belief world, manages the registry, sessions and worlds. `MultiGraph.get_store` is the single resolution path (`multigraph.py:200-229`), so every handler in `theloom/operations/*` and `theloom/composites/*` imports it. |
| 2 | `pkg:typing` | 0.0252 | The standard-library typing module — imported by nearly every Python file. A structural artifact of a strictly typed codebase rather than an architectural fact. |
| 3 | `CommandInput (common)` | 0.0245 | The base class of essentially every command input model in the repository (`theloom/operations/common.py`); the snake/camel boundary and the shared `graph` / `world` fields live on it, so all ~180 command schemas inherit from this one class. |
| 4 | `theloom/store/falkor.py` | 0.0217 | The concrete store: 1,407 lines implementing the whole abstract surface over one database graph. Every read and write in the system eventually lands here. |
| 5 | `theloom/model.py` | 0.0213 | The declared single source of truth — 20 record types, 18 connection types, the lifecycle table, confidence and provenance. Imported by the store, the operations layer, verification, extraction, the visualization schema, and mirrored by the frontend palette. |
| 6 | `run_handler (registry)` | 0.0196 | The single dispatch seam. Input validation, belief-world scoping and receipt collection are applied here once for all commands (`registry.py:1901-1918`), so it sits between the CLI and every handler in the codebase. |
| 7 | `tapestry/src/views/explorer/Explorer.tsx` | 0.0178 | The largest frontend file (1,052 lines) and the SPA's primary view; its degree is dominated by the many local symbols it contains rather than by inbound dependencies. |
| 8 | `tapestry/src/views/chronicle/Chronicle.tsx` | 0.0172 | Same shape: a large single-file view hosting the replay engine, reducers and play loop. |
| 9 | `tapestry/src/views/semantic/SemanticView.tsx` | 0.0146 | 710 lines of canvas wiring, hull overlay and lasso logic in one module. |
| 10 | `tapestry/src/views/systems/SystemsView.tsx` | 0.0145 | The causal-loop renderer with its overlays, animation and exports. |
| 11 | `iso_now (timeutil)` | 0.0129 | The single canonical timestamp producer. Every created or updated record, every event, and every bi-temporal bound calls it. |
| 12 | `theloom/cli/registry.py` | 0.0124 | 1,918 lines declaring the entire command surface; the module-scope import block reaches into every operations module. |
| 13 | `theloom/store/worlds.py` | 0.0118 | Branchable belief worlds implemented as event-log replay over a reserved graph segment — every world command and every overlay read passes through it. |
| 14 | `tests/test_synthesis_fidelity_semantic_grounding.py` | 0.0117 | The largest single test module by degree; its size is local test fixtures, not architectural weight. |
| 15 | `theloom/errors.py` | 0.0117 | The typed error hierarchy. Every module that can fail imports it, which is nearly all of them. |

### By betweenness

| # | Record | Score | Why it is a bridge |
|---|---|---|---|
| 1 | `theloom/store/multigraph.py` | 0.00080 | Everything above the store reaches persistence through it and nothing else; remove it and the operations layer is disconnected from the store layer. |
| 2 | `theloom/store/falkor.py` | 0.00063 | The other half of that bridge — where the abstract surface becomes queries. |
| 3 | `theloom/cli/registry.py` | 0.00056 | The only path from the CLI application into the operations layer. |
| 4 | `theloom/viz/bundle.py` | 0.00038 | The single assembler: it is the one point where analytics, temporal and semantic sections, the store, and the wire schema all meet before emission. |
| 5 | `theloom/operations/semantic.py` | 0.00026 | The junction between the retrieval core and the discovery commands (search, clustering, gap detection, embedding lifecycle). |
| 6 | `theloom/config.py` | 0.00024 | The one configuration resolution path, called from the CLI, the store, the embedder seam and several operations modules. |
| 7 | `theloom/operations/analysis.py` | 0.00021 | Sixteen traversal and analytics commands bridging the CLI to the pure graph kernel. |
| 8 | `CLAUDE.md` | 0.00018 | New to this table this run. The repo-root agent contract now sits on the shortest path between the doc-reference cycle's members (`README.md` → `examples/map-codebase/README.md` → `docs/architecture/QUERYING.md` → back to `CLAUDE.md`) as that cycle shrank to one loop — it is the single hinge a reader crosses to get from any one root document to the others. |
| 9 | `theloom/semantic/embed.py` | 0.00017 | The embedder contract and the resolution seam every vector-producing path funnels through. |
| 10 | `docs/architecture/ARCHITECTURE-MAP.md` | 0.00014 | This file. It is a node in the graph it measures: its outbound documentation references connect subsystems that share no code. |
| 11 | `README.md` | 0.00013 | Same shape — the documentation hub that links the contract files to the subsystem docs. |

The shape to take away: **the store facade and the command registry are the two
structural chokepoints of the Python package.** Any change to `MultiGraph.get_store` or
to `run_handler` is felt by every command; check `blast-radius` before touching either.

---

## 4. Dependency cycles

Cycle detection found 16 cycles (down from 20 at the prior full map, mostly documentation
cross-references that resolved as the doc set changed). They fall into four kinds, and only
one kind is worth acting on.

### Import cycles in the store layer — **intentional**

| Members | Verdict |
|---|---|
| `theloom/store/falkor.py` ↔ `theloom/store/read_port.py` | Intentional. `read_port.py` declares a structural `Protocol` and asserts conformance at type-check time only: the imports of the concrete adapters sit under a `TYPE_CHECKING` guard (`read_port.py:171-179`), so there is no runtime cycle and no import cost. |
| `theloom/store/read_port.py` ↔ `theloom/store/memory.py` | Intentional, same mechanism — the in-memory adapter is the second half of the compile-time conformance check. |

### Documentation reference cycles — **intentional**

One cycle among the prose files this run (down from five at the prior full map — the
others resolved as the doc set was edited):

| Members |
|---|
| `CLAUDE.md` → `README.md` → `examples/map-codebase/README.md` → `docs/architecture/QUERYING.md` → `CLAUDE.md` |

Mutual linking between an index, a contract and a walkthrough is the intended shape of a
documentation set. No action.

### Recursive functions — **intentional**

Twelve self-loops, each a function that calls itself. All are ordinary recursion over a
tree or a nested structure:

`_jsonify (io)` · `type_str (schema)` · `_hash_at_depth (fingerprint)` ·
`_js_string (prompts)` · `_generic_json_to_blocks (parsers)` ·
`_resolve_references (symbolic/core)` · `_substitute (test_claude_examples_contract)` ·
and six tree-sitter walkers: `_extract_calls`, `_find_identifier`,
`_extract_require_calls`, `_string_literal_vocabulary`, `_comment_notes`.

One of these is worth a second look, not because the recursion is wrong but because of
where it runs: the recursive descent in `theloom/graph/cycles.py` and
`theloom/verification/checks.py:185-211` has **no depth guard**, so a sufficiently deep
graph raises a Python recursion error rather than a typed one (see Risk 12).

### Mutual recursion — **intentional**

`_object_rows (schema)` ↔ `_nested_rows (schema)` — the two halves of the schema walker
that flattens nested models into the dotted-path field tables `COMMANDS.md` publishes.
Mutual recursion is the natural shape for walking an object/array-nested schema.

**Net: no cycle in this codebase is a defect.** The store-layer pair is a deliberate
type-only construction, the documentation ring is intended, and the rest is recursion.

---

## 5. Communities vs. directories

*Not recomputed this run (embedding-heavy clustering) — as of commit `624f69d3a4478ff3f04735ec6424d8888f4951b3`.*

Whole-graph structural connectivity gives a clean answer: **three connected components.**

| Component | Size | What it is |
|---|---|---|
| 1 | 7,177 | Everything — the Python package, the frontend, the tests, the docs, and the written layer, all one connected mass. |
| 2 | 14 | `tests/fixtures/multi` — the four-file multi-graph snapshot seed plus its written layer. It is deliberately disconnected: the fixture holds raw JSON snapshots with hand-copied record ids, so no import, call or documentation reference links it to anything. |
| 3 | 1 | `theloom/graph/__init__.py` — an empty namespace marker, per the project-wide convention of deep submodule imports. Nothing imports it and it imports nothing. |

Semantic clustering, run over a 500-record sample of the 7,192 embedded records, is a
**reported null result at this scale**: it returned eight clusters, the largest of size
4, and every one of them sits inside a single directory. The largest
(similarity 0.72) groups `Explorer.tsx`, `Minimap.tsx`, `filters.ts` and
`SemanticView.tsx` — all frontend view code. The rest are local-variable neighbourhoods
inside one component each (`Chronicle`, `EventList`, `Explorer`, `SystemsView`,
`FilterPanel`), plus one genuinely meaningful pair: `MultiGraph.get_store (multigraph)`
sitting next to the written invariant *"MultiGraph.get_store is the single resolution
path for both graph and world"* — the semantic layer finding its own anchor.

**Interpretation:** at this sample size the embedding-space communities do not disagree
with the folder structure. That is a weak signal rather than a strong endorsement — a
7% sample of a 7,000-record graph will surface only very tight local neighbourhoods. The
honest reading is that no cross-directory community was detected, not that none exists.
The structural seams that *are* visible are the two isolated components above, and both
are intended.

---

## 6. Risks and tensions

250 tensions are recorded across the 54 groups. These are the ones a reviewer should
know about first, worst-first. Each is a real trade-off with an anchor — several are
explicitly accepted in the code that carries them.

### Tier 1 — security and dependency surface

1. **The live visualization server is unauthenticated and unbounded per request.** Read-only-ness and localhost scope are policy statements in `SECURITY.md:12-17`, not code: the app is constructed with no middleware, no auth, and no per-request limit — `theloom/viz/serve.py:43-49`, `:93`, `:51-83`. Anyone who can reach the port can read the whole graph.
2. **Request-forgery validation and the actual fetch resolve DNS separately.** The guard resolves every address and rejects on the first non-global one (`theloom/documents/ssrf.py:58-71`), then the fetch re-resolves the same URL string (`:92-95`). The time-of-check/time-of-use window is acknowledged in the module docstring at `ssrf.py:8-9`.
3. **A required document-parsing dependency drags the full deep-learning stack into the default install.** `uv.lock:4217` records the edge; `uv.lock:797-830` and `:4330-4345` show what it pulls in. Every user of the CLI installs it whether or not they ingest documents.
4. **One optional extra cannot build on newer interpreters.** On Python 3.14+ the visualization extra resolves to a 2021 compiler toolchain with no wheels — `uv.lock:2098-2119`.

### Tier 2 — invariants the code itself qualifies

5. **The transaction is not a rollback boundary, so multi-step atomicity is a caller obligation.** Stated plainly at `theloom/store/commit.py:45-49`; `falkor.py:860-912` pays the difference back with explicit compensation. Any new multi-statement mutation must do the same or it silently loses the guarantee.
6. **Hard deletion is a supported escape hatch from the append-only invariant.** Declared on the abstract contract (`theloom/store/base.py:66-71`, `:115-125`) and used as a compensating rollback in at least five places — `theloom/operations/entity.py:396-413`, `inference.py:365-367`, `work_memory.py:166-169`, `composites/self_improve.py:454-465`, `calibration.py:239-272`. Each use is justified; collectively they mean "history is never destroyed" is a convention, not a mechanism.
7. **Purging a ref erases it with no event at all** — `theloom/store/refs.py:248-260`, reached by `worlds.py:890-902`. This is the one write path in the store that leaves no trace.
8. **Belief-world overlay is transparent for graph rows but local-only for vectors and metadata.** The full inventory of what a world cannot reconstruct is in the class docstring at `theloom/store/worlds.py:171-255`; commands that hit the gap must attach a partial-projection notice, and several duplicate that apology in their own words.
9. **Graph-layer failures escape as untyped Python exceptions.** `theloom/graph/analytics.py:68` raises a bare `RuntimeError`, reached from `theloom/operations/analysis.py:293` with no wrapper — a direct exception to the typed-error-codes invariant.
10. **The pure graph layer writes to the store in one function.** `theloom/graph/cycles.py:20` imports the concrete store and `:293-334` persists a loop record — the single exception to an otherwise side-effect-free kernel.
11. **The envelope invariant is enforced by an `assert`.** `theloom/cli/registry.py:1909` and `:1913-1917` — assertions are stripped under optimized interpreter flags, so the guarantee that no command returns a bare array is only as strong as the run mode.
12. **Cycle and component traversals recurse in Python with no depth guard** — `theloom/graph/cycles.py:38`, `:63`, `:93`, `:101`; `theloom/verification/checks.py:185-211`. A deep enough graph produces a recursion error rather than a typed one.

### Tier 3 — cost and scale

13. **Whole-graph hydration sits beside the layer's own never-a-full-scan promise.** Two private copies of the same helper — `theloom/operations/algebra.py:67-70` and `analysis.py:61-64` — plus `blast_radius_traversal.py:61-75`.
14. **Discovery commands issue one vector search per record over a whole-graph load** — `theloom/operations/semantic.py:841-848` (clustering) and `:898-899` (gap detection). This is why those two commands are the slow ones.
15. **The deduplication gate full-scans every record in order to use a search core built to avoid full scans** — `theloom/semantic/deduplication_gate.py:40-44`, `:100-106`.
16. **Cross-graph connection creation costs a scan of every graph and of every bridge** — `theloom/store/multigraph.py:374-398`.
17. **Legacy bridge migration runs on first access, in every process, forever** — `theloom/store/bridges.py:230-250`, guarded only by a per-instance flag.
18. **Overlay reads merge whole-graph row sets in Python, not in the query language** — `theloom/store/worlds.py:326-346`, `:513-534`.
19. **Self-improvement cost scales as proposals times a whole-graph clone** — `theloom/composites/self_improve.py:265-273` calling `simulate_change.py:100-110` inside the proposal loop.
20. **Capability validation re-materializes the whole graph per check and scans connections per record** — `theloom/verification/capability_spec.py:36-44`, `:251-264`.

### Tier 4 — honesty and drift

21. **Inert knobs: input fields the CLI accepts but the code cannot act on.** `theloom/composites/far_analogy_retrieval.py:18-22`, `creativity_loop.py:41-45`, `:139`; `theloom/operations/verification.py:110-111` versus `:468-513`; `theloom/analysis/cwsg.py:16-19`; `timeoutMs` advertised in three analysis modules and enforced in one (`crossdomain.py:29`, `slippage.py:45` versus `isomorphism.py:187-193`). Documented in the generated catalog, so a caller has no way to know.
22. **`constrained-generate` commits records but discards connections while reporting none skipped** — `theloom/operations/verification.py:552-569`.
23. **Truncation is announced by `list-entities` but silent in every epistemic query** — `theloom/operations/entity.py:478-489` versus `epistemic.py:94-97` applied at `:248`, `:282`, `:312`, `:356`, `:400`.
24. **`create-relations` persists a valid prefix on the batch it reports as failed** — `theloom/operations/relations.py:253-258`, `:301-318`.
25. **`merge-world` skips connections with absent endpoints without saying so** — `theloom/operations/worlds.py:711-717`.
26. **Zeroed in-memory exploration state makes three signals structurally inert.** The visit counters always start empty (`theloom/exploration/exploration_state.py:6-22`, `:100-104`), so the exploration bonus, the patch-leaving policy and the visit-count guards can never fire in a single process run.
27. **Gap-fill interestingness is always zero in template mode**, so only a non-positive commit threshold can ever commit — `theloom/composites/gap_fill_cycle.py:89-104`, `:226-246`.
28. **Two dead paths in the entity proposer:** a filtered-count no code path can make non-zero (`theloom/semantic/entity_proposer.py:554-576`) and a reasoning strategy unreachable in-repo (`:10-19`, `:121-123`).
29. **The frontend hand-mirrors the Python taxonomy, and drift fails silently** — `tapestry/src/views/explorer/buildGraph.ts:28-61`, `:70-90`; the palette covers 19 record types against the model's 20 (`tapestry/src/design/palette.ts:7-27`).
30. **Client-side replay silently under-reports merges and connection updates** — `tapestry/src/views/chronicle/replay.ts:177-196`; the event shape carries no per-node id for those two event kinds.
31. **Hand-maintained counts shadow the generated catalog.** `README.md:30-31`, `CLAUDE.md:7-12` and `examples/map-codebase/README.md:34-35` all restate figures that `COMMANDS.md:5` computes. The drift has already happened once and was corrected out of band.
32. **A process-global test seam lives in the production config module** — `theloom/config.py:359-373`, consumed at `theloom/semantic/embed.py:117`.
33. **A malformed config file is silently ignored while a malformed value is a hard error** — `theloom/config.py:174-178` versus `:150-155`.
34. **Wall-clock sleeps carry the most important assertions in the bi-temporal tests** — `tests/test_falkor_store.py:88`, `:282`, `:584-587`; `tests/test_ops_merge.py:391-430`; `tests/test_worlds.py:25-29`. These are the suite's flake surface.
35. **A stale `constraint.*` mypy override outlives the dependency-rationale gap it once tracked.** `STACK.md` (as of `11e6e83`) closed the previous gap between the declared dependency set and the documented rationale — it now carries rows for `numpy` (`STACK.md:38`), the `fastapi`/`uvicorn` `viz-serve` extra and the `umap-learn` `viz-umap` extra (`STACK.md:67-68`), and explicitly flags `readability-lxml` as declared-but-unused (`STACK.md:70-73`). But `pyproject.toml:87` still lists `constraint.*` in the mypy per-module `ignore_missing_imports` override, alongside `falkordb`/`sympy`/`z3`/`tree_sitter_typescript`/`umap`/`fastapi`/`uvicorn` — and `constraint` is neither a declared dependency nor imported anywhere under `theloom/`. Inert today; it would silently disarm type checking if a module literally named `constraint` were ever introduced. (Superseded finding: the prior run's broader library-rationale drift, flagged against `STACK.md:55-66` at commit `86f50bd4`, is resolved by the same `11e6e83` rewrite.)

---

## 7. Open seams

*Not recomputed this run (embedding-heavy gap detection) — as of commit `624f69d3a4478ff3f04735ec6424d8888f4951b3`.*

Pairs of records that read as near-duplicates in meaning but have no connection between
them. Twenty were surfaced; these are the ones that point at something real rather than
at a naming coincidence.

| Pair | Similarity | What it suggests |
|---|---|---|
| `WorldGraphStore.read_entity_doc` ↔ `read_entity` / `read_entities` / `read_entity_docs` (worlds) | 0.79 / 0.77 / 0.76 | The wire-doc twins. The read port declares one dialect and explicitly defers the question of whether the doc-returning twins should survive (`theloom/store/read_port.py:10-17`); the overlay still implements both. This is the single largest duplicated surface in the store. |
| `DocumentIngestion._ingest` ↔ `DocumentIngestion.ingest_content` | 0.79 | The private pipeline core and its public content entry point have drifted close enough to be indistinguishable by meaning — worth checking whether the second is still adding anything. |
| `EmbedEntitiesInput` ↔ `EmbedEntityInput` (semantic) | 0.78 | Two command input models one character apart. A caller reading the catalog has to compare field tables to tell them apart. |
| `MAX_DEPTH_LIMIT` ↔ `DEFAULT_MAX_DEPTH` (fingerprint) | 0.75 | The two depth constants published by the fingerprint module — which the module's own tension notes are re-declared and hard-coded elsewhere (`theloom/reification/fingerprint.py:22-23` versus `:88`, `:120`). |
| `createWrappedLabelRenderer` ↔ `createWrappedHoverRenderer` (nodeLabels) | 0.76 | Two renderers that must be overridden together — the requirement is documented at `tapestry/src/lib/nodeLabels.ts:193-207` but expressed nowhere in the type system. |
| `_MappedEmbedder.__init__` / `.embed_query` in `test_ops_verify_fidelity_semantic_grounding` ↔ the same in `test_synthesis_fidelity_semantic_grounding` | 0.78 / 0.77 | A test double copied between two modules instead of living in the shared fakes module the suite already has. |
| `resolve (test_calibration_alerts)` ↔ `resolve (test_calibration)` | 0.77 | Same shape: duplicated test helper. |
| `TYPE_LABEL` ↔ `typeLabel` (EventList) | 0.76 | A constant and a function one casing apart in the same file. |
| `file` ↔ `path` (Explorer); `types` ↔ `filters`, `ordered` ↔ `order` (FilterPanel) | 0.76 / 0.76 / 0.76 | Local-variable naming that carries no distinction — small, but they are the kind of pair that makes a 1,052-line untested component harder to read. |

The remaining pairs are adjacent test cases whose names differ only by the branch they
exercise (for example the two symmetric/asymmetric grounding rejections, or the two
vector-index construction outcomes). Those are expected and need no action.

---

## 8. Coverage and methodology

**Coverage.** This was another incremental refresh, not a full re-map. 55 of 55 module
groups have a description in the graph; no group is unenriched or missing. This run
re-enriched exactly one group fresh from the code at commit `11e6e83`: `root-3` (`repo
root, part 3/4`) — the diff between `86f50bd4` and `11e6e83` touched `STACK.md` (closing
the dependency-rationale gap flagged at the prior run), `docs/agents/issue-tracker.md`
(a four-line clarification that a `TL-…` id is human-resolvable in Jira but has no `gh`
equivalent), and removed a stray `pr-test.md` at the repo root. `docs/agents` (`docs-agents`)
was carried forward this run — its diff (the four-line `issue-tracker.md` clarification)
was too small to justify re-enrichment, so its semantic layer above is unverified against
that four-line change specifically, though it was freshly enriched at the prior run
(`86f50bd4`) and nothing else in the group moved. Four other groups — `theloom/cli`,
`theloom/operations (part 3/4)`, `theloom/store (part 2/2)`, and everything not named
above — remain carried forward from the runs where they were last (re-)enriched
(`theloom/cli`, `theloom/operations (part 3/4)` and `theloom/store (part 2/2)` from the
full map at commit `624f69d`; the rest from `86f50bd4`) and have not been re-verified
against the current code. The load-bearing-modules and cycles numbers above are freshly
recomputed this run; the Communities-vs-directories (§5) and Open-seams (§7) sections
were **not** recomputed — they still reflect the whole-graph embedding pass from commit
`624f69d3a4478ff3f04735ec6424d8888f4951b3`.

**What is not in the graph.** 43 files were not parsed: the extractor handles Python,
TypeScript, JavaScript, Markdown, JSON, CSS and a few config formats, and anything
outside that set appears either as a bare file record with no symbols or not at all. The
graph also records only what static analysis can see — dynamic dispatch, string-keyed
lookups and runtime registration are invisible to it, which is why several risks above
had to be read out of comments and docstrings rather than out of edges.

**Working-tree caveat.** The tree was dirty at extraction (a modified `.gitignore` at the
repo root, plus uncommitted edits to this document set). Anchors into tracked, unmodified
files are exact.

**Graph.** `codebase-the-loom`, describing commit `11e6e831a003b72e9ac196ddf0387bde35361693`.

**To re-run:**

```
/map-codebase <repo-root>
```

The next run reads the `commit` field in `map-manifest.json` as its baseline and
re-describes only the groups whose files changed.

**To interrogate the graph directly** — the fast path, in preference order:

```bash
# One call: definition, callers, callees, imports, and the written notes
loom explore '{"name": "run_handler (registry)", "graph": "codebase-the-loom"}'

# What breaks if I change this
loom blast-radius '{"name": "MultiGraph.get_store (multigraph)", "graph": "codebase-the-loom"}'

# Everything about one record, including its written layer
loom entity-deep-dive '{"name": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'

# By meaning rather than by name
loom hybrid-search '{"query": "how does a write become atomic", "graph": "codebase-the-loom"}'
```

Record names take two forms: `file:<repo-relative-path>` for files and
`<symbol> (<module stem>)` for symbols. An unambiguous substring resolves; an ambiguous
one lists the candidates rather than guessing.

The full recipe sheet — one command per question class, plus the module-group ids you
need to address the written layer — is in
[QUERYING.md](QUERYING.md).
