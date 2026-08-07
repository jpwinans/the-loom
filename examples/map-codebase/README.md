# Map Codebase

An explained architecture map of any repository, built as a Loom graph:
tree-sitter extraction records *what exists* (files, symbols, call/import/
containment edges anchored to file and line), an LLM enrichment pass records
*what it means* (module purposes, design patterns, invariant claims, risks),
and a cartographer turns the graph into a written `ARCHITECTURE-MAP.md`, an
interactive visualization, and a query cheat-sheet. Re-runs are incremental
and **scale to the diff** — a routine post-merge update costs minutes.

## Usage

```
/map-codebase PATH                 # map a repository (incremental if mapped before)
/map-codebase PATH --full          # force fresh extraction of everything
/map-codebase PATH --no-enrich     # structural-only map, ~2 minutes, no semantic layer
/map-codebase PATH --thorough      # incremental, but whole-group re-enrichment + full cartograph
/map-codebase PATH --graph NAME --output DIR --no-tests
```

Launches [`.claude/workflows/map-codebase.js`](../../.claude/workflows/map-codebase.js)
in the background. Deliverables land in the target's `docs/architecture/`:
`ARCHITECTURE-MAP.md`, `QUERYING.md`, `map-manifest.json` (committed) and
`codebase-map.html` (generated, gitignored).

## Worked example: mapping The Loom itself

This repository maps itself — the graph `codebase-the-loom` is the project's
own **self-model**, and the committed [`docs/architecture/`](../../docs/architecture/)
is its output. To reproduce from a fresh clone:

```bash
uv sync && docker compose up -d falkordb
/map-codebase .          # full run on first mapping: extraction, ~45 enrichment
                         # groups in parallel, embed, cartograph
```

[`docs/architecture/map-manifest.json`](../../docs/architecture/map-manifest.json)
records the commit the graph describes. After merging a PR, the same command
becomes a cheap update:

```bash
/map-codebase .          # incremental: replays the git diff since the manifest commit
```

The incremental run classifies each changed module group by diff magnitude —
**carried** (tiny diff: the semantic layer stands, nothing re-read), **delta**
(a minority of files changed: only the notes citing them are superseded and
rewritten), or **rewrite** (substantial change: full re-enrichment) — and the
cartographer patches the existing map rather than re-deriving it. Measured on
this repo: a trivial merge re-stamps in ~3 minutes; a typical code PR takes a
quarter-hour; the pre-fast-path equivalent was ~45 minutes.

Once mapped, agents (and you) query the graph instead of grepping — the full
recipe sheet is [`docs/architecture/QUERYING.md`](../../docs/architecture/QUERYING.md):

```bash
uv run loom explore '{"name": "resolve_entity_ref", "graph": "codebase-the-loom"}'
    # definition, callers, callees, imports, containment, semantic layer — one call, budgeted
uv run loom find-callers '{"name": "assemble_bundle", "graph": "codebase-the-loom"}'
uv run loom blast-radius '{"name": "GraphSpace.commit", "graph": "codebase-the-loom"}'
    # what breaks if I change this — reverse dependency reach, grouped by module
uv run loom list-entities '{"entityType": "tension", "compact": true, "graph": "codebase-the-loom"}'
    # the risks the enrichment pass recorded, each anchored to file:line
```

## How it uses The Loom

- **Two layers, one graph.** Deterministic structure (`system` file entities,
  symbol entities, `calls`/`references`/`part_of`/`requires` edges with
  `file:line` evidence) and an LLM-written semantic layer (`concept`,
  `pattern`, `claim`, `tension` entities stamped `map_layer: semantic`),
  linked so `explore` returns both in one answer.
- **Updates supersede; they never overwrite.** The incremental path replays a
  git diff: changed files' records are superseded and re-extracted, renames
  land as delete-plus-add, and a re-enriched group's old notes are retired
  with `status: superseded` — so `loom session-changelog` can answer "how did
  the architecture change since the last mapping".
- **Consumption commands are budgeted honesty.** `explore`, `find-callers`,
  and `blast-radius` answer within a token budget, truncating round-robin
  with explicit rollups — an agent always knows what it did *not* see.
- **The written map is a projection.** Every claim in `ARCHITECTURE-MAP.md`
  traces to a graph entity; the graph is the source of truth, the document a
  view of it, and the manifest the anchor tying both to a commit.
- **Verification gates the pipeline.** An enrichment batch that attempts
  writes and verifies none halts the run; entity creation is idempotent
  across retries; the cartographer is read-only on the graph by contract.

## Keeping a map current

The intended rhythm: merge a PR, run `/map-codebase .`, commit the refreshed
`docs/architecture/` (this repo does it on `chore/map-refresh-*` branches).
The manifest's commit anchor makes the whole thing reproducible — any clone
can rebuild the identical graph, and `loom self-model-update` exists as the
one-call shortcut for a repository updating its own self-model.
