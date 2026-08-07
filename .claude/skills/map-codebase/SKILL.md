---
description: Build an explained architecture map of a codebase — a Loom graph holding structure plus a semantic layer (module purposes, patterns, invariant claims, risks), a written ARCHITECTURE-MAP.md, and an interactive visualization, with incremental re-runs. Use for "/map-codebase PATH", "map this codebase", "architecture map", "how does this system fit together", onboarding to an unfamiliar repo, or orienting before a large review or refactor.
allowed-tools: Workflow, Bash, Read, Glob, Grep
---

# Map Codebase

Runs the **map-codebase Workflow** at `.claude/workflows/map-codebase.js` — tree-sitter
extraction (py/ts/tsx/js/go/rust) into a Loom graph, parallel semantic enrichment per
module group via the `codebase-enricher` agent, one `embed-entities` pass, then analysis
+ deliverables via `codebase-cartographer`: `ARCHITECTURE-MAP.md`, `codebase-map.html`,
`QUERYING.md`, and `map-manifest.json` under the target's `docs/architecture/`.

## Invoke

1. Parse `$ARGUMENTS`: **PATH** = first non-flag token (default `.`). Optional flags:
   `--graph NAME`, `--output DIR`, `--full`, `--no-tests`, `--no-enrich`.
2. Call the Workflow tool — this is the valid opt-in (a skill instructing a Workflow run):
   ```
   Workflow({ name: "map-codebase", args: { path: <PATH>, graph: <NAME or omit>, output: <DIR or omit>, full: <true if --full>, noTests: <true if --no-tests>, noEnrich: <true if --no-enrich> } })
   ```
   `--no-enrich` skips the Enrich phase entirely — a structural-only run (extraction +
   embed + cartograph, no semantic layer) that produces a structure-only map in
   about 2 minutes instead of running the full semantic enrichment pass.
3. It runs in the background and notifies on completion. Report: `graphName`, `mode`,
   `groupsEnriched`/`groupsTotal`, `mapPath`, `vizPath`, `queryingDoc`, and the
   `keyFindings`.

**Re-runs are incremental by default** when the target's `map-manifest.json` and graph
exist (`update-codebase` from the last mapped commit; only changed groups re-enrich);
`--full` forces fresh extraction. The graph persists for follow-up queries — `QUERYING.md`
is the cheat sheet for them (`loom explore`, `loom find-callers`/`find-callees`,
`loom blast-radius`, `loom entity-deep-dive`, `loom hybrid-search`).

> **Home:** this pipeline lives in the-loom repository — the skill, the workflow
> (`.claude/workflows/map-codebase.js`), the agents
> (`.claude/agents/codebase-enricher.md`, `.claude/agents/codebase-cartographer.md`),
> and the schemas (`.claude/references/map-codebase-schemas.md`). Run it from the repo
> root; requires FalkorDB (`docker compose up -d falkordb`). Do not keep copies in
> `~/.claude/` — duplicates shadow and drift.
