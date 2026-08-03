# /map-codebase — Architecture Map Skill

*Design spec, 2026-08-03. Status: approved design, pre-implementation.*

## Purpose

`/map-codebase` turns a codebase into an explained architecture map: a Loom graph
holding both the code's structure and a semantic layer describing what each subsystem
is for, what patterns and invariants it embodies, and where its risks live — rendered
as a written map document and an interactive visualization, and kept re-runnable as
the code evolves.

Primary job (in priority order): **architecture map** for onboarding and review.
Change-impact queries, drift watching, and refactor advice are follow-on uses of the
persistent graph, not v1 deliverables.

This is the first non-research consumer of the Loom's codebase-cognition layer:
`extract-codebase`, `update-codebase`, `detect-cycles`, and `visualize` are exercised
by no existing skill.

## Verified constraints (checked against the CLI, 2026-08-03)

- `extract-codebase` is **tree-sitter** based (not SCIP as workflows.md §4 claims —
  that line gets fixed in this PR). Languages: Python, TypeScript/TSX, JavaScript,
  Go, Rust. Unsupported files are skipped.
- Extraction emits entity types `system` (file), `procedure`, `variable`, `concept`
  (class/type) and relations `part_of`, `requires`, `related_to`, `instance_of`.
  Dry-run on the-loom: 262 files → 3,630 entities / 6,757 relations.
- `update-codebase` updates an existing graph incrementally from a `gitRef` diff.
- `visualize` writes a self-contained HTML file; accepts `scope`, `include`,
  `maxEntities`, `output`, `title`, `asOf`.
- CLI invariants (same as the research pipeline): `create-relation` requires
  `polarity`/`strength`/`evidence`; embedding is manual (`embed-entities` after
  creation batches).
- Provenance for code-derived semantic entities: `sourceType: "observation"`,
  confidence basis `direct_observation`.

## Invocation

```
/map-codebase [PATH] [--graph NAME] [--output DIR] [--full] [--include GLOB]... [--no-tests]
```

| Argument | Default | Meaning |
|---|---|---|
| `PATH` | `.` (the-loom itself) | target repo root |
| `--graph` | `codebase-{repo-dirname-slug}` | Loom graph name |
| `--output` | `{PATH}/docs/architecture/` | receives the three output files |
| `--full` | off | force fresh extraction even when a manifest exists |
| `--include` | extractor default | passthrough include globs |
| `--no-tests` | off (tests included) | passthrough `includeTests: false` |

The skill lives in the-loom repo and is invoked from the repo root (same
source-of-truth model as the research pipeline); `PATH` reaches any target repo.

**Re-run model:** when `{output}/map-manifest.json` and the graph both exist, the run
is incremental — `update-codebase` with `gitRef` = the manifest's mapped commit, then
re-enrichment of only the module groups containing changed files, then map + viz
regeneration. `--full` overrides.

## Architecture

```
.claude/skills/map-codebase/SKILL.md        # trigger shim: parse args → Workflow call
.claude/workflows/map-codebase.js            # Setup → Enrich (parallel) → Cartograph
.claude/agents/codebase-enricher.md          # per-module-group semantic enrichment
.claude/agents/codebase-cartographer.md      # analysis + map doc + viz + manifest
.claude/references/map-codebase-schemas.md   # handoff contracts
```

All five files follow the research pipeline's conventions verbatim: CLI-only access
preamble (kebab-case commands, `graph` field on every call, `uv run --directory
"$LOOM_DIR"` fallback), the two CLI invariants stated in the workflow's injected
instruction string, schema-validated structured-output contracts, verification
blocks on entity-creating agents, silence-default. The existing
`tests/test_claude_examples_contract.py` automatically validates every `loom`
example these files document.

### Phase 0 — Setup (one inline workflow agent)

1. Resolve `PATH` to absolute; derive slug and graph name; `git rev-parse HEAD`.
2. Fail fast if FalkorDB is unreachable (`loom graph-stats`) with the remediation
   line (`docker compose up -d falkordb`).
3. Mode detection: manifest + graph exist and `--full` absent → **incremental**
   (`loom update-codebase '{"projectPath", "graphName", "gitRef": <lastCommit>}'`);
   otherwise **full** (`loom create-graph` if needed → `loom extract-codebase`).
4. `loom embed-entities` after extraction.
5. Module grouping: list `system` entities, group by top-level directory, cap 25
   files per group (oversized dirs split by subdirectory, then alphabetical chunks), fold dirs with <3 files into their
   parent. Incremental: keep only groups containing changed files.
6. Record `git status --porcelain` non-empty as a dirty-tree warning.

Returns **Setup**: `{graphName, projectPath, mode, headCommit, moduleGroups:
[{id, label, paths, fileCount}], skippedFiles, dirtyTree}`.

### Phase 1 — Enrich (`pipeline(moduleGroups, codebase-enricher)`, parallel)

Each enricher, for its one group:

1. **Incremental first step:** supersede its group's prior semantic entities
   (`update-entity` `status: "superseded"`, `statusReason: "remapped"`) — re-maps
   never overwrite history, so `session-changelog` can answer "how did the
   architecture change since \<date\>".
