---
repo: the-loom
commit: c470c03fb041fd0d98a659edb109c9cfa85cbf8d
graph: codebase-the-loom
generated: 2026-08-08
mode: incremental
---

# The Loom — architecture map

A newcomer's walkthrough of this repository, derived from the FalkorDB graph
`codebase-the-loom`. Every statement below is a projection of a record in that
graph; each invariant and risk carries the `file:line` anchor the record was
written against. Nothing here is a second source of truth — when this document
and the code disagree, the code wins and the map should be regenerated (§8).

---

## 1. Executive overview

The Loom is a knowledge-graph substrate with a single JSON-in/JSON-out command
line interface. One FalkorDB instance holds everything — graph topology, entity
vectors, document chunks and the append-only event log — and every mutation is
one Cypher statement plus one event append inside a single Redis transaction.
Above that store sits a thin, uniform stack: a declarative command registry that
generates the entire CLI, an operations layer that owns command semantics
(addressing, validation, revision bookkeeping, typed errors) and delegates all
real computation downward, and a set of pure libraries — graph algorithms,
semiring algebra, embeddings and retrieval, symbolic math, document parsing,
codebase extraction, verification rules, narrative synthesis — none of which
knows the CLI exists. A contributor-only React/sigma.js single-page app
(`tapestry/`) is compiled into one HTML file that the Python package ships and
serves. A little over a third of the repository by file count is the test suite,
which is written as the executable specification of the invariants the prose
above can only assert.

| | |
|---|---|
| Files in the graph | 391 (plus 65 external package nodes) |
| Live records | 6,744 — 456 files/packages, 459 classes, 2,925 functions and methods, 1,501 module-level values, and 1,403 written-layer notes |
| Total records incl. superseded versions | 9,691 |
| Relations | 20,123 — 5,572 containment, 8,540 written-layer links, 3,887 call edges, 1,772 import/require edges, 200 inheritance, 126 doc-to-code references, 26 supersessions |
| Language mix | Python 268 files, TypeScript 74, Markdown 21, JSON 14, CSS 9, JavaScript 2, YAML/TOML/lock 3 |
| Files not parsed | 43 (non-code text and data files — CSS, JSON, Markdown, YAML, TOML, lockfiles — which become file records with no symbols) |
| Module groups covered | 46 |

> **Working tree was dirty at extraction time.** Uncommitted modifications were
> present under `.claude/`, `README.md` and `.gitignore` when this map was
> built. The structural layer describes the files as they were on disk, which is
> `c470c03` plus those edits; every anchor in this document should be treated as
> accurate to within that delta.

---

## 2. Subsystem walkthrough

Each subsection answers three questions: what the code is for, what must stay
true about it, and where it strains today.

### A. Project surface

#### 2.1 Repo root — declaration surface

The eleven root files state what the project is, what it is built from, how it
is run and gated, what its words mean, and how to report a hole in it. None of
them is imported by the package. `pyproject.toml` is the single manifest:
runtime dependencies with conservative floors, two console entry points, and the
configuration for all three quality tools. `docker-compose.yml` declares the one
FalkorDB service the architecture depends on. `CLAUDE.md`, `CONTRIBUTING.md`,
`README.md` and `STACK.md` are four audience-specific statements of the same
project; `COMMANDS.md` is the machine-generated catalog of all 164 commands
across 23 categories; `CONTEXT.md` is the ubiquitous-language glossary, most
terms carrying an explicit avoid-list; `SECURITY.md` draws the only trust
boundary the repo states. `scripts/` holds the only executable code here — two
local-only graph fabricators that reach past the CLI into `MultiGraph`.

*Key files:* `pyproject.toml`, `docker-compose.yml`, `CONTRIBUTING.md`,
`CONTEXT.md`, `scripts/gen_bench_graph.py`.

**What must stay true**

- Both console entry points resolve to the same callable — `pyproject.toml:53-55`.
- `mypy --strict` covers `theloom` only and treats nine libraries as untyped —
  `pyproject.toml:81-84`, `pyproject.toml:86-88`.
- `COMMANDS.md` is generated from the registry and a test fails when it drifts —
  `COMMANDS.md:3`, `COMMANDS.md:5`.
- The green-main gate is four commands, and `ruff format --check` is one of them
  — `CONTRIBUTING.md:36-39`, `CONTRIBUTING.md:43-44`.
- The live-mode seed script refuses to delete the caller's default graph —
  `scripts/seed_live_dev.py:25`.

**Where it strains**

- The command count is hand-copied into two documents while only the catalog is
  generated — `COMMANDS.md:5` vs `README.md:30` vs `CLAUDE.md:8-9`.
- The repo layout is described three times and two copies have already drifted —
  `CLAUDE.md:57-74` vs `README.md:366-392` vs `CONTRIBUTING.md:111-118`.
- The glossary bans a word five commands are named after — `CONTEXT.md:104` vs
  `COMMANDS.md:245`, `:393`, `:758`, `:828`, `:886` — and is unreachable from
  every entry-point document (`CONTEXT.md:1-6`, sole unlinked mention at
  `README.md:388`).
- An ISC-licensed project whose only supported store is SSPL-licensed —
  `pyproject.toml:7`, `:13` vs `STACK.md:22-26`.
- Every quickstart chains store startup into a store-dependent command, and the
  service declares no healthcheck — `docker-compose.yml:2-20` vs
  `README.md:63-64`.

#### 2.2 Repo root — the lockfile

`uv.lock` is the resolved, digest-pinned dependency closure: 187 package blocks
and 2,826 sha256-pinned distribution records, all from one index. It contains no
code and nothing imports it, but it decides what every import inside `theloom/`
resolves to at runtime.

**What must stay true**

- Every locked artifact is digest-pinned to a single PyPI index —
  `uv.lock:899-901`; the sole non-registry source is the editable root at
  `uv.lock:4214`.
- The lock restates `pyproject`'s declarations, which is what makes drift
  detectable — `uv.lock:4259-4283`.
- A default sync installs the whole document-AI stack (torch, transformers,
  onnxruntime) — `uv.lock:816-846`.

**Where it strains**

- `falkordb`, the one non-negotiable dependency, is the only one with no version
  floor — `uv.lock:4262`.
- Python 3.14+ resolves `numba`/`llvmlite` onto 2021-era sdist-only releases —
  `uv.lock:2099-2118`, `uv.lock:1454-1468`.
- `python-graphblas` silently drops its numba JIT edge on Python 3.14+ —
  `uv.lock:3303-3310`.

#### 2.3 `docs/` — the written rationale layer

Where decisions the code cannot explain are recorded: two approved design specs
that fixed a subsystem's contract before implementation (the Tapestry
visualization surface and the `/map-codebase` skill), one numbered ADR recording
a deliberate absence, and one benchmark report closing the loop on a scale
ambition with measured numbers and honest caveats. Every file argues from the
repository's numbered architecture invariants and writes down what it chose not
to do.

**What must stay true**

- A dangling entity-to-chunk pointer yields no passage, never an error —
  `docs/adr/0001-soft-chunk-pointers.md:34-50`.
- No sidecar may track cross-graph pointers; one transactional store forbids it
  — `docs/adr/0001-soft-chunk-pointers.md:17-24`, `:65-68`.
- Scale numbers are reported benchmarks; no wall-clock assertion may enter CI —
  `docs/benchmarks/tapestry-scale.md:4-6`, `:19-20`.
- The visualization surface adds no store and never writes back to the graph —
  `docs/design/2026-07-11-loom-visualization-design.md:67-68`.

**Where it strains**

- Pre-implementation specs are frozen snapshots with nothing binding them to
  shipped code — `docs/design/2026-07-11-loom-visualization-design.md:3-4`
  against `:106-140`.
- The 50k-node target is met for interaction and missed sixfold on first paint —
  `docs/benchmarks/tapestry-scale.md:66`, `:79-82`.
- Two decision-record formats coexist and the ADR series has one entry —
  `docs/adr/0001-soft-chunk-pointers.md:1-5`.

#### 2.4 `docs/architecture/` — this map and its companions

The committed output of the `/map-codebase` pipeline: two prose projections of
the graph plus one machine-readable run record. Nothing in the package or the
frontend imports these files and no test asserts anything about them; they exist
for human reviewers, coding agents, and the next mapping run. `map-manifest.json`
is the incremental anchor — the next run reads its `commit` as the baseline.

**What must stay true**

- The manifest is input to the next run, not a report of the last one —
  `docs/architecture/map-manifest.json:4-5`.
- All three deliverables independently pin the same graph and the same commit —
  `map-manifest.json:2`, `:4`; the front matter of this file; `QUERYING.md`.
- No machine-specific absolute path appears in any deliverable —
  `map-manifest.json:3` (`"projectPath": "."`).
- Only the prose and the manifest are tracked; the visualization is declared but
  gitignored — `map-manifest.json` `outputs` block.
- Coverage is stated negatively as well as positively (§8).

**Where it strains**

- The baseline can never contain the deliverables, so this group re-enriches on
  every run — `map-manifest.json:4`.
- The map is a node in the graph it measures, and ranks in that graph's own
  centrality tables (§3).
- Legacy group identifiers are deprecated in prose but nothing retires them
  (§8).
- The directory's only consumer contract is a filename, in a repository built on
  typed schemas — `map-manifest.json` has no version and no schema reference.

#### 2.5 `examples/` — the skill guides

The public-facing documentation surface for the four Claude Code agent skills
this repository ships (deep-research, hyper-research, map-codebase,
loom-expedition). Deliberately guides-only: no runnable code, no schemas, no
agent prompts. Each guide answers what the skill does, how to invoke it, and —
the stated point of the collection — exactly how it drives the Loom's CLI to
build, query and maintain a knowledge graph.

**What must stay true**

- The two CLI invariants (relation payloads carry polarity/strength/evidence;
  embedding is a separate step) are stated once, in the index, for every example
  — `examples/README.md:39-46`.
- Every `loom` command named in the guides is a registered CLI command —
  `examples/deep-research/README.md:62-98`.
- Every repo-relative link in the guides resolves to a checked-in asset —
  `examples/README.md:18-23`.
- `loom-expedition` is documented as the one synchronous, read-only,
  write-nothing example — `examples/README.md:35-38`.

**Where it strains**

- The guides' `loom` invocations sit outside the docs contract test —
  `examples/deep-research/README.md:92-98`, `examples/map-codebase/README.md:57-65`.
- Measured figures and pipeline internals are duplicated into the guides —
  `examples/map-codebase/README.md:46-52`.
- The index generalizes across the examples, and a new example falsifies the
  generalization — `examples/README.md:35-38`.

### B. The `theloom` package

#### 2.6 `theloom/` — the contract layer

The six top-level modules every other subpackage imports but that import almost
nothing themselves. `model.py` is the single source of truth for the domain —
every enum value set in a stable order, the entity/relation/confidence/
provenance shapes, the paired `*Input` create schemas, the confidence-label scale
and the five-state lifecycle table — and it enforces invariants at the type level
rather than leaving them to callers. `errors.py` defines the six structured error
codes as a typed exception hierarchy. `config.py` is the one configuration
resolution path. `timeutil.py` fixes the canonical timestamp shape.
`migrate.py` imports graph snapshots.

*Key files:* `theloom/model.py` (574 lines, imported by 47 modules),
`theloom/errors.py` (34 importers), `theloom/config.py`, `theloom/timeutil.py`,
`theloom/migrate.py`.

**What must stay true**

- Every wire timestamp is ISO 8601 UTC, millisecond precision, `Z` suffix —
  `theloom/timeutil.py:12-15`, `theloom/model.py:38-49`.
- Unknown fields are rejected: every wire model forbids extras —
  `theloom/model.py:361-364`.
- The five-state lifecycle — `retracted` is terminal, only `investigating`
  returns to `active` — `theloom/model.py:313-338`.
- Errors carry their structured code from birth; the CLI never classifies by
  message text — `theloom/errors.py:12-19`, `:22-53`.
- Configuration resolves once, flags > env > file > defaults —
  `theloom/config.py:150-219`.

**Where it strains**

- Config file handling is fail-open for parse errors but fail-loud for field
  errors — `theloom/config.py:143-147` vs `:114-122`.
- The model enforces two invariants but only advises on the lifecycle —
  `theloom/model.py:341-353` (a predicate with no enforcement) vs `:433-438`.
- A process-global test seam lives inside the otherwise-pure config module —
  `theloom/config.py:285-310`.
- `migrate.py` is package code with no production caller —
  `theloom/migrate.py:31-33`.

#### 2.7 `theloom/cli/` — declaration, dispatch, protocol

The entire user-facing surface, and it holds no domain behaviour.
`registry.py` declares every command exactly once as a frozen descriptor built
from a declarative `_Spec` row; `app.py` mechanically generates one Typer
subcommand per descriptor at import time; `io.py` owns the wire protocol (JSON in
from argument or stdin, two-space-indented JSON on stdout, one-line
`{error, code}` on stderr with exit 1); `schema.py` is the single JSON-Schema
walker that serves the catalog, `--schema` output and self-describing validation
errors; `docs.py` renders `COMMANDS.md` as a pure projection of the same
descriptor list.

*Key files:* `theloom/cli/registry.py` (1,676 lines; 164 descriptors across 23
categories, dispatch at `:1666-1676`), `theloom/cli/app.py`,
`theloom/cli/schema.py`, `theloom/cli/io.py`, `theloom/cli/docs.py`.

**What must stay true**

- Every CLI command except `version` and `init` is generated from the registry —
  `theloom/cli/app.py:139-140`, exceptions at `:56-59`.
- Input validation happens once, in `run_handler`, and Pydantic failures become
  `VALIDATION_ERROR` — `theloom/cli/registry.py:1671-1676`.
- stdout carries exactly the result document; diagnostics go to stderr and
  failures exit 1 — `theloom/cli/io.py:87-92`.
- Non-finite floats serialize as `null`, keeping output valid JSON —
  `theloom/cli/io.py:56-64`.
- Every command states its stdin stance explicitly; `allow_empty` has no default
  — `theloom/cli/registry.py:112-123`.
- Every registry command exposes `--schema`, and it answers with no store
  running — `theloom/cli/app.py:108-119`.
