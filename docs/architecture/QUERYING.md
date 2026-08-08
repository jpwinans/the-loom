# Querying the codebase graph

If you are an agent working in this repository: **this graph already knows where
things are.** Query it before you grep. Every symbol, call edge, import,
invariant, risk and convention in this repo is a record anchored to a file and a
line, and one `loom` call usually replaces a dozen searches.

- **Graph name:** `codebase-the-loom`
- **Commit described:** `c470c03fb041fd0d98a659edb109c9cfa85cbf8d`
- **Prerequisite:** a running store — `docker compose up -d falkordb`
- **If `loom` is not on your PATH:** prefix every command with
  `uv run --directory <path-to-your-loom-checkout>` (inside this repo, plain
  `uv run loom …` works).

Every command is `loom <name> '<json>'`, kebab-case command, camelCase JSON
fields, and a `"graph"` field on every call. Entity-addressed reads take
`"name"` instead of `"id"` — exactly one of the two.

> Working tree was dirty when this graph was built (uncommitted edits under
> `.claude/`, `README.md`, `.gitignore`), so a handful of anchors may be off by
> a few lines in those files.

## Naming conventions

You address records by name, so it helps to know how names are written:

| Kind | Name form | Example |
|---|---|---|
| File | `file:<repo-relative-path>` | `file:theloom/store/falkor.py` |
| External package | `pkg:<name>` | `pkg:typing` |
| Function / method | `<symbol> (<module-stem>)` | `run_handler (registry)`, `FalkorGraphStore.create_entity (falkor)` |
| Class | `<Class> (<module-stem>)` | `CommandInput (common)` |
| Module-level value | `<NAME> (<module-stem>)` | `MAX_LINKS_PER_DOC (doclinks)` |
| Written-layer note | plain English sentence | `Deletion invalidates by default; hard=True is the only path that destroys history` |

Substring matching works: `loom explore '{"name": "run_handler", ...}'` resolves
if it is unambiguous, and lists the candidates if it is not.

## Module group ids

The written layer is tagged with `module_group: <id>`. Current ids:

```
repo-root-1  repo-root-2  docs  docs-architecture  examples
theloom  theloom-cli  theloom-store  theloom-operations-1  theloom-operations-2
theloom-operations-3  theloom-composites-1  theloom-composites-2  theloom-graph
theloom-algebra  theloom-analysis  theloom-semantic  theloom-documents
theloom-extraction  theloom-exploration  theloom-reification  theloom-symbolic
theloom-synthesis  theloom-verification  theloom-viz
tapestry-1  tapestry-2  tapestry-e2e  tapestry-src  tapestry-src-design
tapestry-src-lib  tapestry-src-state  tapestry-src-views-explorer
tapestry-src-views-overview  tapestry-src-views-systems
tapestry-src-views-chronicle  tapestry-src-views-semantic
tests-1  tests-2  tests-3  tests-4  tests-5  tests-6
tests-fixtures-multi  tests-fixtures-repo  tests-fixtures-repo-src
```

Seven legacy ids survive from earlier runs whose partition differed —
`root-1`, `docs-1`, `tapestry-src-1`, `tapestry-src-2`, `tapestry-src-3`,
`tapestry-src-4`, `tests-fixtures`. Their records are still valid but describe an
older slicing of the same files; prefer the ids above.

## Recipes

One command per question. The arrow shows a realistic result size, not the full
output.

