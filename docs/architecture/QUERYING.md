# Querying the codebase graph

If you are an agent working in this repository, query this graph before you grep.
The graph already holds every symbol, call edge, import and containment
relationship in the repo, plus a written layer of purposes, patterns, invariants
and risks — and every answer comes back anchored to a file and line.

**Graph name:** `codebase-the-loom`
**Commit described:** `067a5b833e3f9e9ca898288403312140169f8df5`
**Access:** the Loom CLI over Bash — `loom <command> '<json>'`. If `loom` is not on
`PATH`, prefix with `uv run --directory ~/Dropbox/Development/the-loom`. There is
no MCP server.

Every call takes `"graph": "codebase-the-loom"`. Entity-addressed reads take
`name` **or** `id`, never both. `list-entities`, `read-entity`, `get-neighbors`,
`get-relations` and `entity-deep-dive` all accept `"compact": true` and
`"limit": N` to keep responses small.

## Module group ids

These are the labels attached to every written note, usable as a `module_group`
filter when you want the notes for one area:

```
docs-1  docs-2  docs-3  docs-4
root-1  root-2
tapestry-1  tapestry-2  tapestry-e2e
tapestry-src-1  tapestry-src-2  tapestry-src-3  tapestry-src-4
tests-1  tests-2  tests-3  tests-4  tests-fixtures
theloom            theloom-algebra      theloom-analysis    theloom-cli
theloom-composites-1  theloom-composites-2
theloom-documents  theloom-exploration  theloom-extraction  theloom-graph
theloom-operations-1  theloom-operations-2  theloom-operations-3
theloom-reification  theloom-semantic     theloom-store       theloom-symbolic
theloom-synthesis    theloom-verification theloom-viz
```

All 38 groups are enriched. None were skipped.

## Recipes

| Question | Command | Typical response size |
|---|---|---|
| Where is `X` defined? | `loom explore '{"name": "X", "graph": "codebase-the-loom"}'` | definition + callers/callees/imports/containment/inheritance in one call; e.g. `CommandInput (common)` → 1 definition, 149 subclasses (shown truncated), 1 file rollup |
| Who calls `X`? | `loom find-callers '{"name": "X", "graph": "codebase-the-loom"}'` | ranked and anchored at each call site; e.g. → 6 callers, 1 file rollup |
| What does `X` call? | `loom find-callees '{"name": "X", "graph": "codebase-the-loom"}'` | → 4–20 callees, anchored |
| What does module `Y` do? | `loom list-entities '{"query": "Y", "entityType": "concept", "compact": true, "graph": "codebase-the-loom"}'` then `loom explore` on the key files it names | → 1 purpose note listing 5 key files |
| What breaks if I change `Z`? | `loom blast-radius '{"name": "Z", "graph": "codebase-the-loom"}'` | reverse dependency reach grouped by module; e.g. `theloom/store/falkor.py` → 30 direct dependants across 8 modules |
| What are the risks here? | `loom list-entities '{"entityType": "tension", "compact": true, "graph": "codebase-the-loom"}'` | → 201 active risk notes, each with `pole_a` / `pole_b` / `anchor` |
| What invariants must I not break? | `loom list-entities '{"entityType": "claim", "compact": true, "graph": "codebase-the-loom"}'` | → 332 invariants, each with `statement`, `anchor`, `consequence_if_broken` |
| What conventions does this codebase follow? | `loom list-entities '{"entityType": "pattern", "compact": true, "graph": "codebase-the-loom"}'` | → 240 patterns, each with `mechanism` and `instances` |
| Full profile of one symbol | `loom entity-deep-dive '{"name": "X", "compact": true, "graph": "codebase-the-loom"}'` | definition, neighbourhood, centrality, semantic neighbours |
| I only know roughly what it does | `loom hybrid-search '{"query": "<description>", "limit": 10, "graph": "codebase-the-loom"}'` | → 10 ranked hits across code and notes |
| Which files does `F` import / who imports `F`? | `loom explore '{"name": "file:path/to/F.py", "graph": "codebase-the-loom"}'` | file records are named `file:<repo-relative path>` |
| Immediate neighbours only | `loom get-neighbors '{"name": "X", "compact": true, "limit": 12, "graph": "codebase-the-loom"}'` | → 12 rows with relation type and direction |
| How are `X` and `Y` connected? | `loom find-shortest-path '{"from": "X", "to": "Y", "graph": "codebase-the-loom"}'` then `loom explain-path` | → hop chain with relation types |
| Whole-graph shape | `loom graph-stats '{"graph": "codebase-the-loom"}'` | → counts by entity and relation type |
| Hubs | `loom analyze-centrality '{"metric": "degree", "limit": 15, "graph": "codebase-the-loom"}'` | → 15 rows of `{id, name, entityType, score}` — no id follow-up needed |

## Naming conventions in this graph

- **Files** are `file:<repo-relative path>` — e.g. `file:theloom/store/falkor.py`.
- **External packages** are `pkg:<name>` — e.g. `pkg:typing`.
- **Functions, methods, classes and constants** are `<symbol> (<module basename>)`
  — e.g. `CommandInput (common)`, `_extract_calls (treesitter)`. The parenthesised
  part disambiguates same-named symbols across modules.
- **Written notes** (purposes, patterns, invariants, risks) are full sentences and
  carry `map_layer: semantic` plus a `module_group:` stamp in their observations.
  Filter on those two strings to separate the written layer from the extracted
  structure.

## Two things to know before you trust an answer

1. **The two halves of this repo are structurally disconnected.** No edge crosses
   between the Python package and the `tapestry/` TypeScript workspace — they
   communicate through a built HTML file, not a symbol. A `blast-radius` on a
   Python symbol will never reach the frontend, and that is correct.
2. **CSS files and most documentation are present but have no code edges.** Three
   CSS files are fully isolated; the `docs/` clusters connect only through their
   own written notes. If you need to know which component a stylesheet belongs to,
   the graph cannot tell you — read the import in the component.

Query the graph before grepping the repo — the graph already has the answer
anchored to a file and line.