- `schema.py` depends on pydantic and `theloom.errors` only, so `docs.py` and
  `registry.py` can share it without a cycle — `theloom/cli/schema.py:13-26`.

**Where it strains**

- `bulk-import`'s validation errors surface as `OPERATION_ERROR`, not
  `VALIDATION_ERROR` — `theloom/cli/registry.py:1669-1670`,
  `theloom/operations/bulk.py:270-273`.
- Every dispatching command opens a store connection before its handler runs —
  `theloom/cli/app.py:129`, `:83-88`.
- The schema walker's depth cap and cycle guard truncate silently and untested —
  `theloom/cli/schema.py:124`, `:160-171`.
- Command-name uniqueness is pinned by a test, not by the registry itself —
  `theloom/cli/registry.py:1659`.

#### 2.8 `theloom/store/` — persistence

Where the two hardest architecture invariants stop being prose and become
Cypher. It maps the domain model onto one FalkorDB instance so topology,
vectors, the event log and (through the same chassis) document chunks share a
single transactional store; and it makes every mutation event-sourced and
bi-temporal — a write is one Cypher statement plus its `XADD` inside one
`MULTI`/`EXEC`; an update snapshots the outgoing incarnation as a version node
instead of overwriting it; a delete invalidates unless the caller explicitly
asks for erasure.

*Key files:* `theloom/store/falkor.py` (1,143 lines), `theloom/store/space.py`
(the shared chassis: graph handle, event log, commit primitive, paged read,
vector and range indexes), `theloom/store/commit.py`,
`theloom/store/read_port.py`, `theloom/store/multigraph.py`,
`theloom/store/bridges.py`.

**What must stay true**

- A mutation and its event are committed as one unit or neither reaches the
  server — `theloom/store/commit.py:91-103`, `:12-20`.
- A failed event half is repaired forward, never rolled back, and the caller
  still sees success — `theloom/store/commit.py:112-127`, `:130-170`.
- An entity update snapshots the prior incarnation as a closed `:_EntityVersion`
  before the doc is swapped — `theloom/store/falkor.py:420-437`; the relation
  twin is `:936-963`.
- Deletion invalidates by default; `hard=True` is the only path that destroys
  history — `theloom/store/falkor.py:439-500`, `:967-1003`.
- Retraction drops the entity's embedding and closes every attached edge in the
  same statement — `theloom/store/falkor.py:478-486`.
- `filters.py` is the semantics oracle; the Cypher pushdown may only ever be a
  superset — `theloom/store/falkor.py:154-160`, `theloom/store/filters.py:69-100`.
- Any full-scan read must page, or FalkorDB silently truncates it —
  `theloom/store/paging.py:1-11`, `:24-44`.
- The vector index is write-once, sized from stored vectors, and only queryable
  behind an `OPERATIONAL` barrier — `theloom/store/space.py:122-137`, `:161-187`.

**Where it strains**

- Not every write is event-sourced: vectors, metadata and verbatim imports
  bypass the log — `theloom/store/falkor.py:270-275`, `:1129-1137`, `:252-262`,
  `:837-848`.
- The derived read index duplicates filter semantics that must be kept in sync
  by hand — `theloom/store/falkor.py:101-114` vs `theloom/store/filters.py:69-100`.
- The read port's docstring still describes relation updates as
  overwrite-in-place, which they no longer are — `theloom/store/read_port.py:106-109`.
- As-of reads reconstruct the past by scanning the whole present —
  `theloom/store/falkor.py:331-393`.
- `get_neighbors` does the per-id loop the store's own docstring forbids —
  `theloom/store/falkor.py:1083-1095` vs `:520-525`.
- Cross-graph lookup scans every graph and builds a store per graph to do it —
  `theloom/store/multigraph.py:132-142`.

#### 2.9 `theloom/operations/` (1 of 3) — inputs, entities, analytics, consumption

The seam between the registry above and the store, graph, algebra, analysis and
document subsystems below. Every module owns one command family and exposes one
plain function per command with the same shape:
`(params: SomeInput, multi: MultiGraph) -> dict | list`. This third covers the
shared input machinery, entity CRUD with its revision and status semantics, the
semiring/adaptive-routing commands, the traversal and analytics commands, bulk
import, the agent-facing comprehension commands (`explore`, `find-callers`,
`find-callees`, `blast-radius`) with their two deliberately store-free cores,
global document ingest, and the entity-proposal options adapter.

*Key files:* `theloom/operations/common.py`, `entity.py`, `consumption.py`,
`consumption_budget.py`, `analysis.py`.

**What must stay true**

- Entity addressing takes exactly one of `id` or `name`, and a blank name is not
  a name — `theloom/operations/common.py:132-137`.
- An ambiguous name is refused with candidates, never guessed —
  `theloom/operations/common.py:158-164`, `:82-91`.
- Name resolution reaches every status but prefers the live entity —
  `theloom/operations/common.py:68`, `:145`.
- Consumption reads apply their own active-status filter, because id hydration
  has none — `theloom/operations/consumption.py:254-269`.
- A truncated answer accounts for every row it dropped, and one oversized row
  blocks only its own section — `theloom/operations/consumption.py:400-424`,
  `theloom/operations/consumption_budget.py:70-83`.
- `blast-radius` counts the seed and its `part_of` members as seeds, never as
  fallout — `theloom/operations/blast_radius_traversal.py:129-155`.
- Every `update-entity` bumps the version and rewrites `previousVersionId`, and
  an invalid status transition is refused before any write —
  `theloom/operations/entity.py:271-272`, `:249-252`.
- A supplied but ignored `graph` parameter on document commands always returns a
  `PARAMETER_IGNORED` notice — `theloom/operations/documents.py:73-90`.

**Where it strains**

- Every analytics and algebra command hydrates the entire graph in memory —
  `theloom/operations/analysis.py:61-64`, `theloom/operations/algebra.py:67-70`.
- `blast-radius` pays a full graph scan on every call to compute one percentile
  — `theloom/operations/blast_radius_traversal.py:61-75`.
- Two name-resolution policies live in the same layer: refuse ambiguity, or let
  the last duplicate win — `theloom/operations/entity.py:419-422` vs
  `theloom/operations/common.py:153-164`.
- `createdRelationIds` does not contain relation ids —
  `theloom/operations/bulk.py:421`.
- `analyze-category` clusters with all-pairs cosine over up to 10,000 chunks —
  `theloom/operations/documents.py:410-415`.

#### 2.10 `theloom/operations/` (2 of 3) — relations, lifecycle, machinery

Relations and knowledge lifecycle: relation CRUD with the verification gate and
bridge-aware neighbourhood reads, duplicate consolidation, the seventeen
epistemic queries plus credit propagation, the forward-chaining inference engine
whose rules and traces are themselves graph entities, Weisfeiler-Leman pattern
reification, extraction dispatch with run status and rollback, JSON export,
bridge-index queries, prompt-profile loading, and `init`.

*Key files:* `theloom/operations/epistemic.py` (944 lines), `inference.py` (619),
`relations.py` (574), `extraction.py` (364), `reification.py` (299).

**What must stay true**

- The causal/polarity partition is an invariant of the stored edge, not just of
  creation — `theloom/operations/relations.py:321-359`.
- The endpoint gate checks status, not just existence, in both arities —
  `theloom/operations/relations.py:56-59`, `:246-274`.
- A failing strict relation batch still persists its valid prefix —
  `theloom/operations/relations.py:281-282`.
- `merge-entities` supersedes the secondary rather than deleting it, and is
  idempotent — `theloom/operations/merge.py:186-195`, `:110-126`.
- `reify-patterns` is idempotent through a fingerprint marker observation —
  `theloom/operations/reification.py:163-180`.
- Credit propagation clamps confidence to `[0,1]`, halts below `minDelta`, and
  visits each node once — `theloom/operations/epistemic.py:846-887`.
- Inference-rule conclusions may only reference variables bound by the
  conditions — `theloom/operations/inference.py:318-324`; derived polarity comes
  only from the causal defaults — `:526`.
- Every inference-derived relation carries provenance naming its rule and its
  trace — `theloom/operations/inference.py:536-543`.
- `init` creates a `0700` config directory and a `0600` config file, and is
  idempotent — `theloom/operations/init.py:22-36`.

**Where it strains**

- Hard-delete escape hatches inside an event-sourced, bi-temporal store —
  `theloom/operations/inference.py:352-368`, `theloom/operations/extraction.py:293`,
  `:307`.
- `extraction-rollback` reports counts that hide the failures behind them —
  `theloom/operations/extraction.py:291-310`, with `deletedLinks` hard-coded to 0
  at `:315`.
- `stale-beliefs` cannot distinguish "never evaluated" from "unparseable
  timestamp" — `theloom/operations/epistemic.py:50-58`.
- Machinery decoded from observations disappears silently when malformed —
  `theloom/operations/inference.py:251-294`.
- Whole-graph scans and per-entity round trips in the analytical handlers —
  `theloom/operations/inference.py:384-408`, `:624`.

#### 2.11 `theloom/operations/` (3 of 3) — reasoning and assurance

The handler modules past plain CRUD. `semantic.py` owns the embedding lifecycle
and the retrieval/discovery commands; `symbolic.py` is seven one-line adapters
over the algebra engine; `solve.py` routes a natural-language problem through an
LLM into those adapters; `synthesis.py` is the nine Plan-Traverse-Realize
commands including read-only cross-graph merged views; `verification.py` is the
guard/invariant/capability suite plus the sandbox mutation-trace replayer;
`work_memory.py` is `record-outcome`, the write half of cross-session memory.

*Key files:* `theloom/operations/semantic.py` (965 lines), `verification.py`
(641), `synthesis.py` (626), `solve.py` (387), `work_memory.py` (175).

**What must stay true**

- One retrieval binding backs every semantic read in the group —
  `theloom/operations/semantic.py:144-165`.
- Embedding is opt-in and content-hash idempotent; a failure is recorded on the
  entity, never raised — `theloom/operations/semantic.py:332-344`.
- Graph-mutating discovery and repair commands default to dry run —
  `theloom/operations/semantic.py:492`, `:903`.
- `resolve-gaps` never duplicates an existing edge in either direction —
  `theloom/operations/semantic.py:940-945`.
- Every similarity in this module is `1/(1+L2)`, not cosine —
  `theloom/operations/semantic.py:7-13`.
- Superseded and deprecated entities keep their vectors but must not anchor a
  synthesis — `theloom/operations/synthesis.py:118-121`.
- `validate-mutation-trace` never touches the target graph —
  `theloom/operations/verification.py:578-590`, `:639-641`.
- `constrained-generate` is seeded, never wall-clock —
  `theloom/operations/verification.py:507-520`.
- `record-outcome` writes nothing on a bad citation and cites each entity once —
  `theloom/operations/work_memory.py:103-123`.

**Where it strains**

- Soft-fail commands opt out of the exit-code half of the error contract —
  `theloom/operations/symbolic.py:3-7`.
- A hard delete used as saga compensation inside an event-sourced store —
  `theloom/operations/work_memory.py:166-169`.
- An unknown invariant name is a hard error in one command and a silent skip in
  another — `theloom/operations/verification.py:216-220` vs `:307-309`.
- `check-consistency` and `list-guard-violations` run different guard sets —
  `theloom/operations/verification.py:145-161` vs `:378-384`.
- Declared `entityType` filters the handler never applies —
  `theloom/operations/semantic.py:289` vs `:784-825`.
- Discovery commands cost one vector query per candidate entity —
  `theloom/operations/semantic.py:743-750`, `:797`.
- `constrained-generate` commits entities but silently drops every relation —
  `theloom/operations/verification.py:550-556`.

#### 2.12 `theloom/composites/` (1 of 2) — reconnaissance and discovery

Eight one-call commands that bundle many internal operations into a single
structured answer, plus `framework.py`, the shared runner every composite is
built on. Each module declares an input model, resolves a store, runs a fixed
ordered list of named sections through `run_composite`, and returns an envelope
carrying per-section data, wall-clock timing and error text.

*Key files:* `theloom/composites/framework.py`, `far_analogy_retrieval.py`,
`creativity_loop.py`, `enrichment_crawl.py`, `gap_fill_cycle.py`.

**What must stay true**

- `time_section` never raises: every section outcome is a three-key result, and
  a non-null error always accompanies `data: None` —
  `theloom/composites/framework.py:42-61`.
- `framework.py` imports nothing from `theloom`, preventing a layering leak —
  `theloom/composites/framework.py:15-20`.
- Exactly two composites in this group write to the graph, and both write through
  `create_relation` — `theloom/composites/enrichment_crawl.py:395-408`,
  `gap_fill_cycle.py:168-181`.
- `enrichment-crawl` defaults to a dry run, never infers a causal relation type,
  and proposes each unordered pair at most once —
  `theloom/composites/enrichment_crawl.py:199`, `:141-161`, `:318-323`.
- A skipped semantic-consistency check must not veto a gap-fill commit —
  `theloom/composites/gap_fill_cycle.py:228`, `:236-240`.
- `creativity-loop` terminates on evidence, not a fixed cycle count, and never
  mutates the graph — `theloom/composites/creativity_loop.py:375-381`.

**Where it strains**

- Three incompatible top-level result shapes across one package —
  `graph_reconnaissance.py:161`, `analogy_transfer.py:60`,
  `gap_fill_cycle.py:271`.
- Blanket exception capture buys resilience and costs diagnosability —
  `theloom/composites/framework.py:53-56`.
- PageRank scores are published under the key `eigenvector` —
  `theloom/composites/graph_reconnaissance.py:132-138`.
- `gap-fill-cycle`'s `commitThreshold` gate is elaborate machinery over a
  constant zero — `theloom/composites/gap_fill_cycle.py:89-104`.
- `explore-frontier` maps advice back to regions by Python object identity —
  `theloom/composites/explore_frontier.py:214-222`.

#### 2.13 `theloom/composites/` (2 of 2) — analysis bundles and the self-improve cycle

Eleven more one-call commands. Seven are read-only analysis bundles
(`structural-survey`, `semantic-landscape`, `influence-map`,
`multi-graph-landscape`, `provenance-audit`, `verified-extract`,
`simulate-change`); four break the mould — `propose-entities` forwards to the
proposer, `hypothesis-engine` ranks gap-driven hypotheses, `reflect` is the
deterministic reading half of work memory, and `self-improve` chains
reconnaissance → capability check → propose → simulate → rank → apply into one
governed, human-in-the-loop-by-default cycle.

