# Querying the codebase graph

If you are an agent working in this repository: this is the fast path. The graph already
holds every symbol, call, import, invariant, convention and risk in the tree, anchored to
a file and a line. Query it before you grep.

- **Graph name:** `codebase-the-loom`
- **Commit it describes:** `21466d5250d7ce760079705305a422077e36f17d`
- **Transport:** the `loom` CLI over Bash. Every call is
  `loom <command> '<json>'` with kebab-case commands, camelCase JSON fields, and a
  `"graph"` field on every call. If `loom` is not on `PATH`, prefix with
  `uv run --directory <path-to-your-loom-checkout>`. There is no MCP server.

## Naming conventions

Entity-addressed reads take a `name` instead of an `id` (exactly one of the two).

| Kind | Name form | Example |
| --- | --- | --- |
| File | `file:<repo-relative path>` | `file:theloom/store/falkor.py` |
| Function / method | `<symbol> (<module stem>)` | `assemble_bundle (bundle)`, `FalkorGraphStore.update_entity (falkor)` |
| Class / type / interface | `<Name> (<module stem>)` | `CommandInput (common)`, `TapestryBundle (schema)` |
| Variable / constant | `<name> (<module stem>)` | `MAX_STDIN_BYTES (io)` |
| External package | `pkg:<name>` | `pkg:falkordb`, `pkg:sigma` |
| Module purpose | `<directory> purpose` | `theloom/store purpose`, `tapestry/src/design purpose` |

Ambiguous names are refused with a candidate list rather than guessed — pick one from the
list and re-ask.

## Module group ids

The written layer (purposes, conventions, invariants, risks) is stamped with a
`module_group`. The 45 current groups:

```
theloom               theloom-algebra       theloom-analysis      theloom-cli
theloom-composites-1  theloom-composites-2  theloom-documents     theloom-exploration
theloom-extraction    theloom-graph         theloom-operations-1  theloom-operations-2
theloom-operations-3  theloom-reification   theloom-semantic      theloom-store
theloom-symbolic      theloom-synthesis     theloom-verification  theloom-viz
tapestry-1            tapestry-2            tapestry-e2e          tapestry-src
tapestry-src-design   tapestry-src-lib      tapestry-src-state
tapestry-src-views-chronicle  tapestry-src-views-explorer
tapestry-src-views-overview   tapestry-src-views-semantic
tapestry-src-views-systems
tests-1  tests-2  tests-3  tests-4  tests-5  tests-6
tests-fixtures-multi  tests-fixtures-repo   tests-fixtures-repo-src
repo-root-1  repo-root-2  docs  docs-architecture
```

Ten further labels survive from earlier runs and still carry records: `docs-1` … `docs-4`,
`root-1`, `tests-fixtures`, and the coarser frontend partition `tapestry-src-1` …
`tapestry-src-4`. Their content overlaps the current groups and names files that have since
moved; prefer the current ids.

## Recipes

One command per question. Result shapes below are what a typical call actually returns —
not full output.

### Where is `X` defined?

```bash
loom explore '{"name": "assemble_bundle (bundle)", "graph": "codebase-the-loom"}'
```

→ definition anchor, callers in, calls out, imports, imported-by, containment, inheritance
and the written layer attached to it, all budgeted into one document.
Typical: 1 definition line, 3 callers, 9 callees, 6 imports, 1 file rollup.

### Who calls `X`?

```bash
loom find-callers '{"name": "compute_fingerprint (fingerprint)", "graph": "codebase-the-loom"}'
```

→ ranked callers, each anchored at its call site in the caller's file.
Typical: 3–8 rows plus a per-file rollup.

### What does `X` call?

```bash
loom find-callees '{"name": "update_codebase_diff (codebasediff)", "graph": "codebase-the-loom"}'
```

→ ranked callees with call-site anchors. Typical: 10–30 rows, truncated with an explicit
`shown + cut = total` block when over budget.

### What does module `Y` do?

```bash
loom list-entities '{"query": "theloom/store purpose", "entityType": "concept", "compact": true, "graph": "codebase-the-loom"}'
```

→ 1 record whose observations carry `purpose` and `key_files`. Then `loom explore` the files
it names. Typical: one ~1,500-character purpose paragraph.

