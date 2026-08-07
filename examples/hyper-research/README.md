# Hyper Research

The meta-orchestrator above [deep-research](../deep-research/README.md): give
it a context document — a synthesis, a design doc, a messy braindump — and it
extracts the independent questions inside, runs a full deep-research pipeline
**per question in parallel onto one shared Loom graph**, consolidates the
overlap, and writes a cross-cutting report that no single question's research
would have produced.

## Usage

```
/hyper-research research/reports/some-synthesis.md
/hyper-research DOC --topic "framing topic"      # override the inferred framing
/hyper-research DOC --graph my-graph             # accumulate into an existing graph
/hyper-research DOC --output reports/out.md      # report destination
/hyper-research DOC --category research          # Loom doc-store category for the report
```

Launches [`.claude/workflows/hyper-research.js`](../../.claude/workflows/hyper-research.js)
in the background; reports the session id, graph name, report path, extracted
question ids, completed/failed question counts, and the cross-cutting themes.

**Pipeline shape:** comprehend the document → explore what the graph already
knows → extract independent questions → per-question deep-research fan-out
(each invokes the deep-research workflow **by name**) → consolidation barrier
→ expedition and cross-session discovery → cross-cutting synthesis → the
report is written to `research/reports/{slug}-{date}.md` *and ingested back
into the Loom's document store*, so the next hyper-research run can read this
one's conclusions.

> **Set expectations before you launch.** This multiplies everything about
> [deep-research](../deep-research/README.md) by the question count. A rich
> context document that yields several substantive questions is a
> most-of-the-day background run with token spend to match, and the shared
> agent-concurrency cap makes the speedup sub-linear (parallel questions
> queue rather than all running at once). Launch it, walk away, and let the
> completion notification find you.

## Why not just several chat sessions?

Running N separate research chats gets you N reports that have never met.
The fan-out here writes every question's research into **one graph**, which
buys what independent sessions structurally cannot produce:

- **Cross-question contradictions surface.** Two questions' findings that
  disagree get caught by the consolidation pass, not discovered months later.
- **Convergences become first-class.** Independent lines of work arriving at
  the same conclusion are recorded as evidence of strength, with the lineage
  to prove the independence.
- **Overlap merges instead of duplicating.** Concurrent writers may create
  near-duplicates; consolidation folds them with full history rather than
  leaving N copies of the same claim.
- **The next round starts smarter.** The synthesis is ingested back into the
  store, so a future run reads this run's conclusions before extracting its
  questions.

## How it uses The Loom

Everything deep-research demonstrates, plus the multi-writer patterns:

- **One shared graph, many concurrent writers.** Every per-question pipeline
  writes into the same graph with per-question provenance tags. Concurrent
  duplicates are allowed to coexist non-fatally — the post-barrier
  consolidation pass finds them by semantic similarity and folds them with
  `loom merge-entities` (observations union, relations redirect, the
  secondary superseded, never deleted).
- **Cross-question structure is the payoff.** After consolidation, the
  synthesis stage reads what no single question produced:
  `loom cross-session-contradictions` (two questions' research disagreeing
  without noticing), convergences where independent lines of work arrived at
  the same conclusion, and bridges between question subgraphs.
- **The report closes the loop.** `loom ingest-content` puts the final
  synthesis into the document store under a category (default `research`), so
  it is chunked, embedded, and searchable — the output of this run is legible
  input to the next one.
- **Session provenance keeps the fan-out auditable.** Each question's records
  carry their session tag; `loom session-changelog` and the per-question
  `research_session` entities reconstruct which question contributed what.

**Concurrency note:** the per-question fan-out shares the Workflow agent cap
with each pipeline's internal agents, so wall-clock speedup is sub-linear in
question count — parallel runs queue against the cap rather than all running
at once.

## After a run

```bash
uv run loom multi-graph-landscape '{}'                      # where this graph sits in the ecosystem
uv run loom cross-session-contradictions '{"graph": "<g>"}' # what the questions disagree on
uv run loom list-entities '{"entityType": "convergence", "graph": "<g>"}'
uv run loom hybrid-search '{"query": "<theme>"}'            # the ingested report is searchable
```

The natural cycle: hyper-research produces a synthesis → the synthesis becomes
the next run's context document → each round's questions get sharper.