*Key files:* `theloom/composites/self_improve.py` (605 lines),
`simulate_change.py` (313), `hypothesis_engine.py` (415), `reflect.py` (381),
`influence_map.py` (208).

**What must stay true**

- `simulate-change` never mutates the graph it is asked about; it clones —
  `theloom/composites/simulate_change.py:100-110`, `:240-249`.
- `self-improve` writes nothing unless `autoApply` is explicitly true —
  `theloom/composites/self_improve.py:341-348`.
- A relation-batch failure hard-deletes the entity that was just applied —
  `theloom/composites/self_improve.py:439-466`.
- Proposals that degrade or could not be simulated are dropped before ranking —
  `theloom/composites/self_improve.py:285-294`, `:320-336`.
- A reflection replaces the previous `usage_status` observation and retracts it
  when no verdict is reached — `theloom/composites/reflect.py:267-285`.
- `preferred` requires both a positive decayed score and `minCorroboration`
  useful citations — `theloom/composites/reflect.py:225-233`.
- Reflection lessons are written through the gated `update-entity` operation,
  never straight to the store — `theloom/composites/reflect.py:352-365`.

**Where it strains**

- `hypothesis-engine`'s overall score ignores its own sub-scores and depends on a
  key nothing writes — `theloom/composites/hypothesis_engine.py:84-132`, `:352`.
- Declared section order is not execution order in the eager-section composites
  — `theloom/composites/multi_graph_landscape.py:74-109`.
- Best-effort temp-graph cleanup can leave `sim-<uuid>` graphs behind —
  `theloom/composites/simulate_change.py:310-313`.
- `reflect` reads like a report but mutates entity observations by default —
  `theloom/composites/reflect.py:315`, `:352-365`.
- `centralityDelta` reports raw degree, not a centrality measure —
  `theloom/composites/simulate_change.py:65-68`, `:147-164`.
- `provenance-audit` scans the whole graph to answer a single-entity question —
  `theloom/composites/provenance_audit.py:98-110`.

#### 2.14 `theloom/graph/` — in-memory graph algebra

Hydrates wire documents into a small insertion-ordered directed multigraph
(`LoomGraph`) and runs the pure structural analyses on top of it: centrality and
components, cycle detection and feedback-loop classification, shortest and
bounded all-simple paths, motif mining, subgraph filters, and the parsers that
read structured facts back out of observation strings. Its defining constraint is
determinism — enumeration order and tie-breaking are part of the observable
command output, so most algorithms are written out longhand rather than delegated.

*Key files:* `theloom/graph/hydrate.py`, `cycles.py` (the largest and only
store-aware module), `analytics.py`, `motifs.py`, `metadata.py`.

**What must stay true**

- Hydration drops dangling relations, so no edge can reference an absent node —
  `theloom/graph/hydrate.py:118`.
- Neighbor iteration is deduplicated and order-fixed at IN-then-OUT —
  `theloom/graph/hydrate.py:73-96`.
- Loop polarity is the parity of negative edges, with missing polarity read as
  positive — `theloom/graph/cycles.py:258-266`.
- PageRank converges to the stated tolerance or raises rather than returning
  provisional scores — `theloom/graph/analytics.py:66-68`.
- Observation parsing is total: malformed input yields nulls, never an exception
  — `theloom/graph/metadata.py:38-53`.

**Where it strains**

- An untyped `RuntimeError` escapes a codebase built on typed error codes —
  `theloom/graph/analytics.py:68`.
- The pure algorithm layer writes to the store, and a store-less persist request
  lies about it — `theloom/graph/cycles.py:20`, `:293-334`.
- Two cycle enumerators with different completeness guarantees and no signposting
  — `theloom/graph/cycles.py:31-55` vs the Johnson circuits path.
- Parallel edges are counted, collapsed, or duplicated depending on which
  algorithm you ask — `theloom/graph/analytics.py:28`, `:50-51` vs
  `theloom/graph/hydrate.py:73-85`.
- Recursive DFS in half the group with no depth guard —
  `theloom/graph/analytics.py:119-142`, `theloom/graph/cycles.py:38-99`.

#### 2.15 `theloom/algebra/` — semirings and routing

The pure computational core for weighted traversal: five semirings as frozen
`(zero, one, plus, times)` records, weight extractors that turn a relation's
strength label into a semiring element, one shared recursive DFS engine, and on
top of that a relation-type registry sorting types into structural/epistemic/
causal categories, a table of six cross-category morphisms, a query router, a
segmented executor and a level-synchronous metapath engine. No CLI, no model, no
I/O beyond an optional lazy adjacency read.

**What must stay true**

- Traversal is a backtracking DFS, not Bellman-Ford: value and path are decoupled
  — `theloom/algebra/core.py:191-207`.
- Adjacency emission order is part of the public contract; ties keep first
  discovery — `theloom/algebra/core.py:141-148`.
- Relation categorization is total, with causal as the open-world default —
  `theloom/algebra/routing.py:44-53`.
- Approximate morphisms are exactly the tropical/viterbi pair and are labelled as
  such — `theloom/algebra/routing.py:92-103`.
- A missing source entity yields an empty result map rather than an error —
  `theloom/algebra/core.py:234-235`, `:265-266`.

**Where it strains**

- Metapath expansion has no cycle guard and no frontier cap —
  `theloom/algebra/routing.py:526-528`, `:586-596`.
- Missing-morphism handling is inconsistent across the three consumers —
  `theloom/algebra/routing.py:218-220`, `:548-551` vs `:335-337`.
- Two semiring resolvers with deliberately divergent semantics —
  `theloom/algebra/core.py:85-105`.
- `execute_routing_plan` accepts a mode it cannot honour for segmented plans —
  `theloom/algebra/routing.py:448-451`.

#### 2.16 `theloom/analysis/` — the creativity kernel

A store-free, IO-free library of scoring and search algorithms that turn an
already-hydrated graph into cross-domain mappings, CWSG analogy transfers with
novel-entity proposals, concept slippages, approximate subgraph matches,
Weisfeiler-Leman component signatures, far-analogy candidate pairs, and
interestingness/confidence/adaptability scores. Each module implements one named
piece of literature with the formula written out in its docstring.

*Key files:* `theloom/analysis/cwsg.py`, `crossdomain.py`, `slippage.py`,
`absence_surprise.py`, `adaptability.py`.

**What must stay true**

- Cross-domain mapping is strictly one-to-one —
  `theloom/analysis/crossdomain.py:198-219`.
- Novel transfer endpoints are `__NOVEL__`-prefixed placeholders, never graph ids
  — `theloom/analysis/cwsg.py:31`, `:110-119`.
- Only relations attached to the matched relational core transfer —
  `theloom/analysis/cwsg.py:68`, `:81-85`.
- Temperature is clamped to `[0,1]` and lowers the slippage threshold
  monotonically — `theloom/analysis/slippage.py:37`, `:54-56`.
- The WL hashing primitive is shared with reification to stay bit-identical —
  `theloom/analysis/component_signatures.py:32`.
- Pattern validation runs to completion and raises before any search begins —
  `theloom/analysis/isomorphism.py:31-55`, `:132-134`.

**Where it strains**

- Timeout budgets are advertised in three modules and enforced in one —
  `theloom/analysis/crossdomain.py:18`, `slippage.py:28` vs
  `isomorphism.py:189-193`.
- A timed-out subgraph search is indistinguishable from a complete one —
  `theloom/analysis/isomorphism.py:242-265`.
- Oversized input raises in one module and is silently truncated in another —
  `theloom/analysis/crossdomain.py:167-174` vs `slippage.py:238`.
- Slippage failures are swallowed whole inside CWSG —
  `theloom/analysis/cwsg.py:152-166`.
- Two `farAnalogyScore` fields carry incomparable scales —
  `theloom/analysis/component_signatures.py:217-227` vs
  `sliced_wasserstein.py:105-111`.
- Structural helpers are duplicated rather than shared across the layer —
  `theloom/analysis/absence_surprise.py:54-91` vs `slippage.py:59-70`.

#### 2.17 `theloom/semantic/` — the meaning layer

The engine room: text into vectors (`embed.py`), the single definition of
"nearest" and the single retrieval path (`search.py`), the ordering and grouping
of results (`ranking.py`), the definition of "needs embedding" and how a
status/vector divergence is repaired (`embedding_state.py`), and on top of that
the duplicate gate and the entity proposer. It is deliberately a dependency leaf:
imported by operations, composites, documents, analysis, exploration and viz —
never the reverse.

**What must stay true**

- Every vector is L2-normalized before it leaves the embedder —
  `theloom/semantic/embed.py:82-88`.
- Documents and queries are embedded with different task prefixes and no caller
  can bypass it — `theloom/semantic/embed.py:28-29`, `:90-99`.
- One cosine-to-score conversion exists, and every hit carries the raw cosine
  alongside it — `theloom/semantic/search.py:58-65`, `:140-151`.
- Vector search returns only active entities unless a caller explicitly opts out
  — `theloom/semantic/search.py:97`, `:136`.
- `needs_embedding` is the single skip predicate: completed status plus a
  matching hash — `theloom/semantic/embedding_state.py:57-61`.
- The dedup gate matches within one entity type but across all five statuses —
  `theloom/semantic/deduplication_gate.py:25`, `:117-125`.
- The entity proposer is read-only — `theloom/semantic/entity_proposer.py:80-88`,
  `:143-152`.
- MMR always keeps the top-ranked row and returns rows in selection order —
  `theloom/semantic/ranking.py:216-238`.

**Where it strains**

- Step 4 of the proposal pipeline filters nothing —
  `theloom/semantic/entity_proposer.py:554-576`.
- The LLM reasoning strategy is enabled by default and unreachable in practice —
  `theloom/semantic/entity_proposer.py:108`, `:121-123`.
- Violation semantics travel as prose and are recovered by regex —
  `theloom/semantic/entity_proposer.py:63-66`, `:229-231`.
- A strict `min_score` escalates the one retrieval core into a full index scan —
  `theloom/semantic/search.py:133-134`, `:154-156`.
- Hard-coded type and status lists shadow the domain model —
  `theloom/semantic/entity_proposer.py:44-61`, `deduplication_gate.py:25`.
- The embedder singleton outlives a config change —
  `theloom/semantic/embed.py:102-118`.

#### 2.18 `theloom/documents/` — ingestion

Turns external artifacts — a file, a directory, a raw string, a URL — into
embedded, searchable chunk rows that live inside the same FalkorDB instance as
the graph. It owns the whole pipeline: an extension allowlist and per-format
parsers normalising every input into one block shape, a three-phase size-aware
chunker with sentence overlap and an atomic-block escape hatch, an SSRF-hardened
fetcher, the declared chunk-metadata shape, and event-sourced persistence into a
dedicated per-prefix chunk graph.

**What must stay true**

- Chunk writes are event-sourced through the store's shared commit primitive —
  `theloom/documents/chunkstore.py:103`, `:207-222`.
- Chunks live in one per-prefix chunk graph, global across knowledge graphs —
  `theloom/documents/chunkstore.py:56`, `:69-78`.
- Chunk event payloads carry coordinates, never chunk text —
  `theloom/documents/chunkstore.py:232-251`.
- `sourceId` is a deterministic sha256 prefix of the resolved path, URL or caller
  id — `theloom/documents/ingestion.py:51-57`.
- Reingest preserves chunk identity and skips unchanged chunks —
  `theloom/documents/ingestion.py:316-361`.
- Every fetch hop requires all resolved addresses to be globally routable —
  `theloom/documents/ssrf.py:39-80`.
- Embedding failure never blocks chunk persistence; the reason is stored on the
  chunk — `theloom/documents/ingestion.py:60-69`, `:143-145`.

**Where it strains**

- First ingest appends blindly while reingest diffs, so re-ingesting a file
  duplicates its chunks — `theloom/documents/ingestion.py:141-157` vs `:305`.
- The SSRF guard resolves DNS separately from the connection it protects —
  `theloom/documents/ssrf.py:74-80` vs `:92-95`.
- The response-size ceiling is checked after the whole body is in memory —
  `theloom/documents/ssrf.py:95`, `:106-109`.
- `ingest_url` classifies SSRF failures by message prefix, contradicting the
  module's own error taxonomy — `theloom/documents/ingestion.py:250-254` vs
  `:36-39`.
- Document-wide reads are capped at 1,000 chunk rows before filtering —
  `theloom/documents/ingestion.py:271`, `:305`.
- One code fence exempts an entire document from `maxSize` —
  `theloom/documents/chunker.py:137`, `:148` vs `:165-166`.

#### 2.19 `theloom/extraction/` — artefacts into graph content

The dominant path is deterministic, LLM-free codebase extraction: tree-sitter
parses each source file into file/class/function/variable records plus
containment, call, inheritance and import edges; a whole-project second pass
joins what no single-file parse can resolve; a third pass links Markdown docs
into the code they name; one module owns every name, observation prefix and
evidence string those passes write; an incremental path replays a git diff over
an existing graph, superseding rather than deleting. A second, unrelated path
does LLM document extraction. The two share only the append-only run record.

*Key files:* `theloom/extraction/treesitter.py` (1,386 lines), `resolution.py`,
`doclinks.py`, `encoding.py`, `codebasediff.py`.

**What must stay true**

- An incremental update supersedes entities; it never deletes them —
  `theloom/extraction/codebasediff.py:462-471`.
- The structural diff only ever retracts edges structural extraction itself emits
  — `theloom/extraction/codebasediff.py:78-88`, `:266-282`.
- An update that looks like a collapse is refused rather than applied —
  `theloom/extraction/codebasediff.py:345-360`, `:522-528`.
- A callee that does not resolve to exactly one reachable target produces no edge
  — `theloom/extraction/resolution.py:431-451`.
- Line numbers are 0-based in code and 1-based in the graph, and the round trip
  is the identity — `theloom/extraction/encoding.py:17-23`, `:117-134`.
- Extraction output is deterministic for a given tree —
  `theloom/extraction/treesitter.py:1203-1211`.
