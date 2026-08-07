# Querying the codebase graph

If you are an agent working in this repository: this is the fast path. The graph already
holds every symbol, call, import, invariant, convention and risk in the tree, anchored to
a file and a line. Query it before you grep.

- **Graph name:** `codebase-the-loom`
- **Commit it describes:** `0343de03f15efbb6ce1d329e8f8703e18bad4900`
- **Transport:** the `loom` CLI over Bash. Every call is
  `loom <command> '<json>'` with kebab-case commands, camelCase JSON fields, and a
  `"graph"` field on every call. If `loom` is not on `PATH`, prefix with
  `uv run --directory <path-to-your-loom-checkout>`. There is no MCP server.

The working tree was clean when this edition was built.

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
`module_group`. The 46 current groups:

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
repo-root-1  repo-root-2  docs  docs-architecture  examples
```

Seven further labels survive from earlier runs and still carry 130 records between them:
`docs-1`, `root-1`, `tests-fixtures`, and the coarser frontend partition `tapestry-src-1` …
`tapestry-src-4`. Prefer the current ids — the legacy records overlap them and are older
readings of the same files. Unlike the previous edition of this guide, none of the legacy
records is orphaned: every one is anchored to a file that exists, so `loom explore` on a
file will surface them. The `docs-2` … `docs-4` labels the previous edition warned about no
longer carry any record.

## Recipes

One command per question. Result shapes below are what a typical call actually returns —
not full output.

### Where is `X` defined?

```bash
loom explore '{"name": "assemble_bundle (bundle)", "graph": "codebase-the-loom"}'
```

→ definition anchor, callers in, calls out, imports, imported-by, containment, inheritance
and the written layer attached to it, all budgeted into one document.
Typical for this symbol: 1 definition line (`theloom/viz/bundle.py:79-165`), 15 callers,
10 callees, 13 written-layer notes, and a truncation block declaring 7 of 39 rows cut at
the default 2,000-token budget.

### Who calls `X`?

```bash
loom find-callers '{"name": "compute_fingerprint (fingerprint)", "graph": "codebase-the-loom"}'
```

→ ranked callers, each anchored at its call site in the caller's file.
Typical: 3–8 rows plus a per-file rollup; this symbol returns 4 with nothing cut.

### What does `X` call?

```bash
loom find-callees '{"name": "update_codebase_diff (codebasediff)", "graph": "codebase-the-loom"}'
```

→ ranked callees with call-site anchors; this symbol returns 10, nothing cut. Wider
functions truncate with an explicit `shown + cut = total` block.

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
rather than hidden. For that file: 19 seeded members, 162 affected symbols, 100 listed and
62 counted-not-listed, 1 suppressed hub, depth 4. A single function is much narrower —
`compute_fingerprint (fingerprint)` reaches 15 symbols across 4 modules with nothing cut.
Name a file when you want the module-level answer and a symbol when you want the precise one.

### What are the risks here?

```bash
loom list-entities '{"entityType": "tension", "compact": true, "limit": 40, "graph": "codebase-the-loom"}'
```

→ each record carries `pole_a`, `pole_b`, an `anchor` and `implications`. Narrow with
`"query": "<module group id>"` to scope to one subsystem. Typical: 2–13 risks per group,
median 7; 347 in the graph.

### What must stay true here?

```bash
loom list-entities '{"query": "theloom-semantic", "entityType": "claim", "compact": true, "graph": "codebase-the-loom"}'
```

→ invariants with `statement`, `anchor` and `consequence_if_broken`.
Typical: 5–32 per group, median 10; 629 in the graph.

### What conventions does this code follow?

```bash
loom list-entities '{"query": "theloom-operations-2", "entityType": "pattern", "compact": true, "graph": "codebase-the-loom"}'
```

→ named conventions with `description`, `instances` and `mechanism`.
Typical: 3–12 per group, median 7; 355 in the graph.

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

→ the hop sequence as an id list. Typical: 2–4 hops (this pair returns 3, via
`theloom/operations/common.py`). Endpoints take exactly one of `sourceId`/`sourceName` and
one of `targetId`/`targetName`; mixing them returns a typed `VALIDATION_ERROR`.

```bash
loom explain-path '{"sourceName": "file:theloom/cli/registry.py", "targetName": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'
```

→ a narrative walk of the same hops. The name form works at this commit — the
`OPERATION_ERROR` an older edition of this guide documented for `sourceName`/`targetName`
is fixed. It does require an LLM to be configured; without one it returns a typed
`OPERATION_ERROR` carrying the provider's message rather than a partial answer, so fall back
to `find-shortest-path` and read the hops yourself.

### Everything about one symbol, at length

```bash
loom entity-deep-dive '{"name": "run_composite (framework)", "compact": true, "graph": "codebase-the-loom"}'
```

→ a `{result, metadata}` envelope whose result carries the neighbourhood, relations,
semantic neighbours and provenance, each section with its own timing and error field.

### What shape is the graph in?

```bash
loom graph-stats '{"graph": "codebase-the-loom"}'
loom analyze-centrality '{"algorithm": "degree", "limit": 15, "graph": "codebase-the-loom"}'
```

→ counts by record and relation type; a ranked `{id, name, entityType, score}` array.
Typical: one stats document (9,350 records and 19,315 relationships including superseded
versions; 6,530 and 13,868 live — the live relation figure is carried forward from an
earlier commit, see `docs/architecture/ARCHITECTURE-MAP.md` §1); 15 ranked rows.

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