2. Read the group's actual source files plus its extracted entities
   (`list-entities` / `get-neighbors` over its paths).
3. Write the semantic layer:
   - `concept` — one module-purpose entity per group ("what this subsystem is for")
   - `pattern` — design patterns genuinely observed (registry, event sourcing, …)
   - `claim` — invariants and contracts, with confidence
     (`basis: "direct_observation"`) and provenance (`sourceType: "observation"`,
     `extractor: "map-codebase"`)
   - `tension` — risks and contradictions (duplicated responsibility, dead seams)
   All semantic entities carry observations `map_layer: semantic` and
   `module_group: <id>` so the layer is selectable.
4. Link semantic → code entities with `related_to` (full payloads: `polarity: null`,
   `strength`, one-line `evidence`).
5. `loom embed-entities` after the batch; verify every creation via `read-entity`.

Returns **Enrich**: `{groupId, conceptIds, patternIds, claimIds, tensionIds,
verification{entitiesAttempted, entitiesVerified, failedCreations}}`.

### Phase 2 — Cartograph (one agent)

1. Deterministic analysis: `analyze-centrality` (degree and betweenness),
   `detect-cycles`, `find-clusters`, `detect-components`, `semantic-gaps`,
   `graph-stats`.
2. Write `ARCHITECTURE-MAP.md` (structure below).
3. Render `codebase-map.html`: `loom visualize` with `scope: {"mode": "full"}` and
   `maxEntities: 400` (halved per retry on render failure). Note: `visualize`'s
   `include` field is `{analytics, temporal, semantic}` booleans — bundle features,
   not an entity-type filter — and `scope.entityType` is singular; density is
   managed by the `maxEntities` cap.
4. Write `map-manifest.json`: `{graphName, projectPath, commit, mode, timestamp,
   groups, outputs}`.

Returns **Map**: `{mapPath, vizPath, stats{entities, relations, cycles, hubs},
keyFindings[]}`.

Workflow returns `{graphName, mapPath, vizPath, mode, groupsTotal, groupsEnriched,
keyFindings}`.

## Graph model

| Layer | Entities | Relations | Producer |
|---|---|---|---|
| Structural | `system`, `procedure`, `variable`, `concept` (class) | `part_of`, `requires`, `related_to`, `instance_of` | `extract-codebase` (deterministic) |
| Semantic | `concept` (module purpose), `pattern`, `claim`, `tension` | `related_to` → code entities | enricher agents (read source) |

Layer discrimination: observations (`map_layer: semantic`, `module_group`) +
provenance (`extractor: map-codebase`). Incremental supersession applies only to the
semantic layer; `update-codebase` owns structural churn.

## ARCHITECTURE-MAP.md structure

1. **Executive overview** — the system in a paragraph; at-a-glance stats table
   (files, symbols, relations, language mix, skipped files, dirty-tree warning).
2. **Subsystem walkthrough** — per group: purpose, key files, patterns, invariant
   claims.
3. **Load-bearing modules** — centrality table with one line each on why it's a hub.
4. **Dependency cycles** — member chains, flagged intentional vs suspect.
5. **Communities vs. directories** — where `find-clusters` disagrees with folder
   structure.
6. **Risks & tensions** — worst first.
7. **Open seams** — `semantic-gaps`: similar-but-unconnected areas.
8. **Coverage & methodology** — enriched n/m groups (unenriched named), graph name,
   mapped commit, re-run instructions, follow-up query hints (`entity-deep-dive`,
   `hybrid-search`).

## Error handling

| Failure | Behavior |
|---|---|
| FalkorDB down | Setup fails fast with remediation; nothing half-extracted |
| Enricher agent fails | `pipeline()` drops the group to null; run continues; Coverage section names unenriched groups |
| Verification invariant (`attempted > 0, verified == 0`) | Halt — Loom write path failing (same rule as research) |
| Viz too dense | Retry at lower `maxEntities`; else ship map without HTML + note |
| Dirty working tree | Map at `HEAD` with a warning; not fatal |
| Unsupported languages | Skipped, counted, reported; never fatal |

## Testing

- **Contract test (already in CI):** harvests every documented `loom '<json>'`
  invocation in the new `.claude/**/*.md` files and validates against the
  registry's input models. New UUID-valued keys, if any, get added to its
  `ID_KEYS`.
- **Local discipline:** `node --check` both new/changed workflow files.
- **Acceptance = dogfood:** `/map-codebase .` on the-loom itself — review map + viz
  by hand; then one small commit and an incremental re-run to prove the update path
  (changed group re-enriched, unchanged groups untouched, superseded entities
  queryable via `includeSuperseded`).
- Same PR fixes workflows.md §4's stale "SCIP-based TS/JS" line to
  "tree-sitter (py/ts/tsx/js/go/rust)".

## Out of scope (v1)

- Change-impact advisory flows (`simulate-change` before edits) — the graph enables
  them conversationally; no dedicated command surface yet.
- Drift watchdog / CI integration (`stale-beliefs` alerts on re-extractions).
- Cross-repo maps (multi-graph bridges between codebase graphs).
- Extending the contract-test harvester to `.js` files.