- Self-model update refuses any repository that is not The Loom —
  `theloom/extraction/selfmodel.py:30-62`.
- Chunk content is fed to the model as data, never as instructions —
  `theloom/extraction/pipeline.py:80`, `:166`.
- A single document contributes at most 50 references, and the drop is reported —
  `theloom/extraction/doclinks.py:74`, `:233-244`.

**Where it strains**

- The incremental update is incremental only in its writes — it re-extracts the
  whole project — `theloom/extraction/codebasediff.py:517-519`, `:213`.
- Deduced edges enter a graph whose consumers treat every edge as fact —
  `theloom/extraction/resolution.py:77-141` (the builtin-name curation added after an observed 288-caller `len()` incident at `:71-76`).
- Extraction run records live outside the graph's transactional, bi-temporal
  history — `theloom/extraction/runstore.py:28-29`, `:73-84`.
- The file-collection rule exists twice and the copies must agree —
  `theloom/extraction/codebasediff.py:115-131` vs
  `theloom/extraction/treesitter.py:1089-1124`.
- The document pipeline reports zero errors by construction —
  `theloom/extraction/pipeline.py:96-105`.
- The TypeScript and JavaScript extractors are near-duplicate walkers —
  `theloom/extraction/treesitter.py:587-690` vs `:706-800`.

#### 2.20 `theloom/exploration/` — foraging signals

The library behind `explore-frontier`. It turns a graph's connected components
into ranked "where should I look next" recommendations by computing four
independent normalized signals — age staleness, bridging potential, coverage gap
and a UCB1 exploration bonus — fusing them with a renormalizing weighted average,
layering a marginal-value-theorem patch-leaving policy on top, and running six
anti-pattern guards over aggregated state.

**What must stay true**

- Every exploration signal score is clamped to `[0,1]` —
  `theloom/exploration/composite_signals.py:89`, `:53`.
- Absent signals are dropped and weights renormalized, never treated as zero —
  `theloom/exploration/composite_signals.py:70-88`.
- Region identity is the smallest entity id in sorted order —
  `theloom/exploration/exploration_state.py:89-97`.
- Region state is derived at query time from entity state, never persisted —
  `theloom/exploration/exploration_state.py:153-193`.
- Missing evidence scores as maximally explorable, not as zero —
  `theloom/exploration/age_staleness.py:92-93`.
- `run_guards` reports why it produced nothing: skipped versus tier availability
  — `theloom/exploration/guards.py:76-82`, `:467-468`.

**Where it strains**

- Three incompatible region-identity schemes coexist —
  `theloom/exploration/exploration_state.py:89-97` vs `guards.py:152-161` vs
  `guards.py:374`.
- The stateless-by-design store leaves UCB and MVT informationless within a run —
  `theloom/exploration/exploration_state.py:8-21`.
- `BridgingPotential` collapses to a binary constant under its documented usage —
  `theloom/exploration/bridging_potential.py:70-83`.
- `detect_comfort_zone` silently switches from region-scoped to graph-wide
  counting — `theloom/exploration/guards.py:250-263`.

#### 2.21 `theloom/reification/` — structural fingerprints

The one shared implementation of Weisfeiler-Leman ego fingerprinting over a
hydrated graph. Each node is reduced to a short hash of its rooted neighborhood
up to a bounded depth, so nodes whose local structure looks alike collapse to the
same digest. `reify-patterns`, the entity proposer and component signatures all
import from here, so their fingerprints stay bit-identical by construction.

**What must stay true**

- Fingerprints are invariant to adjacency ordering —
  `theloom/reification/fingerprint.py:49-53`.
- Depth is clamped at both public entry points —
  `theloom/reification/fingerprint.py:93`, `:133`.
- The package is pure: it reads a hydrated copy and never mutates or persists —
  `theloom/reification/fingerprint.py:10`, `:15-18`.
- Memo keys carry depth, so one cache is safe across mixed-depth calls —
  `theloom/reification/fingerprint.py:57`, `:74-78`.
- Fingerprints read only entity and relation types, never names or content —
  `theloom/reification/fingerprint.py:38`, `:60`.

**Where it strains**

- Direction-aware at depth 1, direction-blind beyond it —
  `theloom/reification/fingerprint.py:63-79`.
- A group description reports one arbitrary member, not the group —
  `theloom/reification/fingerprint.py:139-148`.
- A 64-bit truncated digest trades compact keys for silent collision merging —
  `theloom/reification/fingerprint.py:26-27`, `:137-148`.

#### 2.22 `theloom/symbolic/` — computer algebra

SymPy behind a single total function: `core.run(operation, params, timeout)`
looks a string operation name up in a 21-entry dispatch table, runs the handler
under a `SIGALRM` watchdog, and returns a JSON-serializable envelope instead of
raising. It owns all expression parsing, all SymPy-object formatting, and a small
chain interpreter. A pure computation leaf — no graph state, no store, no imports
from the rest of the package.

**What must stay true**

- `core.run` never raises for main-thread callers —
  `theloom/symbolic/core.py:1001-1022`.
- The watchdog timeout is clamped to 1–120s and restores prior signal state —
  `theloom/symbolic/core.py:1008-1009`, `:1023-1025`.
- One alarm covers an entire chain, not each step —
  `theloom/symbolic/core.py:1016`, `:931-932`.
- Handler results cross the boundary as strings, never as SymPy objects —
  `theloom/symbolic/core.py:61-75`.

**Where it strains**

- The never-raises guarantee has a hole outside the main thread —
  `theloom/symbolic/core.py:1009`, `:1015`.
- `sympify` on caller-controlled strings assumes a trusted caller —
  `theloom/symbolic/core.py:48`, `:772-776`, `:918`.
- `latex_result` does not always contain LaTeX —
  `theloom/symbolic/core.py:533`, `:577` vs `:117`.
- A chain cannot carry a `verify` step — `theloom/symbolic/core.py:948` vs
  `:212-219`.
- The module documents seven operations while the table registers twenty-one —
  `theloom/symbolic/core.py:3` vs `:969-991`.

#### 2.23 `theloom/synthesis/` — graph into prose

The Plan-Traverse-Realize pipeline: pick a query-relevant subgraph, decompose the
question, group into ordered regions, walk those regions systematically or
adaptively while attaching confidence, source passages and an append-only
provenance trail, topologically order each region, and render it as narrative,
outline, evidence map, causal chain, proposal or raw. `fidelity.py` then grades
the produced text against the graph it came from. A second, unrelated subsystem
lives here too: a seeded generator plus a CEGIS loop over graph structures.

**What must stay true**

- Synthesis output is fully deterministic when no LLM is configured —
  `theloom/synthesis/llm.py:215-218`, `realizer.py:318-321`.
- `mulberry32` is bit-exact 32-bit, so a seed determines the candidate graph
  exactly — `theloom/synthesis/generator.py:28-60`.
- CEGIS verification touches no store; only a successful commit does —
  `theloom/synthesis/cegis.py:211-257`.
- The CEGIS loop always terminates — `theloom/synthesis/cegis.py:382-418`.
- Provenance is append-only and sealed at finalize —
  `theloom/synthesis/traverser.py:42-86`.
- Selection depth and breadth are hard-capped regardless of caller input —
  `theloom/synthesis/selector.py:166-172`.
- Only document-provenance entities can resolve to a source chunk, and a miss is
  not an error — `theloom/synthesis/links.py:32-40`.

**Where it strains**

- `quick_verify` falls back to regex-matching violation prose —
  `theloom/synthesis/cegis.py:278-303`.
- Two fidelity modes report the same score field with incomparable semantics —
  `theloom/synthesis/fidelity.py:220-274` vs `:277-348`.
- LLM and parse failures are swallowed without a signal —
  `theloom/synthesis/decomposer.py:75-85`, `realizer.py:311-336`.
- `relationCount` counts relations whose far endpoint was dropped —
  `theloom/synthesis/selector.py:126-129`, `:189-191`.
- The package docstring disclaims the CEGIS subsystem the package contains, and
  `cegis.py` inverts the package's dependency direction —
  `theloom/synthesis/__init__.py:4-5`, `cegis.py:34-35`.

#### 2.24 `theloom/verification/` — the rule layer

A store-agnostic library of predicates deciding whether a graph, or a single
proposed mutation, keeps the model's structural promises. `checks.py` holds the
read-side guards, the five builtin invariants and the shared three-colour DFS
cycle detector; `guards.py` holds the mutation gate that create-entity and
create-relation call before writing; `metrics.py` holds the coverage and coupling
generators shared by the capability command and the DSL; `capability_spec.py`
layers a fluent DSL; `propagation.py` implements AC-3 arc consistency over the
19-value entity-type domain.

**What must stay true**

- Guards abstain when a field is absent rather than reporting a violation —
  `theloom/verification/checks.py:42-98`.
- The polarity partition is enforced on write and mirrored on read from one
  message — `theloom/verification/guards.py:64-71`,
  `theloom/verification/checks.py:24-28`.
- Entity gates warn; relation gates block —
  `theloom/verification/guards.py:41-52` (warning strings appended to observations) versus `:55-78` (blocking error strings).
- Retracted entities read back but cannot become relation endpoints —
  `theloom/verification/guards.py:81-101`.
- `find_cycle_nodes` never leaves the supplied node set —
  `theloom/verification/checks.py:187-210`.
- The AC-3 worklist is LIFO and the pop order is part of the wire contract —
  `theloom/verification/propagation.py:104`.
- The shared capability generators live here so operations imports downward —
  `theloom/verification/metrics.py:1-9`.

**Where it strains**

- The mutation gate and the read-side guards enforce partly disjoint rule sets —
  `theloom/verification/checks.py:172-177` vs `guards.py:55-78`.
- A duplicate-name warning fires on a partial, case-insensitive match —
  `theloom/verification/guards.py:46-51`.
- An unrecognised coupling metric silently falls back to degree centrality —
  `theloom/verification/metrics.py:65`.
- The GraphSpec DSL named in the docstrings has no implementation —
  `theloom/verification/checks.py:5-8`.
- Cycle detection recurses, so depth is bounded by the Python stack —
  `theloom/verification/checks.py:191-210`.
- Every capability check re-lists the entire graph —
  `theloom/verification/capability_spec.py:36-44`.

#### 2.25 `theloom/viz/` — bundle, page, server

Turns a live graph into a shippable payload for the Tapestry SPA. It chooses
which slice to show (full / ego / causal / typed / search, optionally bounded to
a system-time `asOf`), optionally attaches three analysis sections, validates the
payload against a versioned wire contract, and emits it through one of three
transports: raw JSON, a self-contained single-file HTML page carrying the
committed SPA build, or a read-only FastAPI service the same SPA talks to in live
mode. Almost nothing is computed here — only the 2-D projection and the degree
truncation.

**What must stay true**

- Every bundle leaves the assembler as a validated dump —
  `theloom/viz/bundle.py:146-165`.
- Injected bundle JSON can never terminate the template script block —
  `theloom/viz/html.py:33`.
- A missing or unbuilt frontend template fails as a typed `ConfigError` —
  `theloom/viz/html.py:28-44`.
- Live-mode HTTP status is a typed-code table lookup, never prose matching —
  `theloom/viz/serve.py:28-35`.
- Degree truncation is deterministic and always disclosed —
  `theloom/viz/bundle.py:66`, `:72-76`.
- Truncated and scoped bundles contain no dangling relation endpoints —
  `theloom/viz/bundle.py:71`, `theloom/viz/scope.py:100-102`.
- Live mode is read-only: every registered route is a GET —
  `theloom/viz/serve.py:108-216`.
- `asOf` bundles stamp `temporalScope=current` on the sections that were not
  recomputed — `theloom/viz/bundle.py:128-135`.

**Where it strains**

- The live server is unauthenticated and its bind host is caller-supplied —
  `theloom/viz/serve.py:45-46`, `:105-108`.
- The static path writes to a caller-controlled filesystem location —
  `theloom/viz/html.py:55-58`.
- `asOf` bounds entities, relations and events but leaves analytics and semantics
  at the present — `theloom/viz/bundle.py:128-135` vs `analytics.py:56`.
- `asOf` is validated by date parsing but applied by string comparison —
  `theloom/viz/bundle.py:107` vs `theloom/viz/temporal.py:15`.
- Search scope silently drops the non-active entities the rest of the bundle
  ships — `theloom/viz/scope.py:90-96`.

### C. Tapestry — the visualization front end

#### 2.26 `tapestry/` (1 of 2) — the data contract and the live smoke

Three artifacts that decide what the SPA is built from and how it is proved:
`fixtures/dev-bundle.json`, a verbatim export snapshot the dev build fetches,
seven Playwright specs inject, one vitest validates against the JSON schema and
one Python test round-trips through the Pydantic model; `e2e-live/live.spec.ts`,
the counterpart proof for the live data path; and `package-lock.json`, pinning
the toolchain both halves run on.

**What must stay true**

- The dev fixture is an exact round-trip of the bundle assembler's output —
  `tapestry/fixtures/dev-bundle.json:1-14`.
- Optional entity fields are omitted rather than nulled; a ten-key core is always
  present — `tapestry/fixtures/dev-bundle.json:16-34`.
- Exported relations carry polarity only for causal types; strength and evidence
  are always present — `tapestry/fixtures/dev-bundle.json:224-233`.
- Event payloads carry `previous` for in-place mutations only —
  `tapestry/fixtures/dev-bundle.json:640-680`.
- The live smoke pins four server-only behaviors and nothing else —
  `tapestry/e2e-live/live.spec.ts:10-28`.

**Where it strains**

- The semantic surface is proved only against a frozen fixture, never a live
  server — `tapestry/fixtures/dev-bundle.json:1459-1511`.
- The bundle envelope is validated exactly; entity and relation records are
  unvalidated pass-through — `tapestry/fixtures/dev-bundle.json:16-34`.
- Mixed snake_case and camelCase inside a single wire record —
  `tapestry/fixtures/dev-bundle.json:24-33`.
- `previousVersionId` points at the record's own id, so a version chain is a
  self-loop — `tapestry/fixtures/dev-bundle.json:128` vs `:144-146`.

#### 2.27 `tapestry/` (2 of 2) — build and contract toolchain