Purpose records are named after the **directory**, not the group id — `theloom/store purpose`,
not `theloom-store purpose`, which matches nothing. A handful carry a narrative title instead
(`Tapestry build and contract toolchain purpose`); when in doubt, search by group id, which
matches the `module_group` observation on every written record:

```bash
loom list-entities '{"query": "theloom-store", "entityType": "concept", "compact": true, "graph": "codebase-the-loom"}'
```

### What breaks if I change `Z`?

```bash
loom blast-radius '{"name": "file:theloom/semantic/embed.py", "graph": "codebase-the-loom"}'
```

→ reverse dependency reach grouped by module, with hub suppression and truncation declared
rather than hidden. For that file: 19 seeded members, 161 affected symbols across 10 modules,
100 listed and 61 counted-not-listed, 1 suppressed hub. A single function is much narrower —
`compute_fingerprint (fingerprint)` reaches 15 symbols across 4 modules with nothing cut.
Name a file when you want the module-level answer and a symbol when you want the precise one.

### What are the risks here?

```bash
loom list-entities '{"entityType": "tension", "compact": true, "limit": 40, "graph": "codebase-the-loom"}'
```

→ each record carries `pole_a`, `pole_b`, an `anchor` and `implications`. Narrow with
`"query": "<module group id>"` to scope to one subsystem. Typical: 3–13 risks per group,
344 in the graph.

### What must stay true here?

```bash
loom list-entities '{"query": "theloom-semantic", "entityType": "claim", "compact": true, "graph": "codebase-the-loom"}'
```

→ invariants with `statement`, `anchor` and `consequence_if_broken`.
Typical: 5–35 per group, 630 in the graph.

### What conventions does this code follow?

```bash
loom list-entities '{"query": "theloom-operations-2", "entityType": "pattern", "compact": true, "graph": "codebase-the-loom"}'
```

→ named conventions with `description`, `instances` and `mechanism`.
Typical: 4–11 per group, 367 in the graph.

### I only know roughly what I am looking for

```bash
loom hybrid-search '{"query": "how are events committed atomically", "graph": "codebase-the-loom"}'
```

→ semantic plus graph-expansion ranking over the whole graph. Typical: 10 rows spanning
code, invariants and risks.

### How do these two things connect?

```bash
loom find-shortest-path '{"sourceName": "file:theloom/cli/registry.py", "targetName": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'
```

→ the hop sequence as an id list. Typical: 2–4 hops. Endpoints take exactly one of
`sourceId`/`sourceName` and one of `targetId`/`targetName`; mixing them returns a typed
`VALIDATION_ERROR`.

`loom explain-path` gives a prose reading of the same hops, but at this commit only its
**id** form works — passing `sourceName`/`targetName` fails with
`OPERATION_ERROR: FalkorDocStore.list_entities() takes 1 positional argument but 2 were
given` (`theloom/operations/synthesis.py:525-541` resolves names against a document-store
view that the shared resolver cannot query). Feed it the ids that `find-shortest-path`
returned:

```bash
loom explain-path '{"sourceId": "<id>", "targetId": "<id>", "graph": "codebase-the-loom"}'
```

→ a narrative walk of the path. Requires an LLM to be configured; without one it returns a
typed `OPERATION_ERROR` from the provider rather than a partial answer.

### Everything about one symbol, at length

```bash
loom entity-deep-dive '{"name": "run_composite (framework)", "compact": true, "graph": "codebase-the-loom"}'
```

→ a multi-section bundle: neighbourhood, relations, semantic neighbours, provenance.
Typical: 5 sections, each with its own timing and error field.

### What shape is the graph in?

```bash
loom graph-stats '{"graph": "codebase-the-loom"}'
loom analyze-centrality '{"algorithm": "degree", "limit": 15, "graph": "codebase-the-loom"}'
```

→ counts by record and relation type; a ranked `{id, name, entityType, score}` array.
Typical: one stats document; 15 ranked rows.

## Make agents use this graph

Optional, and the repository owner's choice. This `PreToolUse` hook nudges agents toward the
graph whenever they reach for `Grep` or `Glob`. It only ever injects context — it never
blocks a tool call, never returns a permission decision, and ends in `|| true` so it cannot
fail the call. Drop it into `.claude/settings.json`:

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

The guard on `map-manifest.json` makes it self-disabling: in a checkout without a map, the
hook emits nothing.

---

Query the graph before grepping the repository. The graph already has the answer, and it
comes back anchored to a file and a line.
