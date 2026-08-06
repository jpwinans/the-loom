# The Loom

A knowledge-graph substrate: one transactional store holding typed entities,
relations, embeddings, and document text, driven through a JSON-in/JSON-out
CLI. This glossary is the ubiquitous language for the project — use these
terms, in these senses, when discussing or changing The Loom.

## Language

### Substrate

**Store**:
The single FalkorDB instance holding every graph, entity vector, document
chunk, and full-text index. There is exactly one; no knowledge lives outside it.
_Avoid_: database, backend, data layer

**Graph**:
A named knowledge graph inside the store. Many graphs coexist; every command
targets one.
_Avoid_: namespace, dataset, collection

**Event log**:
The append-only record of every mutation to a graph — the source of truth for
history. Distinct from the `event` entity type (something that happened in the
*modeled* domain); say "event log" when you mean the mutation record.
_Avoid_: audit log, history table

**Projection**:
The current state of a graph, understood as a view derived from its event log
— never the only truth, since any past state is equally queryable.
_Avoid_: snapshot

**Invalidation**:
Ending a record's validity while preserving it in history. The only way stored
knowledge ever "changes" — updates invalidate; they never overwrite in place.
_Avoid_: overwrite, edit in place

**As-of**:
A read of a graph as it stood at one instant of its own recorded history — a
first-class operation, not a reconstruction.
_Avoid_: time travel (colloquial), rollback

**Session**:
The research session that created a record, carried on entities and relations
as first-class provenance.

**Bridge**:
A relation whose endpoints live in two different graphs, connecting them at
the ecosystem level.
_Avoid_: cross-link, inter-graph edge

### Knowledge

**Entity**:
A typed node in a knowledge graph whose content is its observations. Say
"entity" even when discussing graph algorithms.
_Avoid_: node, vertex, record

**Observation**:
One atomic statement attached to an entity. An entity's content is the sum of
its observations.
_Avoid_: property, note, attribute

**Relation**:
A directed, typed edge between two entities, falling into exactly one of three
classes: structural, epistemic, or causal.
_Avoid_: edge, link, connection

**Structural relation**:
A relation describing how things are arranged, with no polarity: `related_to`,
`instance_of`, `part_of`, `sources`, `calls`, `references`.

**Epistemic relation**:
A relation describing what knowledge says about other knowledge, with no
polarity: `supports`, `contradicts`, `questions`, `supersedes`.

**Causal relation**:
A relation describing influence, always carrying polarity: `causes`,
`enables`, `requires`, `inhibits`, `amplifies`, `dampens`.

**Polarity**:
The signed direction of effect on a causal relation — `+` moves the target the
same way, `−` the opposite way. Only causal relations have it.
_Avoid_: sign, valence

**Strength**:
A relation's weight class: weak, moderate, strong, or foundational.
_Avoid_: weight

**Evidence (relation field)**:
The free-text justification recorded on a relation for why the edge exists.
Distinct from the `evidence` *entity type* (a piece of evidence as a node in
its own right); disambiguate when it matters.

**Status lifecycle**:
The five entity statuses — active, superseded, deprecated, retracted,
investigating — with validated transitions. Retracted is terminal; only
investigating may return to active; an unset status means active.

**Retraction**:
The normal sense of "deleting" an entity or relation: its status becomes
retracted and its history stays queryable. Actually erasing a record (a hard
delete) is the exception and must be requested explicitly.
_Avoid_: delete (ambiguous — say retract or hard-delete)

