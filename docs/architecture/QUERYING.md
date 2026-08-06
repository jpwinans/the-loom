# Querying the codebase graph

**For agents:** this repository has a Loom knowledge graph of its own source. When you
need to know where something is defined, who calls it, what a module does, or what
breaks if you change it — ask the graph. It already has the answer, anchored to a file
and a line, and it costs one command instead of a grep-and-read loop.

- **Graph name:** `codebase-the-loom`
- **Commit the graph describes:** `e4a12a1b188e5391ec431a8c5754d2fa4733b1f9`
- **Written walkthrough:** [ARCHITECTURE-MAP.md](ARCHITECTURE-MAP.md)
- **CLI:** `loom <command> '<json>'` — kebab-case commands, camelCase JSON fields, and a
  `graph` field on every call. If `loom` is not on `PATH`, prefix with
  `uv run --directory /Users/jameswinans/Dropbox/Development/the-loom`. There is no MCP
  server.

Entity-addressed reads take `name` instead of `id` (exactly one of the two).
`list-entities`, `read-entity`, `get-neighbors`, `get-relations` and `entity-deep-dive`
all accept `"compact": true` and `"limit": N` to keep responses small.

## Naming conventions in this graph

| Thing | How it is named | Example |
|---|---|---|
| A file | `file:<repo-relative path>` | `file:theloom/store/falkor.py` |
| An external package | `pkg:<import name>` | `pkg:falkordb` |
| A function or method | `<symbol> (<module stem>)` | `assemble_bundle (bundle)` |
| A method on a class | `<Class>.<method> (<module stem>)` | `MultiGraph.get_store (multigraph)` |
| A class, interface or type | `<Name> (<module stem>)` | `CommandInput (common)` |
| A module group's purpose | `<group> purpose` | `theloom/store purpose` |

## Module groups

Each group has one purpose record, plus its patterns, invariants and strains. The group
id appears in every written record as `module_group: <id>`.

`docs-1` … `docs-4` · `repo-root-1` · `root-1` · `root-2` · `tapestry-1` · `tapestry-2` ·
`tapestry-e2e` · `tapestry-src` · `tapestry-src-1` … `tapestry-src-4` ·
`tapestry-src-lib` · `tests-1` … `tests-6` · `tests-fixtures` · `tests-fixtures-repo` ·
`tests-fixtures-repo-src` · `theloom` · `theloom-algebra` · `theloom-analysis` ·
`theloom-cli` · `theloom-composites-1` · `theloom-composites-2` · `theloom-documents` ·
`theloom-exploration` · `theloom-extraction` · `theloom-graph` ·
`theloom-operations-1` … `theloom-operations-3` · `theloom-reification` ·
`theloom-semantic` · `theloom-store` · `theloom-symbolic` · `theloom-synthesis` ·
`theloom-verification` · `theloom-viz`

## Recipes

### Where is `X` defined?

```bash
loom explore '{"name": "assemble_bundle (bundle)", "graph": "codebase-the-loom"}'
```

One call returns the definition (file + line range + signature + docstring), callers,
callees, imports, importedBy, containment, inheritance, and the written layer attached
to it. Budgeted, so it self-truncates and says so.
→ *typical shape:* definition + 12 imports + 5 importedBy + 4 contained symbols +
10 written notes.

### Who calls `X`?

```bash
loom find-callers '{"name": "hydrate_graph (hydrate)", "graph": "codebase-the-loom"}'
```

Ranked, and each row is anchored at the call site — file and line of the call, not just
the calling file. → *typical shape:* 6 callers, rolled up by file.

### What does `X` call?

```bash
loom find-callees '{"name": "assemble_bundle (bundle)", "graph": "codebase-the-loom"}'
```

→ *typical shape:* 8–20 callees, with unresolved calls omitted rather than guessed
(an ambiguous callee produces no edge — `theloom/extraction/resolution.py:431-451`).

### What does module `Y` do?

```bash
loom list-entities '{"query": "theloom/store", "entityType": "concept", "compact": true, "graph": "codebase-the-loom"}'
```

Returns the group's purpose record: what it is for, its key files, its public surface.
→ *typical shape:* 1 purpose record with 4–5 observations.

Then follow up on the files it names:

```bash
loom explore '{"name": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'
```

### What breaks if I change `Z`?

```bash
loom blast-radius '{"name": "MultiGraph.get_store (multigraph)", "graph": "codebase-the-loom"}'
```

Reverse dependency reach, grouped by module, with hub suppression disclosed rather than
silent. → *typical shape:* 40–120 affected symbols across 8–15 modules, plus a
`suppressedHubs` note if a high-degree node was withheld.

### What are the risks here?

```bash
loom list-entities '{"entityType": "tension", "compact": true, "limit": 30, "graph": "codebase-the-loom"}'
```

Narrow to one area with a query:

```bash
loom list-entities '{"entityType": "tension", "query": "store", "compact": true, "graph": "codebase-the-loom"}'
```

Each strain carries `pole_a`, `pole_b`, an `anchor` (file:line, both sides), and
`implications`. → *typical shape:* 8 strains for a package-sized group; 241 exist in
total.

### What must stay true about this area?

```bash
loom list-entities '{"entityType": "claim", "query": "bi-temporal", "compact": true, "graph": "codebase-the-loom"}'
```

Invariants, each with a `statement`, an `anchor` and a `consequence_if_broken`.
→ *typical shape:* 9–16 invariants per package.

### How is this area built?

```bash
loom list-entities '{"entityType": "pattern", "query": "theloom-cli", "compact": true, "graph": "codebase-the-loom"}'
```

→ *typical shape:* 6–10 recurring construction patterns per group, each with a
`description`, `instances` (file:line list), `mechanism` and `variation`.

### I only have a concept, not a symbol name

```bash
loom hybrid-search '{"query": "how are relation updates persisted", "graph": "codebase-the-loom"}'
```

Vector plus keyword retrieval over everything — code symbols and written notes alike.
→ *typical shape:* 10 ranked hits with scores and anchors.

### Everything about one thing, in one call

```bash
loom entity-deep-dive '{"name": "file:theloom/cli/registry.py", "compact": true, "graph": "codebase-the-loom"}'
```

→ *typical shape:* a multi-section envelope — record, neighbors, relations, provenance,
centrality — each section independently fault-isolated.

### How do two things connect?

```bash
loom find-shortest-path '{"fromName": "file:theloom/cli/app.py", "toName": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'
loom explain-path '{"name": "file:theloom/viz/bundle.py", "graph": "codebase-the-loom"}'
```

### Structure of the whole thing

```bash
loom graph-stats '{"graph": "codebase-the-loom"}'
loom analyze-centrality '{"algorithm": "degree", "limit": 15, "graph": "codebase-the-loom"}'
loom detect-cycles '{"includePaths": true, "graph": "codebase-the-loom"}'
```

---

## Make agents use this graph

Optional, and the repository owner's choice. A Claude Code hook can nudge agents toward
the graph whenever they reach for `Grep` or `Glob`. It emits `additionalContext` only —
it never blocks a tool call, never returns a permission decision, and ends in `|| true`
so it cannot fail the call. Add to `.claude/settings.json`:

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

The guard on `docs/architecture/map-manifest.json` means the nudge disappears
automatically in a checkout that has no map.

---

**Query the graph before grepping the repo.** The graph already has the answer, anchored
to a file and a line — including the parts of the answer that are not in the source text
at all: why a module exists, what must stay true about it, and where it strains.