No application code: the build, contract and verification toolchain that turns
the SPA into the single artifact the Python distribution ships. `npm run build`
is a three-stage `&&` chain — typecheck the two composite TypeScript projects,
inline the whole app into one HTML file, then assert the data sentinel survived
bundling before copying the file into the Python package.
`schema/bundle.schema.json` is the generated wire contract checked into the
frontend so JavaScript tooling and Python tests agree. Two Playwright configs
partition end-to-end verification into a static `file://` suite and a live suite.

**What must stay true**

- `npm run build` is a three-stage gate: typecheck, bundle, emit —
  `tapestry/package.json:8`.
- No template is emitted unless the data sentinel survived bundling —
  `tapestry/scripts/emit-template.mjs:4-8`.
- `theloom/viz/static/tapestry.html` is the only artifact crossing from the Node
  workspace into the Python package — `tapestry/scripts/emit-template.mjs:8`.
- The bundle envelope is closed while entity and relation payloads stay open —
  `tapestry/schema/bundle.schema.json:319`, `:330`, `:338`.
- Vitest never loads the Playwright suites — `tapestry/vite.config.ts:10-12`.

**Where it strains**

- The sentinel guard checks presence, not uniqueness, while the consumer replaces
  every occurrence — `tapestry/scripts/emit-template.mjs:4` vs
  `theloom/viz/html.py:34`.
- The typecheck gate excludes the very files that configure the gate —
  `tapestry/tsconfig.app.json:24`, `tsconfig.node.json:22`.
- `npm run e2e` verifies the committed template, not the working tree —
  `tapestry/package.json:10`.
- Chromium is the only browser the shipped artifact is ever exercised in —
  `tapestry/playwright.config.ts:12-17`.

#### 2.28 `tapestry/e2e/` — the browser acceptance suite

Seven Playwright specs that drive the single self-contained HTML artifact
`loom visualize` emits, re-created at `beforeAll` time from two committed inputs
and opened over `file://`. They walk all five view tabs and their signature
interactions, run axe-core over every panel in both themes, pin the WAI-ARIA
tablist behaviour and the focus-trapped help dialog, prove node repositioning and
post-drag click suppression, pin the export filename convention, and cover saved
views end to end.

**What must stay true**

- The suite renders through the same substitution as `theloom/viz/html.py` —
  `tapestry/e2e/smoke.spec.ts:18-21`.
- Each spec owns a distinct temp artifact so parallel specs never clobber each
  other — `tapestry/e2e/smoke.spec.ts:15`, `a11y.spec.ts:21`.
- The accessibility gate is zero serious or critical violations, by construction
  — `tapestry/e2e/a11y.spec.ts:43-45`.
- A drag never registers as a click — `tapestry/e2e/drag.spec.ts:83-85`,
  `:122-133`.
- The help dialog is a real focus-trapped modal that restores focus on close —
  `tapestry/e2e/help.spec.ts:38-54`.

**Where it strains**

- Seven copies of the artifact-building `beforeAll` re-implement the Python
  renderer — `tapestry/e2e/smoke.spec.ts:17-22` and five siblings.
- Wall-clock settle timeouts sit beside deterministic waiting —
  `tapestry/e2e/smoke.spec.ts:89`, `:142`, `:176`, `:208`.
- Precise fixture counts make the suite readable and brittle at once —
  `tapestry/e2e/smoke.spec.ts:232-249`.
- The suite exercises only the static `file://` artifact — every spec navigates
  to it (`tapestry/e2e/smoke.spec.ts:43`).

#### 2.29 `tapestry/src` — app shell and help overlay

`main.tsx` is the entire bootstrap: one root render that wraps the app in the
bundle provider, so no component below ever renders without a bundle in hand.
`App.tsx` is chrome and router in one component — a fixed header carrying the
brand mark, the bundle's identity and counts, an ARIA tablist of the five views,
a live-server chip with graph switcher and refresh, a help trigger and a theme
radiogroup — plus four mount-time effects wiring the app to browser globals.
`HelpOverlay.tsx` is the shortcut sheet, a real focus-trapped modal whose open
state the shell owns.

**What must stay true**

- Nothing in the app renders before a bundle exists —
  `tapestry/src/main.tsx:6-10`, `tapestry/src/App.tsx:170`.
- Hash restore runs before the hash writer's first write —
  `tapestry/src/App.tsx:233-258`.
- The URL hash is replaced, never pushed — `tapestry/src/App.tsx:248-258`.
- Exactly one view component is mounted at a time, keyed for a clean remount —
  `tapestry/src/App.tsx:395-407`.
- The OS colour-scheme listener exists only while the theme is auto —
  `tapestry/src/App.tsx:261-268`.
- Help focus makes a round trip: into the dialog on open, back to the trigger on
  close — `tapestry/src/views/HelpOverlay.tsx:70-80`, `:129`.

**Where it strains**

- The shortcut sheet is a hand-maintained copy of bindings defined elsewhere —
  `tapestry/src/views/HelpOverlay.tsx:26-60`.
- Opening the modal does not suspend the app's global shortcuts —
  `tapestry/src/App.tsx:220`.
- Four of the five tabs point `aria-controls` at panels that are not in the DOM —
  `tapestry/src/App.tsx:307` vs `:395-407`.
- Narrow viewports drop the bundle's identity and provenance, not just decoration
  — `tapestry/src/App.css:445-461`.
- The shell carries ordering and focus invariants that no test pins —
  `tapestry/src/App.tsx:233-258`.

#### 2.30 `tapestry/src/design/` — the visual contract

One CSS custom-property token file defining two complete themes, a TypeScript
mirror of the model's 19 entity types plus the accessor that turns a type name
into a token reference, and the three-line bridge resolving the tri-state theme
setting to a concrete attribute on `<html>`. Everything downstream reads colour,
type ordering and typography through this layer, so a theme swap is a single
attribute write.

**What must stay true**

- Every entity type has a token in both themes —
  `tapestry/src/design/palette.ts:35-39`, `tokens.css:99-118`.
- `ENTITY_TYPES` mirrors the model's enum in enum order —
  `tapestry/src/design/palette.ts:1-27`, mirroring `theloom/model.py:56-75`.
- Entity identity is never encoded by colour alone —
  `tapestry/src/design/tokens.css:8-17`.
- Colour follows the entity, never a rank — marks are not repainted on filter —
  `tapestry/src/design/tokens.css:14-15`.
- `applyTheme` is the single switch point for the rendered theme —
  `tapestry/src/design/theme.ts:24-26`.

**Where it strains**

- Enum-to-token parity is asserted but unenforced by any test —
  `tapestry/src/design/palette.ts:1-27`.
- Token indirection versus hard-coded hex fallbacks in canvas views —
  `tapestry/src/design/tokens.css:2` vs the fallback tables in the views.
- Accessibility thresholds documented in prose, validated outside the repo —
  `tapestry/src/design/tokens.css:76`, `:178`.

#### 2.31 `tapestry/src/lib/` — the shared kernel

Everything the four sigma views and the shell need but no single view owns:
bundle acquisition and shape agreement across three delivery modes with a typed
load error; canvas interaction primitives (the pure click-vs-drag decision, the
Sigma wiring, the label reveal policy and wrapping renderers); export to SVG and
PNG under one filename convention; and app-shell affordances — the global
shortcut dispatcher, roving-tabindex math, and per-graph saved views.

**What must stay true**

- Every bundle-load failure raises a typed error naming its source —
  `tapestry/src/lib/data.ts:82-101`, `:69-77`.
- Live mode is detected by the parsed marker's shape, never by the sentinel
  literal — `tapestry/src/lib/live.ts:20-28`, `data.ts:59-63`.
- A load failure after a bundle is up keeps the data and reports the loaded graph
  — `tapestry/src/lib/BundleContext.tsx:99`.
- A view's click handlers must consume the drag latch before acting —
  `tapestry/src/lib/dragNodes.ts:155-159`, contract at `:26-30`.
- The normalization bbox is frozen for the drag's duration and cleared on release
  — `tapestry/src/lib/dragNodes.ts:95`, `:120`.
- Overriding the label renderer without also overriding the hover renderer
  double-draws labels — `tapestry/src/lib/nodeLabels.ts:193-201`.
- PNG export must call `sigma.refresh()` synchronously before reading the
  canvases — `tapestry/src/lib/exportSvg.ts:298-299`.
- The TS bundle type is pinned to the committed JSON schema in both directions —
  `tapestry/src/lib/schema.test.ts:171-227`.
- Keyboard shortcuts never fire while the user is typing or holding a modifier —
  `tapestry/src/lib/keyboard.ts:20-24`, `:32-33`.

**Where it strains**

- SVG export ignores the label reveal policy that governs the screen and the PNG
  — `tapestry/src/lib/exportSvg.ts:147`, `:210-211`.
- Both export paths are offered as WYSIWYG but omit every DOM-overlay decoration
  — `tapestry/src/lib/exportSvg.ts:24-30`.
- `fetchGraphs` is the one unguarded fetch, and its failure is swallowed into an
  empty list — `tapestry/src/lib/live.ts:31-35`.
- The shared kernel imports from one specific view, inverting the layering —
  `tapestry/src/lib/BundleContext.tsx:29`, `exportSvg.ts:34`.
- The pure/impure split leaves the impure edge entirely untested —
  `tapestry/src/lib/exportSvg.ts:261-326`.

#### 2.32 `tapestry/src/state/` — cross-view state and its URL projection

A single flat store holding the active view, theme, selection, filters, path
tool, isolated loop, the Chronicle scrubber triple and the brushed id set, with
one narrow setter per field; plus the module that turns a chosen subset of it
into a shareable location hash and back. No fetching, no rendering, no derived
selectors.

**What must stay true**

- `parseHash` is total: a malformed or foreign hash yields an empty patch —
  `tapestry/src/state/urlHash.ts:16-21`.
- `applyHash` is a partial merge and the single path that keeps the address bar
  and the store in step — `tapestry/src/state/urlHash.ts:30-38`.
- `setFilters` merges; every other setter replaces —
  `tapestry/src/state/store.ts:72` vs `:68-79`.
- `clearPath` resets the endpoints but leaves path mode armed —
  `tapestry/src/state/store.ts:75`.

**Where it strains**

- Shared links carry only four of the store's twelve fields —
  `tapestry/src/state/urlHash.ts:3-9` vs `store.ts:26-41`.
- The hash payload is trusted by assertion, not validated —
  `tapestry/src/state/urlHash.ts:18`, `:34-36`.
- Store tests mutate a shared singleton with no reset between cases —
  `tapestry/src/state/store.test.ts:12-15`, `:27-30`.

#### 2.33 `tapestry/src/views/explorer/` — the default reading surface

The force-directed WebGL weave. It compiles a bundle into a graphology multigraph
whose attributes encode every visual channel, settles it for three seconds, then
hands the reader fuzzy search, non-destructive facet filters, a shortest-path
tool, a detail panel, a legend, a minimap, keyboard walking, export and saved
views. The subsystem is split deliberately between pure, unit-tested modules and
thin React chrome.

**What must stay true**

- Filtering hides via reducers and never mutates the graph —
  `tapestry/src/views/explorer/filters.ts:45-61`.
- Entities without a confidence score pass every confidence floor —
  `tapestry/src/views/explorer/filters.ts:36-40`.
- An edge is visible only when both endpoints are visible —
  `tapestry/src/views/explorer/filters.ts:53-58`.
- Relations with a missing endpoint are skipped, not errors —
  `tapestry/src/views/explorer/buildGraph.ts:197-198`.
- Initial node positions are a deterministic function of entity id —
  `tapestry/src/views/explorer/buildGraph.ts:132-162`.
- Path reachability is undirected but the highlighted edges are the real directed
  ones — `tapestry/src/views/explorer/pathMode.ts:25-59`.
- Every Sigma-owned resource is released on graph change or unmount —
  `tapestry/src/views/explorer/Explorer.tsx:299-307`.
- The force layout degrades to a synchronous driver when a Worker cannot be built
  — `tapestry/src/views/explorer/layout.ts:96-114`.

**Where it strains**

- Unknown entity types are legible in the legend but unfilterable —
  `tapestry/src/views/explorer/legendRows.ts:29-33`.
- The CSS token layer is duplicated as literal hex fallbacks in three files —
  `tapestry/src/views/explorer/buildGraph.ts:63-98`, `Explorer.tsx:77-82`.
- Path search rebuilds a full graph copy per endpoint change while the rest of
  the view is scale-gated — `tapestry/src/views/explorer/pathMode.ts:25-33`.
- `Explorer.tsx` concentrates logic its own siblings exist to extract —
  `tapestry/src/views/explorer/Explorer.tsx:451-486`, `:1017-1032`.

#### 2.34 `tapestry/src/views/overview/` — the dashboard

A single-screen roll-up answering "what shape is this weave in" before any
exploration starts: six headline tiles, composition bars, graph-health rows, a
ten-bin confidence histogram and a PageRank-ranked table whose rows deep-link
into the Explorer. Every number comes from one pure pass over the bundle.

**What must stay true**

- Stats read the bundle arrays, never the graph model, so dangling relations stay
  countable — `tapestry/src/views/overview/stats.ts:5-9`, `:56`, `:63-66`.
- The confidence histogram is exactly ten bins and 1.0 clamps into the last —
  `tapestry/src/views/overview/stats.ts:58`, `:76-77`.
- Unscored entities are excluded from the histogram and reported separately —
  `tapestry/src/views/overview/stats.ts:49-52`, `:71-78`.
- Every proportional bar divides by a maximum floored at 1 —
  `tapestry/src/views/overview/Overview.tsx:197-201`.
- The view is read-only: its only state write is select-plus-jump —
  `tapestry/src/views/overview/Overview.tsx:1-7`.

**Where it strains**

- Bundle documents are read as untyped records: schema drift degrades silently —
  `tapestry/src/views/overview/stats.ts:49-68`.
- The roll-up claims agreement with the Explorer facets while deliberately
  counting a different population — `tapestry/src/views/overview/stats.ts:4-9`.
- The print export reaches into other components' class names —
  `tapestry/src/views/overview/Overview.css:493-504`.
- Test coverage stops at the pure function —
  `tapestry/src/views/overview/stats.test.ts:19-29`.