**Merge**:
Folding a duplicate entity into a primary one: observations union, relations
redirect, and the secondary is superseded.
_Avoid_: dedupe (that's the detection step, not the fold)

**Entity type families**:
The nineteen entity types cluster into: research epistemology (`concept`,
`claim`, `source`, `question`, `evidence`), synthesis products (`pattern`,
`insight`, `tension`, `convergence`), systems modeling (`system`, `variable`,
`loop`, `leverage_point`), narrative and procedure (`event`, `procedure`), and
reasoning machinery (`hypothesis`, `inference_rule`, `inference_trace`,
`research_session`).

**Tension**:
A synthesis product naming a genuine conflict between findings that each
appear sound on their own.
_Avoid_: contradiction (that's the relation between two specific records)

**Convergence**:
A synthesis product naming independent lines of work that arrive at the same
conclusion.

### Epistemic layer

**Confidence**:
The reliability of an entity or relation: a 0–1 score, the basis it rests on
(from direct observation down to speculation), and when it was last evaluated.
Scores map to five labels, speculative through very high.
_Avoid_: probability, certainty

**Provenance**:
The lineage of how a piece of knowledge entered the system — from what kind of
source, extracted by whom, by what method. Distinct from the `source` entity
type and the `sources` relation.
_Avoid_: origin

**Credit propagation**:
Confidence flowing along epistemic chains, so that what evidence supports
inherits changes in the evidence's standing.

**Stale belief**:
An entity whose confidence has not been evaluated recently enough to trust.

**Contested claim**:
A claim with conflicting evidence — supported and contradicted at once.

### Systems modeling

**Feedback loop**:
A closed causal cycle, classified reinforcing or balancing by the product of
its polarities. Loops are first-class: a `loop` entity records each one.

**Leverage point**:
A place in a modeled system where intervention is disproportionately
effective, ranked by Meadows level. Also first-class, as a `leverage_point`
entity.

### Semantic layer

**Embedding**:
The stored vector for an entity's text. Embedding is deliberate, not a side
effect — creating an entity does not embed it.
_Avoid_: vector (alone)

**Semantic gap**:
A pair of entities that are semantically similar yet unconnected in the graph
— a candidate missing relation. The per-entity view of the same idea is an
entity's *semantic neighbors*.

**Cluster**:
A grouping of entities by embedding similarity, discovered rather than
declared — no graph structure involved.

**Hybrid search**:
One search combining three signals — vector similarity, keyword full-text, and
graph structure — all read from the one store.

**Chunk**:
A unit of ingested document text, retrievable by search. Chunks are global to
the store, not scoped to any graph.
_Avoid_: passage, fragment

### Reasoning

**Semiring**:
The pluggable algebra a traversal composes path values with — reachability,
shortest or widest path, path counting, max-confidence — one engine, many
algebras.

**Adaptive routing**:
Analyzing a query into a plan that picks the right traversal and semiring
automatically, instead of the caller choosing.

**Inference rule**:
A declarative rule, stored in the graph as an entity, that derives new facts
from existing ones.

**Inference trace**:
The recorded derivation behind a derived fact — the answer to "why does this
exist?", stored as an entity.

**Verification**:
Checking a graph against its stated obligations: guards (per-record validity),
named invariants, and property specs.

**CEGIS**:
Counterexample-guided inductive synthesis — building a graph that satisfies
property specs by iterating against counterexamples.

### Generative and creative

**Synthesis**:
Coherent text produced from the graph by plan → traverse → realize, with
provenance carried through to every evidence unit.
_Avoid_: summarization

**Fidelity**:
The degree to which a text is structurally faithful to the graph it claims to
represent — checkable, not aspirational.

**Extraction**:
Turning documents or a codebase into entities and relations. Every extraction
is a tracked run that can be rolled back.

**Motif**:
A recurring subgraph shape found by frequent-subgraph mining.
_Avoid_: template

**Reification**:
Promoting a recurring motif into a first-class `pattern` entity, linked to its
instances by `crystallized_from`.
_Avoid_: crystallization (that names the lineage relation, not the act)

**Concept slippage**:
The substitution of a concept by a near neighbor to seed creative
alternatives during analogy transfer (in the Hofstadter sense).

**Far analogy**:
A structurally sound mapping between semantically distant domains, retrieved
by fingerprint → match → slip → transfer → score.

**CWSG**:
Copying with Substitution and Generation — the analogy-transfer algorithm:
copy source structure, substitute mapped concepts, generate novel entities for
the unmapped remainder.

**Frontier**:
The under-explored, under-described region of a graph, ranked by foraging
signals (staleness, coverage gaps, bridging potential) with patch-leaving
advice in the Marginal Value Theorem sense.

**Trigger queue**:
The queue of analogy candidates noticed during other work, held for later
processing — reported, never silently drained.

**Composite**:
A one-call pipeline that chains primitive commands into a higher-level
operation (reconnaissance, deep-dive, influence map, creativity loop, …).
_Avoid_: workflow, macro

### Work memory

**Work memory**:
The experiential layer: recorded outcomes of real work, citing the graph
records the work leaned on.

**Usage outcome**:
How a recorded piece of work turned out: `useful` (positive citation),
`dead_end` (led nowhere), or `corrected` (the graph was wrong). The last two
differ in where the fault lies.

**Reflect**:
Distilling recorded outcomes into standing lessons — time-decayed usage
scores and preferred / contested / dead-end standings.

**Postmortem**:
The after-action pass over finished work: resolving gaps it exposed, reifying
patterns it revealed, and propagating credit to what it used.

### 3D Memory Machine

**3D Memory Machine**:
The three axes classifying an entity as a memory: memory type (what cognitive
function it serves), domain (what area of life it belongs to), and durability
(how long it remains valid). Volatile durability requires an expiry. Note
"domain" here is this axis — not the domains of cross-domain mapping.

### Codebase graphs

**Codebase graph**:
A graph extracted from a repository: symbols joined by call, reference, and
containment relations anchored to file and line, plus a semantic layer.

**Calls vs. references**:
`calls` is invocation; `references` is non-invoking mention. Keep the
distinction — it is what makes "who calls X?" answerable.

**Semantic layer (codebase)**:
The written enrichment layered onto extracted structure — module purposes,
patterns, invariant claims, risks. Unrelated to embeddings despite the name;
say "embedding" or "semantic search" when you mean vectors.

**Blast radius**:
The reverse dependency reach of a symbol — everything that could break if it
changes.

**Self-model**:
The Loom's codebase graph of its own repository.

### Tapestry (visualization)

**Tapestry**:
The visualization frontend: a single-file page that reads a bundle, whether
exported statically or served live. The Loom is the substrate; Tapestry is
the loom's woven output made visible.

**Bundle**:
The assembled JSON of a graph scope — entities, relations, analytics, the
event history, the semantic projection — that every Tapestry mode reads.
(Wire name: TapestryBundle.)

**Scope**:
The subset of a graph a bundle covers: the whole graph, an entity's ego
neighborhood, the causal-only subgraph, a typed filter, or a search match.

**Views**:
Tapestry's five: Explorer (the graph), Overview (composition and health),
Systems (causal-loop diagram), Chronicle (history replay and diff), and
Semantic Map (the embedding projection).

### Research pipeline (shipped skills)

**Deep research**:
Autonomous multi-iteration research on one question, building source /
evidence / claim entities with calibrated confidence into a Loom graph.

**Hyper-research**:
The meta-orchestrator: independent questions extracted from a document, deep
research run per question in parallel onto one shared graph, then a
cross-cutting synthesis.

**Expedition**:
A discovery pass over accumulated graph structure looking for emergent
theories — reading what the graph already implies rather than researching
anew.