| Question | Command |
|---|---|
| Where is `X` defined? | `loom explore '{"name": "X", "graph": "codebase-the-loom"}'` → definition line, callers, callees, imports, containment, inheritance and the written notes attached to it, in one budgeted call |
| Who calls `X`? | `loom find-callers '{"name": "X", "graph": "codebase-the-loom"}'` → ranked, each anchored at its call site (typically 3–20 rows plus a per-file rollup) |
| What does `X` call? | `loom find-callees '{"name": "X", "graph": "codebase-the-loom"}'` → outbound calls with call-site anchors |
| What breaks if I change `Z`? | `loom blast-radius '{"name": "Z", "graph": "codebase-the-loom"}'` → reverse dependency reach grouped by module, with hubs suppressed and the suppression reported |
| What does module `Y` do? | `loom list-entities '{"query": "Y purpose", "entityType": "concept", "compact": true, "graph": "codebase-the-loom"}'` → the group's purpose paragraph and key files; then `loom explore` on those files |
| What conventions does this area follow? | `loom list-entities '{"query": "Y", "entityType": "pattern", "compact": true, "graph": "codebase-the-loom"}'` → named patterns with mechanism and consequence |
| What must stay true here? | `loom list-entities '{"query": "Y", "entityType": "claim", "compact": true, "graph": "codebase-the-loom"}'` → invariants, each with an `anchor:` and a `consequence_if_broken:` |
| What are the risks here? | `loom list-entities '{"entityType": "tension", "compact": true, "limit": 40, "graph": "codebase-the-loom"}'` → two-sided tensions with anchors (347 exist; narrow with `query`) |
| Everything about one symbol, deeply | `loom entity-deep-dive '{"name": "X", "compact": true, "graph": "codebase-the-loom"}'` → neighbourhood, relations and observations in one fetch |
| Which files does `X` sit between? | `loom get-neighbors '{"name": "X", "compact": true, "limit": 25, "graph": "codebase-the-loom"}'` → one row per unique neighbour |
| Find it by meaning, not by name | `loom hybrid-search '{"query": "how are embeddings skipped when unchanged", "graph": "codebase-the-loom"}'` → semantically ranked records, graph-expanded and MMR-diversified |
| How do two areas connect? | `loom find-shortest-path '{"from": "A", "to": "B", "graph": "codebase-the-loom"}'` then `loom explain-path` on the same endpoints for prose |
| What are the load-bearing files? | `loom analyze-centrality '{"algorithm": "degree", "limit": 15, "graph": "codebase-the-loom"}'` → ranked `{id, name, entityType, score}` |
| Where are the import cycles? | `loom detect-cycles '{"includePaths": true, "graph": "codebase-the-loom"}'` → member id chains (see the map's §4 for the adjudicated reading) |
| How big is the graph? | `loom graph-stats '{"graph": "codebase-the-loom"}'` → record and relation counts by type |

Keep responses small: `"compact": true` projects five fields, and `"limit": N`
caps rows and reports the untruncated total.

Two CLI invariants worth knowing if you ever write to a Loom graph (this one is
read-only for consumers): `create-relation` requires `polarity` (`"+"`/`"-"` for
causal types, `null` otherwise), `strength`, and `evidence`; and embedding is a
separate step — `loom embed-entities '{"graph": "..."}'` after a batch.

## Make agents use this graph

Optional, and the repo owner's choice: a Claude Code hook that reminds an agent
the graph exists whenever it reaches for `Grep` or `Glob`. It only ever injects
context — it never blocks a tool call, never returns a permission decision, and
ends in `|| true` so it cannot fail the call. It self-disables in any checkout
where `docs/architecture/map-manifest.json` is absent. Add to
`.claude/settings.json` in the mapped repo:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Grep|Glob",
        "hooks": [
          {
            "type": "command",
            "command": "{ [ -f \"${CLAUDE_PROJECT_DIR:-.}/docs/architecture/map-manifest.json\" ] && echo \"{\\\"hookSpecificOutput\\\":{\\\"hookEventName\\\":\\\"PreToolUse\\\",\\\"additionalContext\\\":\\\"This repo has a Loom codebase graph - see docs/architecture/QUERYING.md. Prefer loom explore / find-callers / find-callees / blast-radius / hybrid-search for where-defined, who-calls, impact and concept questions; grep only what the graph cannot answer.\\\"}}\"; } || true",
            "timeout": 5,
            "statusMessage": "Checking for codebase graph"
          }
        ]
      }
    ]
  }
}
```

---

**Query the graph before grepping the repo.** The graph already has the answer,
anchored to a file and a line — and it also has the reason, which grep never
will.
