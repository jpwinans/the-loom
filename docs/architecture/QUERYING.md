# Querying the codebase graph

**If you are an agent about to grep this repository: query the graph first.** Every
symbol, call edge, import, invariant, risk and convention in this codebase is already
in a FalkorDB graph, anchored to a file and a line. One `loom` call usually answers what
would otherwise take several `Grep`/`Read` round trips.

**Graph name:** `codebase-the-loom`

**Prerequisite:** the store must be running — `docker compose up -d falkordb`.

**Invocation:** `loom <command> '<json>'`. If `loom` is not on `PATH`, prefix every call
with `uv run --directory <path-to-your-loom-checkout>` (inside this repo,
`uv run loom ...` is enough).

## Record naming

Two forms, both resolvable by unambiguous substring:

- **Files** — `file:<repo-relative-path>`, e.g. `file:theloom/store/falkor.py`
- **Symbols** — `<symbol> (<module stem>)`, e.g. `create_entity (falkor)`,
  `run_handler (registry)`, `MultiGraph.get_store (multigraph)`

An ambiguous substring returns the candidate list rather than guessing, so a partial
name is a safe first attempt.

## The recipes

One command per question. Sizes are the shape of a realistic answer, not the full output.

| Question | Command |
|---|---|
| Where is `X` defined? | `loom explore '{"name": "X", "graph": "codebase-the-loom"}'` → definition + callers/callees/imports/containment + the written notes, in one call (typical: ~1 definition, 6 callers, 12 callees, budgeted with an explicit truncation block) |
| Who calls `X`? | `loom find-callers '{"name": "X", "graph": "codebase-the-loom"}'` → ranked, each anchored at its call site (typical: 4–20 rows, plus a per-file rollup when truncated) |
| What does `X` call? | `loom find-callees '{"name": "X", "graph": "codebase-the-loom"}'` → same shape, outbound |
| What does module `Y` do? | `loom list-entities '{"query": "Y purpose", "entityType": "concept", "compact": true, "graph": "codebase-the-loom"}'` for its purpose (1 row, a paragraph), then `loom explore` on the key files it names |
| What breaks if I change `Z`? | `loom blast-radius '{"name": "Z", "graph": "codebase-the-loom"}'` → reverse dependency reach grouped by module (typical: 3–8 modules, 20–60 symbols, hop-bounded) |
| What are the risks here? | `loom list-entities '{"entityType": "tension", "query": "<module or topic>", "compact": true, "graph": "codebase-the-loom"}'` → each risk as two poles + an anchor + implications (typical: 4–7 per module group) |
| What must stay true? | `loom list-entities '{"entityType": "claim", "query": "<module or topic>", "compact": true, "graph": "codebase-the-loom"}'` → invariants with `anchor:` file:line and the consequence of breaking them (typical: 6–10 per module group) |
| What is the convention here? | `loom list-entities '{"entityType": "pattern", "query": "<module or topic>", "compact": true, "graph": "codebase-the-loom"}'` → named conventions with instances (typical: 4–8 per module group) |
| Find it by meaning, not by name | `loom hybrid-search '{"query": "how does a write become atomic", "graph": "codebase-the-loom"}'` → ~10 ranked rows across code and notes |
| Everything about one record | `loom entity-deep-dive '{"name": "file:theloom/store/falkor.py", "graph": "codebase-the-loom"}'` → definition, neighbourhood, notes, one envelope |
| How do these two connect? | `loom find-shortest-path '{"from": "A", "to": "B", "graph": "codebase-the-loom"}'`, then `loom explain-path` on the result |
| What is the shape of the whole thing? | `loom graph-stats '{"graph": "codebase-the-loom"}'` · `loom analyze-centrality '{"algorithm": "degree", "limit": 15, "graph": "codebase-the-loom"}'` · `loom detect-cycles '{"includePaths": true, "graph": "codebase-the-loom"}'` |

**Keeping responses small:** `list-entities`, `read-entity`, `get-neighbors`,
`get-relations` and `entity-deep-dive` all accept `"compact": true` and `"limit": N`.
Use both by default; the consumption commands (`explore`, `find-callers`,
`find-callees`, `blast-radius`) are budgeted already and always report what they cut.

## Module-group ids

The written layer is tagged `module_group: <id>`. Filter by these to scope a question to
one part of the codebase — for example
`loom list-entities '{"entityType": "tension", "query": "theloom-store-1", "compact": true, "graph": "codebase-the-loom"}'`.

**Python package**

`theloom` · `theloom-algebra` · `theloom-analysis` · `theloom-cli` ·
`theloom-composites-1` · `theloom-composites-2` · `theloom-documents` ·
`theloom-exploration` · `theloom-extraction` · `theloom-graph` ·
`theloom-operations-1` · `theloom-operations-2` · `theloom-operations-3` ·
`theloom-operations-4` · `theloom-reification` · `theloom-semantic` ·
`theloom-store-1` · `theloom-store-2` · `theloom-symbolic` · `theloom-synthesis-1` ·
`theloom-synthesis-2` · `theloom-verification` · `theloom-viz`

**Frontend**

`tapestry-1` · `tapestry-2` · `tapestry-e2e` · `tapestry-src` · `tapestry-src-design` ·
`tapestry-src-lib` · `tapestry-src-state` · `tapestry-src-views-chronicle` ·
`tapestry-src-views-explorer` · `tapestry-src-views-overview` ·
`tapestry-src-views-semantic` · `tapestry-src-views-systems`

**Tests**

`tests-1` … `tests-9` · `tests-fixtures-multi` · `tests-fixtures-repo` ·
`tests-fixtures-repo-src`

**Contract and documentation**

`root-1` (CLAUDE.md) · `root-2` (COMMANDS.md) · `root-3` (contract layer) ·
`root-4` (uv.lock) · `docs` · `docs-architecture` · `examples`

The human-readable labels these ids correspond to are listed in `map-manifest.json`.

## Worked example

Before changing the store facade:

```bash
loom explore '{"name": "MultiGraph.get_store (multigraph)", "graph": "codebase-the-loom"}'
loom blast-radius '{"name": "MultiGraph.get_store (multigraph)", "graph": "codebase-the-loom"}'
loom list-entities '{"entityType": "claim", "query": "theloom-store-1", "compact": true, "graph": "codebase-the-loom"}'
loom list-entities '{"entityType": "tension", "query": "theloom-store-1", "compact": true, "graph": "codebase-the-loom"}'
```

That is four calls for: what it is, what depends on it, what must stay true about it,
and what is already known to be fragile around it.

## Make agents use this graph

Optional, and the repository owner's choice. Adding this hook to a mapped repo's
`.claude/settings.json` nudges Claude Code toward the graph whenever it reaches for
`Grep` or `Glob`. It only ever injects context — it never blocks a tool call, never
returns a permission decision, and ends in `|| true` so it cannot fail the tool call.
This is the canonical copy, reproduced verbatim from the-loom's own
`.claude/settings.json`:

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

The guard on `map-manifest.json` means the hook is inert in a repository that has not
been mapped.

## Freshness

`map-manifest.json` records the commit the graph describes. If it trails `HEAD`
materially, re-run `/map-codebase <repo-root>` — the run is incremental, and only the
groups whose files changed are re-described.

---

**Query the graph before grepping the repo.** The graph already has the answer, anchored
to a file and a line — and it also has the invariants and the known risks, which grep
cannot give you at all.