#### 2.35 `tapestry/src/views/systems/` — the causal-loop diagram

Re-reads the weave as a systems-dynamics model: it projects the bundle down to
its causal slice, colours each edge by polarity on a diverging channel with a
`+`/`−` glyph at its midpoint, badges every variable carrying a Meadows leverage
point, and lists the analytics pass's feedback loops in a rail where selecting a
row isolates that loop and unlocks a pulse that travels it in influence
direction.

**What must stay true**

- The Systems graph holds only causal edges and the entities they touch —
  `tapestry/src/views/systems/systems.ts:63-107`.
- Loop edge keys are resolved through directed out-edges, never undirected lookup
  — `tapestry/src/views/systems/systems.ts:125-140`.
- The flow pulse is a wrapped raised cosine so exactly one edge peaks at a time —
  `tapestry/src/views/systems/systems.ts:193-201`.
- Flow animation exists only while a loop is isolated, and reduced-motion viewers
  get the emphasis without a frame loop —
  `tapestry/src/views/systems/SystemsView.tsx:79-85`, `:358-374`.
- A variable keeps the same seeded position across the Explorer and Systems views
  — `tapestry/src/views/systems/systems.ts:79`.
- Unmount tears down renderer, layout, timer, drag controller and both overlay
  layers — `tapestry/src/views/systems/SystemsView.tsx:324-335`.

**Where it strains**

- The exported image is not the image on screen —
  `tapestry/src/views/systems/SystemsView.tsx:439-451`.
- Loop selection is keyed by array index but typed as id-or-index —
  `tapestry/src/views/systems/LoopPanel.tsx:15-16`, `:37`, `:45`.
- Per-edge DOM overlays scale with the whole graph in a view built for large
  scopes — `tapestry/src/views/systems/SystemsView.tsx:244-296`.
- Redundant encoding is visual-only: polarity is hidden from assistive tech —
  `tapestry/src/views/systems/SystemsView.tsx:487`.
- The causal view mirrors Explorer and token constants instead of importing them
  — `tapestry/src/views/systems/systems.ts:35-41`.

#### 2.36 `tapestry/src/views/chronicle/` — bi-temporal time travel

A second diagram over the same shared model, driven per instant from a pure
client-side replay of the exported event log: build a timeline, answer which
nodes and edges existed at instant *t* and each node's effective status, and
classify what changed between two instants. The view turns those answers into
reducers and overlay badges, so dragging the scrubber replays the weave
assembling itself. It is as-of read semantics reimplemented in the browser.

**What must stay true**

- Retraction replays as a status flip plus edge closure, never node removal —
  `tapestry/src/views/chronicle/replay.ts:150-162`.
- A node with no creation event is present from the start of the replay —
  `tapestry/src/views/chronicle/replay.ts:231-235`, `:250-253`.
- The timeline span is always strictly positive —
  `tapestry/src/views/chronicle/replay.ts:208-210`.
- The virtual window row stride must equal the rendered row height —
  `tapestry/src/views/chronicle/EventList.tsx:31`, `:240-297`.
- A diff node wears exactly one category, and the summary counts match the badges
  — `tapestry/src/views/chronicle/Chronicle.tsx:398-401`, `:493-502`.

**Where it strains**

- Chronicle mutates the shared graph model it otherwise only projects over —
  `tapestry/src/views/chronicle/Chronicle.tsx:164`, `:466-470`.
- PNG and SVG exports project different instants in diff mode —
  `tapestry/src/views/chronicle/Chronicle.tsx:512-540` vs `:368-372`.
- `entities_merged` is streamed but never projected —
  `tapestry/src/views/chronicle/replay.ts:191-196`.
- Effective status resolution assumes the exported log is time-ordered —
  `tapestry/src/views/chronicle/replay.ts:237-244`.

#### 2.37 `tapestry/src/views/semantic/` — the meaning map

A scatter plot of the bundle's precomputed embedding projection, read as a map of
the graph's meaning rather than its link structure. It is the only view that runs
no layout and draws no edges — coordinates come straight from the projection, so
screen distance encodes semantic distance. Over the point field it layers convex
cluster hulls rebuilt on every render pass and a freehand lasso whose brushed set
the Explorer echoes.

**What must stay true**

- The projection is the layout; no force algorithm ever runs here —
  `tapestry/src/views/semantic/semanticMap.ts:50-64`.
- A point exists only where a projection coordinate and an entity both exist —
  `tapestry/src/views/semantic/semanticMap.ts:32-48`.
- Hull and lasso geometry is computed in viewport pixels, never graph space —
  `tapestry/src/views/semantic/SemanticView.tsx:248-254`.
- Every point stays visible; the brush dims rather than filters —
  `tapestry/src/views/semantic/SemanticView.tsx:199-204`.
- A degenerate loop never mutates the brush —
  `tapestry/src/views/semantic/SemanticView.tsx:52-53`.

**Where it strains**

- Node dragging is enabled in the one view where position is the data —
  `tapestry/src/views/semantic/SemanticView.tsx:211-218`.
- Per-frame full overlay rebuild versus the 50k-point scale the same file plans
  for — `tapestry/src/views/semantic/SemanticView.tsx:238-275`.
- Exports drop the cluster hulls the on-screen map is read through —
  `tapestry/src/views/semantic/SemanticView.tsx:400-424`.
- The cluster menu lists clusters the map refuses to draw —
  `tapestry/src/views/semantic/semanticMap.ts:118-125` vs `SemanticView.tsx:581-596`.

### D. The test suite

#### 2.38 `tests/` (part 1) — harness, CLI protocol, foundations

Carries the two things the rest of the suite depends on — the namespaced live
store fixture chain and the shared test doubles — and then pins the outermost and
innermost layers at once: the CLI JSON protocol and its typed error codes, the
registry's single construction path, the config precedence chain, multi-graph and
visualization wire shapes, the composite framework's never-throw envelope, the
event-sourcing of bridges and document chunks, and the pure algorithmic
foundations.

**What must stay true**

- Every live-store test is namespaced and leaves the store as it found it —
  `tests/conftest.py:35-45`.
- Documented `loom` invocations must validate against the live CLI input models —
  `tests/test_claude_examples_contract.py:146-160`.
- A chunk write and its event append are one unit, in both failure directions —
  `tests/test_chunk_events.py:179-218`.
- A composite section never throws; failure degrades to a data-null envelope —
  `tests/test_composites_framework.py:28-41`.
- Error codes come from the typed exception hierarchy, never from prose matching
  — `tests/test_cli_io.py:67-85`.
- Chunk reads survive the server's result-set cap and honour their own limit —
  `tests/test_chunkstore.py:28-43`, `tests/conftest.py:62-71`.
- A truncated consumption answer accounts for every row it dropped —
  `tests/test_consumption.py:263-313`.
- Superseded entities leave every consumption read surface —
  `tests/test_consumption.py:158-171`, `:409-415`, `:533-540`.
- Config resolves through one loader with flags over env over file over defaults
  — `tests/test_config.py:25-104`.

**Where it strains**

- Pure-unit and live-FalkorDB tests share one unmarked suite —
  `tests/conftest.py:19-32` vs `tests/test_cegis.py:1-8`.
- Tests reach into private surfaces to pin behaviour the public API does not
  expose — `tests/test_cegis.py:26-31`, `tests/test_chunk_events.py:215`.
- A test module reaches outside `tests/` to police files it does not own —
  `tests/test_claude_examples_contract.py:31-32`.
- Budget assertions are tuned to the allocator's current fixed overhead —
  `tests/test_consumption.py:257-260`, `:279`, `:287`.

#### 2.39 `tests/` (part 2) — derivation surfaces and extraction honesty

The executable specification for the code that computes an answer or proposes a
change: pure algebra with hand-worked goldens, the entity-proposal foundation
against a tiny in-memory fake, two composites that must produce real non-stub
findings with no LLM and degrade rather than fabricate, and the extraction
contracts — doc-to-code resolution and its four false-positive guards, the single
encode/parse module, path globs, bi-temporal retirement of legacy edges, and
provenance on LLM-extracted entities.

**What must stay true**

- Every populated section keeps its first row, even when that row alone blows the
  budget — `tests/test_consumption_budget.py:40-45`.
- The dedup gate asks the vector index and matches against every status —
  `tests/test_dedup_gate_search.py:52-73`.
- The embedding state machine is binary, content-hash driven, and reconciles in
  both directions — `tests/test_embedding_state.py:44-105`.
- An upstream section failure nulls every downstream section instead of
  fabricating zeros — `tests/test_enrichment_crawl.py:283-301`.
- Symmetric evidence never infers a causal relation —
  `tests/test_enrichment_crawl.py:226-241`.
- A doc-to-code link is drawn only for an unambiguous, code-shaped, callable,
  non-vocabulary mention — `tests/test_extraction_doclinks.py:98-165`.
- `exclude` is applied after `include` and removes the file's entities from the
  graph — `tests/test_extraction_filters.py:48-63`.
- LLM-extracted entities carry a provenance block —
  `tests/test_extraction_provenance.py:89-118`.

**Where it strains**

- Composite tests assert loose lower bounds on real algorithmic output —
  `tests/test_creativity_loop.py:77-84`.
- A wall-clock assertion in a suite whose CI is documented not to gate on timing
  — `tests/test_enrichment_crawl.py:345-370`.
- A shared fakes module exists, yet most modules write their own —
  `tests/test_entity_proposer_foundation.py:41-106`.
- Human-readable prose is asserted as contract in several places —
  `tests/test_creativity_loop.py:199-205`.
- The shared fixture repo is asserted with exact counts from several modules at
  once — `tests/test_extraction_doclinks.py:378-382`.

#### 2.40 `tests/` (part 3) — extraction pipeline and the store beneath it

Eight of twelve files tell one continuous story: how source text becomes a graph,
what the resolvers refuse to guess, how a re-run retires what an older extractor
got wrong, and what the store guarantees underneath. The recurring subject is
refusal under uncertainty.

**What must stay true**

- Structural extraction never emits `related_to` —
  `tests/test_extraction_resolution.py:451-463`.
- No extracted edge points at an entity the extraction did not create —
  `tests/test_extraction_resolution.py:481-499`.
- An ambiguous name produces no edge at all —
  `tests/test_extraction_resolution.py:205-241`.
- Every call edge is anchored at its call site in the caller's file —
  `tests/test_extraction_units.py:63-76`.
- Git visibility, not directory contents, decides what becomes an entity —
  `tests/test_extraction_units.py:418-441`.
- Every store mutation appends exactly one typed event, in order —
  `tests/test_falkor_store.py:498-533`.
- Updates invalidate rather than overwrite, and version intervals partition
  system time — `tests/test_falkor_store.py:602-662`.
- Full-scan store reads stay complete above the server result-set cap —
  `tests/test_falkor_store.py:468-490`.
- `COMMANDS.md` is byte-equal to the registry-generated catalog —
  `tests/test_generate_docs.py:34-40`.

**Where it strains**

- Exact golden counts make one fixture repo a shared bottleneck —
  `tests/test_extraction_units.py:473-493`.
- The suite freezes private store and self-model internals —
  `tests/test_falkor_store.py:144-147`, `:378-395`.
- Hard delete is a tested escape hatch from the append-only invariant —
  `tests/test_falkor_store.py:129-135`, `:384-395`.
- This group needs Docker and git, and never exercises the in-memory read port —
  `tests/test_falkor_store.py:35-37`.
- Real sleeps and a wall-clock duration assertion inside the suite —
  `tests/test_falkor_store.py:553-556`, `:611-617`.

#### 2.41 `tests/` (part 4) — the write path, the model, and response honesty

The executable contract for entity/relation writes, the domain model beneath
them, and — the newest and largest addition — the Agent Contract's
response-honesty convention: a command response must never be success-shaped for
something that did not happen. One mechanism (`notices`) plus one file per
command family that adopted it. The stance is uniform: a contract is what the
test asserts by equality on whole output documents, whole enum inventories and
whole transition tables.

**What must stay true**

- Deletion retracts by default and the record stays readable —
  `tests/test_ops_entity.py:138`, `:152`.
- Update auto-populates revision fields with a fixed change-type precedence —
  `tests/test_ops_entity.py:169`, `:183-201`.
- Guard violations are appended as observations, not raised —
  `tests/test_ops_entity.py:62`, `:69`.
- `merge-entities` is a single atomic contract: union, redirect, supersede, one
  event — `tests/test_ops_merge.py:79`, `:100-175`, `:183`.
- A re-merge is a no-op and a dry run writes nothing at all —
  `tests/test_ops_merge.py:303`, `:325`.
- `bulk-import` is idempotent by `name::entityType` and reports per-item errors
  without failing the batch — `tests/test_ops_bulk.py:87`, `:243`.
- Polarity belongs to causal relation types only, on every write path —
  `tests/test_model.py:117`, `:280`, `tests/test_ops_bulk.py:172`.
- The status lifecycle is a fixed table and `retracted` is terminal —
  `tests/test_model.py:312-323`.
- The wire format is `exclude_unset` by alias: set nulls survive, unset optionals
  disappear — `tests/test_model.py:231`, `:245`, `:272`.
- Name and id addressing produce identical results, including for non-active
  entities — `tests/test_name_addressing.py:158-172`.
- Notices are purely additive and survive dispatch to CLI stdout —
  `tests/test_notices.py:23-61`.
- Documents are global: the graph parameter is accepted, ignored, and announced —
  `tests/test_ops_documents.py:153`, `:160`, `:174`.
- An embedding failure is recorded on the chunk, not swallowed —
  `tests/test_ops_documents.py:95-129`.

**Where it strains**

- Hard delete is supported and tested while the architecture forbids overwriting
  — `tests/test_ops_entity.py:138` vs `:152`.
- Tests reach below the ops layer into private internals —
  `tests/test_ops_merge.py:379`, `:416-424`.
- Bi-temporal pivots are built from real wall-clock sleeps —
  `tests/test_ops_merge.py:396-402`.
- Merge leaves stale edges attached to the superseded secondary —
  `tests/test_ops_merge.py:206-238`.
- Notice adoption is asserted command-by-command with no registry-wide sweep —
  `tests/test_ops_documents.py:153`, `tests/test_ops_algebra_direction.py:123`.
- Nine files each define their own entity/relation builders —
  `tests/test_ops_entity.py:37`, `tests/test_ops_merge.py:32`.

#### 2.42 `tests/` (part 5) — store contract and the semantic layer

The two places where the architecture's promises are invisible in the source and
only provable by running. On the store side: read-port conformance across two
adapters, as-of reads, mutation/event atomicity under four injected failure
points, event-log repair, and filter pushdown proved equivalent to a Python
oracle across a 26-case matrix. On the semantic side: the one search core, the
hybrid-ranking stages as pure functions, content-hash skip on re-embed,
fingerprint goldens, and the composites that must account for every write they
attempt.

**What must stay true**

- Every read-port adapter answers the same way, down to ordering —
  `tests/test_read_port.py:71`, `:174`, `:271`, `:294`.
- An as-of read returns the version live at the bound —
  `tests/test_read_port.py:458-565`.
- A mutation and its event append are one unit — neither half survives alone —
  `tests/test_store_atomicity.py:92`, `:192`.
- An unrepairable event-log gap is named in a typed error —
  `tests/test_store_atomicity.py:313-380`.
- Server-side filter pushdown is exactly equivalent to the Python filter path —
  `tests/test_store_pushdown.py:219-235`.
- Vector search never full-scans and never trusts the engine's window order —
  `tests/test_semantic_perf.py:57`, `:109`.
- Search scores are `1/(1+L2)` with the raw cosine carried alongside —
  `tests/test_semantic_search_core.py:36`, `:89`.
- Auto-apply accounts for every write: reported, rolled back, or reported as
  stranded — `tests/test_self_improve.py:57`, `:173`.
- The SSRF guard rejects private and reserved addresses before any network IO —
  `tests/test_ssrf.py:16`, `:58`, `:73`.
- The abstract store surface is pinned by set equality, not merely non-empty —
  `tests/test_store_base.py:16`, `:46`.

**Where it strains**

- Bi-temporal correctness is asserted through wall-clock sleeps —
  `tests/test_read_port.py:458-548`.
- The in-memory adapter is a conformant reader while every write suite is
  hardwired to Falkor — `tests/test_store_atomicity.py:38`,
  `tests/test_store_pushdown.py:39`.
- The pushdown suite asserts through private store internals —
  `tests/test_store_pushdown.py:148`, `:288`.
- A measured store-engine defect is recorded only as a test comment —
  `tests/test_semantic_perf.py:440-450`.
- Half the group needs live infrastructure and half does not, with nothing
  marking which — `tests/test_ssrf.py:9`, `tests/test_store_base.py:14`.

#### 2.43 `tests/` (part 6) — visualization, work memory, and leaf units

Three subsystems sharing a method rather than a subject: the whole visualization
and export pipeline walked stage by stage (scope resolution and typed refusals,
the three optional sections, the assembler's degree-ranked truncation, the as-of
bound, sentinel injection and escaping, the HTTP surface, and two drift guards
that hold checked-in artifacts against the live model); the work-memory feedback
loop; and the synthesis and verification leaf units nothing else pins.

**What must stay true**

- `record-outcome` is all-or-nothing: no evidence entity survives a failed
  citation write — `tests/test_work_memory.py:184`, `:213`.
- One outcome is one vote: duplicate citations collapse to a single edge —
  `tests/test_work_memory.py:196`, `:358`.
- Citation weight decays by an exact half-life measured against the supplied
  bound — `tests/test_work_memory.py:262`, `:291-292`.
- An as-of bound reconstructs the graph as it stood, including edges retired
  since — `tests/test_viz_asof.py:18`, `:37`.
- Analytics and semantic sections are never recomputed as-of; they self-label —
  `tests/test_viz_asof.py:107`, `:124-129`.
- Optional bundle sections are omitted, never emitted empty —
  `tests/test_viz_bundle.py:28`, `:40`.
- Bundle truncation keeps the highest-degree core, induces its relations, and is
  reproducible — `tests/test_viz_bundle.py:57`, `:79`.
- Bundle JSON is injected at a sentinel and escaped against script-close —
  `tests/test_viz_html.py:22`, `:28`.
- The committed JSON Schema and dev fixture must equal what the model emits —
  `tests/test_viz_schema_drift.py:21`, `:29`.
- Typed error codes surface as fixed HTTP statuses with the code in the body —
  `tests/test_viz_serve.py:32`, `:51`.

**Where it strains**

- Bi-temporal ordering is established with real wall-clock sleeps —
  `tests/test_viz_asof.py:23-25`.
- The whole HTTP surface contributes nothing on the default install path —
  `tests/test_viz_serve.py:10`.
- The UMAP projection path is never exercised in CI —
  `tests/test_viz_semantic.py:95-108`.
- The served-template drift guard skips in exactly the checkouts most likely to
  be stale — `tests/test_viz_html.py:49-53`.
- Decay behaviour is pinned by re-implementing the writer being pinned —
  `tests/test_work_memory.py:65-107`.

#### 2.44 `tests/fixtures/multi/` — the multi-graph snapshot seed

A four-file seed encoding three named graphs plus a reserved bridges sidecar,
wired into a chain. Deliberately tiny so tests can assert exact counts and
byte-exact document equality rather than shapes.

**What must stay true**

- Fixture docs are the byte-exact expected output, not merely valid input —
  `tests/fixtures/multi/_bridges.json:2-29`.
- Bridge endpoint ids must resolve to nodes in the sibling graph files —
  `tests/fixtures/multi/_bridges.json:5-6`, `:13-14`.
- Every graph file carries the full nodes/edges/metadata triple, empties included
  — `tests/fixtures/multi/systems.json:18-19`.

**Where it strains**

- Bridge docs use snake_case graph fields while node docs use camelCase —
  `tests/fixtures/multi/_bridges.json:7` vs `:13-14`.
- Hand-copied UUIDs couple four files with nothing checking the coupling —
  `tests/fixtures/multi/research.json:27`.

#### 2.45 `tests/fixtures/repo/` — the non-Python half of the golden repo

Six files that exist to be extracted, never executed: two Markdown docs pinning
doc-link resolution (one that must produce references, one whose every mention
must produce none), a TypeScript entry point and a JavaScript helper exercising
cross-language resolution, and a README plus a stylesheet proving non-code text
still becomes a graph root.

**What must stay true**

- `roundCents` is defined twice on purpose, so no doc mention may resolve —
  `tests/fixtures/repo/lib/index.ts:22`, `lib/helper.js:5`.
- `docs/glossary.md` must contribute zero relations —
  `tests/fixtures/repo/docs/glossary.md:3-14`.
- `docs/architecture.md` yields exactly four references and two refusals —
  `tests/fixtures/repo/docs/architecture.md:3-10`.
- The TypeScript entry point imports its helper extensionlessly, so resolution
  must cross into JavaScript — `tests/fixtures/repo/lib/index.ts:1`.

**Where it strains**

- The fixture is both a growable negative-case corpus and a frozen count baseline
  — `tests/fixtures/repo/lib/index.ts:20-24`.
- The `lib/` tree names its symbols after a domain the docs never link to —
  `tests/fixtures/repo/docs/architecture.md:3-7`.

#### 2.46 `tests/fixtures/repo/src/` — the Python half of the golden repo

A three-file miniature banking service that exists only to be parsed. Its job is
to present, in about 50 lines, one instance of every construct the extractor must
recognise — a decorated dataclass with a method, a module-level factory, a
constant read by two functions, typed functions with and without docstrings, a
stdlib import, a package-qualified cross-file import, intra- and cross-module
call sites, a bare attribute mutation that is not a call, and two marker comments
carrying citations.

**What must stay true**

- Rationale comments bind to the innermost enclosing symbol, else to the file —
  `tests/fixtures/repo/src/policy.py:8`, `service.py:17`.
- `service.py` imports its sibling by the `src.`-qualified path, so the fixture
  must be rooted one level up — `tests/fixtures/repo/src/service.py:3`.
- `under_review` is deliberately both a callable and the string it returns —
  `tests/fixtures/repo/src/policy.py:12-14`.
- `service.py`'s functions are the fixture's only symbols without docstrings —
  `tests/fixtures/repo/src/service.py:6-14`.

**Where it strains**

- `policy.py` is written as a guard that nothing calls —
  `tests/fixtures/repo/src/policy.py:1`, `:6-9`.
- `transfer` bypasses `Account.deposit` on the debit side, hiding half the
  coupling — `tests/fixtures/repo/src/service.py:7-8`.
- The fixture must read as ordinary code while every line number is test data —
  `tests/fixtures/repo/src/policy.py:8`.

---

## 3. Load-bearing modules

Ranked by degree centrality — how many other records touch this one.

| # | Record | Why it is a hub |
|---|---|---|
| 1 | `pkg:typing` | Every Python module in the repo declares a typing import, so this external package node collects an import edge from nearly all 268 Python files. |
| 2 | `CommandInput (common)` | The base class 157 command input models extend (`theloom/operations/common.py:42-56`); changing it changes the wire schema of every command. |
| 3 | `file:theloom/store/falkor.py` | The store implementation: 1,143 lines carrying the entity and relation rows, their versions, and every read path the rest of the system calls. |
| 4 | `file:theloom/store/multigraph.py` | The facade every handler receives as its second argument — named graphs, bridges, chunk store and store construction all resolve here. |
| 5 | `file:tapestry/src/views/explorer/Explorer.tsx` | The largest frontend component; it instantiates Sigma, composes every reducer layer, and wires search, filters, path mode, export and saved views. |
| 6 | `file:theloom/model.py` | The domain model, imported by 47 modules; every enum, shape and validator downstream code speaks. |
| 7 | `file:tapestry/src/views/chronicle/Chronicle.tsx` | The time-travel view: a second Sigma instance plus the replay projection, overlays, play loop and exports. |
| 8 | `file:tapestry/src/views/systems/SystemsView.tsx` | The causal-loop view with its polarity glyph and leverage-badge overlay layers and the flow animation. |
| 9 | `file:tapestry/src/views/semantic/SemanticView.tsx` | The projection scatter with hull and lasso overlays and the cluster-brush menu. |
| 10 | `file:docs/architecture/ARCHITECTURE-MAP.md` | This document — it names roughly fifty files and symbols, and the written layer anchors dozens of notes back to it. The map is a node in the graph it measures. |
| 11 | `file:tests/test_entity_proposer_foundation.py` | 612 lines of in-fake proposal tests that touch the proposer, the dedup gate, fingerprints, capability specs and interestingness. |
| 12 | `file:theloom/extraction/treesitter.py` | 1,386 lines: every per-language extractor plus the whole public extraction API. |
| 13 | `file:theloom/operations/semantic.py` | 965 lines covering the embedding lifecycle and every retrieval and discovery command. |
| 14 | `file:theloom/cli/registry.py` | Where all 164 commands are declared; every operation module is imported here. |
| 15 | `file:theloom/operations/epistemic.py` | 944 lines: seventeen epistemic queries plus credit propagation. |

Ranked by betweenness — how often the shortest path between two other records
runs through this one — the picture shifts toward the spine rather than the bulk.
`theloom/store/multigraph.py` and `theloom/store/falkor.py` lead: nearly every
path from a command to its data crosses them. `theloom/cli/registry.py` is third,
being the only place the CLI surface meets the operations layer. Then
`theloom/viz/bundle.py` (the single assembler behind three transports),
`theloom/operations/semantic.py`, `README.md` (documentation links out into
otherwise-distant subsystems), `theloom/operations/analysis.py`,
`theloom/config.py`, `theloom/semantic/embed.py`,
`theloom/operations/common.py` and `theloom/model.py`. Note that
`docs/architecture/ARCHITECTURE-MAP.md` ranks fourth on betweenness for the same
reason `README.md` ranks sixth: prose that names many files becomes a shortcut
between subsystems that share no code.

---

## 4. Dependency cycles

Sixteen cycles exist. Three are multi-record; thirteen are single-record
self-loops, all of which are recursive functions and none of which is a design
problem.

| Members | Verdict | Reason |
|---|---|---|
| `theloom/store/falkor.py` → `theloom/store/read_port.py` → `theloom/store/falkor.py` | intentional | The concrete store implements the read port and the port's type annotations name the implementation; a narrow protocol/implementation pair, not a layering violation. |
| `theloom/store/read_port.py` → `theloom/store/memory.py` → `theloom/store/read_port.py` | intentional | Same shape for the second adapter: the in-memory store implements the port and is referenced from it. |
| `README.md` → `CONTRIBUTING.md` → `docs/architecture/ARCHITECTURE-MAP.md` → `README.md` | intentional | Documentation cross-references, not imports. Worth knowing only because it makes these three files short-circuit paths between unrelated subsystems (§3). |
| `_object_rows (schema)` ↔ `_nested_rows (schema)` | intentional | Mutual recursion in the JSON-Schema walker — an object row descends into nested rows, which recurse back for object-valued properties (`theloom/cli/schema.py:114-172`). Bounded by the walker's depth cap and `seen` guard. |
| `type_str (schema)` | intentional | Self-recursion over composite schema types (`theloom/cli/schema.py:84-101`). |
| `_jsonify (io)` | intentional | Recursive JSON sanitisation of dicts and lists (`theloom/cli/io.py:56-64`). |
| `_hash_at_depth (fingerprint)` | intentional | The Weisfeiler-Leman refinement recurses on depth − 1 with a memo cache (`theloom/reification/fingerprint.py:56-82`). |
| `_resolve_references (core)` | intentional | Recursive `$name` substitution through nested chain payloads (`theloom/symbolic/core.py:789-822`). |
| `_generic_json_to_blocks (parsers)` | intentional | Recursive descent over arbitrary JSON with a depth cap (`theloom/documents/parsers.py:261-305`). |
| `_comment_notes`, `_string_literal_vocabulary`, `_extract_calls`, `_find_identifier`, `_extract_require_calls` (treesitter) | intentional | Five tree walkers, each recursing over child nodes of a parsed syntax tree (`theloom/extraction/treesitter.py:326-703`). |
| `_js_string (prompts)` | intentional | Recursive JavaScript-compatible string formatting (`theloom/synthesis/prompts.py:13-24`). |
| `_substitute (test_claude_examples_contract)` | intentional | Recursive placeholder substitution inside the docs contract test (`tests/test_claude_examples_contract.py:96-110`). |

The only cycle worth a second look is the recursion family in
`theloom/extraction/treesitter.py` and `theloom/verification/checks.py`: several
of these walkers have no explicit depth guard, so a pathological input is bounded
only by the Python stack (see §6).

---

## 5. Communities versus directories

Two findings, and the second is the more interesting one.

**The code is one connected mass.** Component detection over the 6,744 live
records finds exactly two components: one containing 6,743 records, and one
isolated record — `tapestry/src/views/explorer/Explorer.css`, a stylesheet that
tree-sitter does not parse and that nothing imports through a resolvable edge.
Everything else is joined, largely through containment and package-import edges.

**Semantic clustering does not recover the directory structure.** Clustering over
a 500-record sample produces thirteen clusters, all of size two or three, with
average similarities between 0.70 and 0.74. Every one of them is a
within-file naming coincidence — `LABEL_FONT`/`labelStateFor`/`s` inside the
Systems view; `effectiveTime`/`effectiveTimeRef` inside the Chronicle;
`minY`/`py` inside the minimap. The three cross-file clusters are equally
shallow: two Explorer test files, two state files in the same directory, and
`theloom/operations/__init__.py` paired with `theloom/reification/__init__.py`
(both empty).

The honest reading is that at this granularity the embedding space is dominated
by local naming, not by architecture, and it disagrees with the folder structure
only by being uninformative rather than by proposing a different seam. The real
seams in this codebase are the ones the directory tree already states — the
store, the operations layer, the pure libraries, the frontend — and they are
visible in the *betweenness* ranking (§3) far more clearly than in any clustering
result. Treat §3 and §4 as the structural evidence; treat this section as a null
result reported rather than hidden.

---

## 6. Risks and tensions

347 tensions are recorded across the graph. These are the ones a reviewer should
know about first, worst-first within each band.

### Architectural

1. **Hard delete is a supported escape hatch inside an append-only,
   event-sourced store.** Five call sites use it deliberately —
   `theloom/operations/inference.py:352-368`,
   `theloom/operations/extraction.py:293` and `:307`,
   `theloom/composites/self_improve.py:454-465` (saga compensation),
   `theloom/operations/work_memory.py:166-169` — each with a stated rationale,
   and the store exposes it at `theloom/store/falkor.py:439-500`. It is tested as
   a contract (`tests/test_falkor_store.py:129-135`). The invariant says updates
   invalidate and never overwrite; this is the documented exception, and it is
   spreading.
2. **Not every write is event-sourced.** Vectors, metadata and verbatim imports
   bypass the log — `theloom/store/falkor.py:270-275`, `:1129-1137`, `:252-262`,
   `:837-848`. Replay from the event log therefore reconstructs less than the
   store contains.
3. **The derived read index duplicates filter semantics that are kept in sync by
   hand.** `theloom/store/falkor.py:101-114` against
   `theloom/store/filters.py:69-100`. The pushdown-vs-oracle test suite
   (`tests/test_store_pushdown.py:219`) is what stands between this and silent
   wrong answers.
4. **The pure algorithm layer writes to the store.** `theloom/graph/cycles.py:20`
   and `:293-334` — the one module in `theloom/graph/` that holds a store
   reference, and a store-less persist request reports success without
   persisting.
5. **An untyped `RuntimeError` escapes a codebase built on typed error codes** —
   `theloom/graph/analytics.py:68`.
6. **`bulk-import`'s validation errors surface as `OPERATION_ERROR`** rather than
   `VALIDATION_ERROR`, breaking the one-code-per-failure-class rule —
   `theloom/cli/registry.py:1669-1670`, `theloom/operations/bulk.py:270-273`.

### Security and trust boundary

7. **The live visualization server is unauthenticated and its bind host is
   caller-supplied and unvalidated** — `theloom/viz/serve.py:45-46`, listing
   every graph at `:105-108`. `SECURITY.md:12-17` documents this as an
   assumption; nothing enforces it as a default.
8. **The SSRF guard resolves DNS separately from the connection it protects**
   (`theloom/documents/ssrf.py:74-80` vs `:92-95`, acknowledged at `:8-9`), and
   the response-size ceiling is checked only after the whole body is in memory
   (`:95`, `:106-109`).
9. **`ingest_url` classifies SSRF failures by message prefix**, contradicting the
   typed-error rule its own module docstring states —
   `theloom/documents/ingestion.py:250-254` vs `:36-39`.
10. **`sympify` runs on caller-controlled strings** — `theloom/symbolic/core.py:48`,
    `:772-776`, `:918` — on the stated assumption of a trusted caller.
11. **The static visualization path writes to a caller-controlled filesystem
    location** — `theloom/viz/html.py:55-58`.

### Correctness

12. **Re-ingesting a file duplicates its chunks.** First ingest appends blindly
    while reingest diffs — `theloom/documents/ingestion.py:141-157` vs `:305`.
13. **`asOf` is validated by date parsing but applied by string comparison** —
    `theloom/viz/bundle.py:107` vs `theloom/viz/temporal.py:15`.
14. **Merge leaves stale edges attached to the superseded secondary** —
    `tests/test_ops_merge.py:206-238`, which pins the behaviour as accepted.
15. **`constrained-generate` commits entities but silently drops every relation**
    — `theloom/operations/verification.py:550-556`.
16. **A timed-out subgraph search is indistinguishable from a complete one** —
    `theloom/analysis/isomorphism.py:242-265`.
17. **`hypothesis-engine`'s overall score ignores its own sub-scores** and depends
    on a key nothing writes — `theloom/composites/hypothesis_engine.py:84-132`,
    `:352`.
18. **`createdRelationIds` does not contain relation ids** —
    `theloom/operations/bulk.py:421`.

### Scale and resource use

19. **Every analytics and algebra command hydrates the entire graph in memory** —
    `theloom/operations/analysis.py:61-64`, `theloom/operations/algebra.py:67-70`;
    `blast-radius` additionally pays a full scan per call
    (`theloom/operations/blast_radius_traversal.py:61-75`), `provenance-audit`
    scans the whole graph for a single-entity question
    (`theloom/composites/provenance_audit.py:98-110`), and every capability check
    re-lists the graph (`theloom/verification/capability_spec.py:36-44`).
20. **Discovery commands cost one vector query per candidate entity** —
    `theloom/operations/semantic.py:743-750`, `:797`, with a default ceiling of
    5,000 entities.
21. **Unbounded recursion.** Metapath expansion has no cycle guard and no
    frontier cap (`theloom/algebra/routing.py:526-528`, `:586-596`); cycle
    detection and several graph walks recurse without a depth guard
    (`theloom/verification/checks.py:191-210`,
    `theloom/graph/cycles.py:38-99`); the tree-sitter walkers likewise (§4).
22. **Best-effort temp-graph cleanup can leave `sim-<uuid>` graphs behind** —
    `theloom/composites/simulate_change.py:310-313`.

### Supply chain

23. **`falkordb`, the one non-negotiable dependency, is the only one with no
    version floor** — `uv.lock:4262`, `pyproject.toml:22`.
24. **An ISC-licensed project whose only supported store is SSPL-licensed** —
    `pyproject.toml:7`, `:13` vs `STACK.md:22-26`.
25. **Python 3.14+ resolves `numba`/`llvmlite` onto 2021-era sdist-only releases**
    and `python-graphblas` silently drops its JIT edge — `uv.lock:2099-2118`,
    `uv.lock:3303-3310`.

### Documentation and test discipline

26. **The command count is hand-copied into two documents while only the catalog
    is generated** — `COMMANDS.md:5` vs `README.md:30` vs `CLAUDE.md:8-9`; the
    repo layout is described three times with two copies already drifted
    (`CLAUDE.md:57-74`, `README.md:366-392`, `CONTRIBUTING.md:111-118`).
27. **The glossary is unreachable from every entry-point document** and bans a
    word five commands are named after — `CONTEXT.md:1-6`, `:104` vs
    `COMMANDS.md:245`.
28. **Pure-unit and live-FalkorDB tests share one unmarked suite** —
    `tests/conftest.py:19-32` vs `tests/test_cegis.py:1-8`; half of several
    groups needs Docker and nothing marks which half.
29. **Bi-temporal correctness is asserted through real wall-clock sleeps** —
    `tests/test_read_port.py:458-548`, `tests/test_ops_merge.py:396-402`,
    `tests/test_viz_asof.py:23-25`.
30. **Exact golden counts make one fixture repo a shared bottleneck** —
    `tests/test_extraction_units.py:473-493` and four sibling modules assert
    against the same seven files.

---

## 7. Open seams

Pairs of records that read as near-identical but are not connected. The
strongest twenty were sampled; three families stand out.

**Deliberate near-twins in the store and verification surfaces.** `read_relation`
and `read_relations` on the abstract store (similarity 0.79), the same pair on
the in-memory adapter (0.77), and `guard_causal_polarity`/`guard_non_causal_polarity`
(0.77) are singular/plural or positive/negative counterparts. They are correct as
written; the seam is that nothing in the graph records them as a pair, so a
change to one leaves no trace pointing at the other.

**Duplicated helper vocabulary inside single modules.** `parse_call_site` and
`parse_call_site_text` in the extraction encoder (0.76), `_opt`/`_opt_int` in
component signatures (0.76), `_VARIABLE_KINDS`/`_PROCEDURE_KINDS` in the
tree-sitter extractor (0.76). Each pair is a small local convention that could be
one function with a parameter; none is wrong, all are places where the next
edit has to remember there are two.

**The frontend and its written layer say the same thing twice.** `A diff node
wears exactly one category…` and `A node wears exactly one diff badge…` (0.78)
are two written notes for one behaviour; `DOM overlay layers re-seated on every
afterRender` and its longer twin (0.78); `Refs as the render-loop channel` and
`Refs as the live channel` (0.77); the two live-region claims (0.77). These are
artifacts of the same behaviour being described from two module groups (the
Chronicle view and the Systems view, the shell and the lib) — a signal that those
two groups share a convention that has no single owner.

One further pair is a bookkeeping artifact rather than a code seam: two
`repo root (part 1/2) purpose` records (0.78) and the
`tapestry/src views …`/`tapestry/src/views/explorer` pair (0.78) are the older and
newer written layers for the same directory, produced by successive mapping runs
with different group partitions. See §8, legacy identifiers.

---

## 8. Coverage and methodology

**What was covered.** All 46 module groups in the current partition have a
written layer in the graph: a purpose, its patterns, its invariant claims and its
tensions, each anchored to `file:line`. Every subsection in §2 is a projection of
one of those groups. No group is unenriched.

**What this run re-derived.** This was an incremental run against commit
`c470c03`. Eleven groups were re-enriched from fresh reads of the changed files:
`repo root (part 1/2)`, `examples`, `tests (part 1/7)`, `tests (part 3/7)`,
`tests (part 4/7)`, `tests (part 5/7)`, `tests (part 6/7)`, `theloom/cli`,
`theloom/operations (part 1/3)`, `theloom/operations (part 2/3)` and
`theloom/operations (part 3/3)`. Two groups — `docs/architecture` and `theloom` —
had diffs too small to justify re-enrichment and carried their previous semantic
layer forward unchanged. The remaining groups were unchanged in this diff and
retain the layer written at earlier commits. The front matter therefore pins one
commit while the narrative is of mixed vintage: the structural layer (files,
symbols, calls, imports) is current as of `c470c03`; the written layer for an
untouched group is as old as the last run that touched it.

**Legacy identifiers.** Seven older group labels survive in the graph from
mapping runs whose partition differed from today's — `root-1`, `docs-1`,
`tapestry-src-1` through `tapestry-src-4`, and `tests-fixtures`. Their records
are still readable and still anchored, but they describe an older slicing of the
same files and are superseded in spirit by the current groups. Prefer the current
identifiers listed in `QUERYING.md`; nothing retires the old ones automatically.

**What was not covered.** 43 files became records with no symbols because
tree-sitter has no grammar for them here — CSS, JSON, Markdown, YAML, TOML and
lockfiles. They participate in the graph as file records and as doc-link targets,
but their internals are invisible. The `.claude/` tree (agents, skills, harness
templates) is excluded from the lint gate and is not part of the package; it is
present in the graph only as files.

**Dirty tree.** The working tree carried uncommitted modifications to
`.claude/agents/research-consolidation.md`,
`.claude/skills/the-loom/SKILL.md`,
`.claude/skills/the-loom/references/tool-catalog.md`, `README.md` and
`.gitignore` at extraction time. Anchors are accurate to the files as they were
on disk.

**Method.** Two layers. The structural layer is deterministic: tree-sitter parses
each git-visible file into file/class/function/variable records plus containment,
call, import, inheritance and doc-reference edges; a whole-project pass resolves
what no single file can; ambiguity produces no edge rather than a guessed one. The
written layer is produced per module group by reading the group's files and
recording a purpose, its patterns, its invariant claims and its tensions — each
carrying the `file:line` anchor it was written against. Re-runs supersede only the
written layer; structural churn is handled by the incremental diff, which
supersedes rather than deletes. Rankings in §3, cycles in §4 and the clustering in
§5 are algorithmic output, adjudicated here rather than reported raw.

**How to re-run.** `/map-codebase <repo-root>`. The run reads
`docs/architecture/map-manifest.json` for its baseline commit and re-enriches only
the groups whose files changed. These four files are generated; the only supported
edit is a re-run. `codebase-map.html` is gitignored.

**How to interrogate the graph directly.** Start with
[QUERYING.md](QUERYING.md) — one runnable `loom` invocation per question class.
The fastest paths:

```bash
loom explore        '{"name": "<symbol>", "graph": "codebase-the-loom"}'
loom find-callers   '{"name": "<symbol>", "graph": "codebase-the-loom"}'
loom blast-radius   '{"name": "<symbol>", "graph": "codebase-the-loom"}'
loom entity-deep-dive '{"name": "<symbol>", "compact": true, "graph": "codebase-the-loom"}'
loom hybrid-search  '{"query": "<question>", "graph": "codebase-the-loom"}'
```

The graph name is `codebase-the-loom`; it requires a running FalkorDB
(`docker compose up -d falkordb`).
