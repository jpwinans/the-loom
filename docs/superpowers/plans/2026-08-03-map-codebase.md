# /map-codebase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/map-codebase` skill — tree-sitter extraction + parallel semantic enrichment into a Loom graph, producing `ARCHITECTURE-MAP.md`, `codebase-map.html`, and `map-manifest.json`, with incremental re-runs.

**Architecture:** A thin SKILL.md shim invokes a three-phase Workflow (`Setup → Enrich → Cartograph`). Setup extracts/updates the graph and builds module groups; Enrich fans out one `codebase-enricher` agent per group via `pipeline()`; Cartograph runs deterministic analysis and writes the deliverables. Mirrors the research pipeline's conventions exactly.

**Tech Stack:** Claude Code skill/workflow/agent markdown + one Workflow JS file; the Loom CLI (`loom <command> '<json>'`); no Python changes.

**Spec:** `docs/superpowers/specs/2026-08-03-map-codebase-design.md` — read it first.

## Global Constraints

- Loom access is CLI-only: `loom <command> '<json>'` over Bash, kebab-case commands, camelCase fields, `"graph"` on every call; `uv run --directory "$LOOM_DIR"` fallback (default `~/Dropbox/Development/the-loom`). There is no MCP server.
- `create-relation` REQUIRES `polarity` (`"+"`/`"-"` causal, else `null`), `strength` (`weak|moderate|strong|foundational`), `evidence` (string or `null`).
- Embedding is manual: `loom embed-entities '{"graph": "<G>"}'` after every creation batch.
- Semantic entities carry observations `map_layer: semantic` and `module_group: <id>`, provenance `sourceType: "observation"`, `extractor: "map-codebase"`; claims use confidence basis `direct_observation`.
- Every documented `loom '<json>'` example must be literal JSON (placeholders in `<angle brackets>` or `${VARS}`) — `tests/test_claude_examples_contract.py` harvests and validates them in CI.
- Never refer to AI/Claude or co-authors in commit messages.
- All files live in the-loom repo (source of truth); no copies in `~/.claude/`.

---

### Task 1: Handoff schemas reference

**Files:**
- Create: `.claude/references/map-codebase-schemas.md`
- Test: `tests/test_claude_examples_contract.py` (existing; harvests automatically)

**Interfaces:**
- Produces: the **Setup**, **Enrich**, and **Map** JSON Schemas that Tasks 2–4 restate/consume. Field names are load-bearing: `graphName`, `projectPath`, `mode`, `headCommit`, `moduleGroups[].{id,label,paths,fileCount}`, `skippedFiles`, `dirtyTree`; `groupId`, `conceptIds`, `patternIds`, `claimIds`, `tensionIds`, `verification.{entitiesAttempted,entitiesVerified,failedCreations}`; `mapPath`, `vizPath`, `stats.{entities,relations,cycles,hubs}`, `keyFindings`.

- [ ] **Step 1: Write the file**

````markdown
# Map-Codebase Pipeline — Structured Output Schemas

Canonical handoff contracts for the `/map-codebase` pipeline. Each agent emits one of
these objects as its **final message**; the workflow treats the object as the source of
truth for control flow. Same conventions as `research-schemas.md`: objects are
`{"type":"object"}` with `additionalProperties: true`, "empty is representable", and the
verification block carries the pipeline's single highest-value reliability guarantee.

---

## Setup — emitted by the inline setup agent

`mode` selects full extraction vs `update-codebase` incremental; `moduleGroups` drives
the Enrich fan-out; `headCommit` stamps the manifest.

```json
{
  "type": "object", "required": ["graphName", "projectPath", "mode", "headCommit", "moduleGroups"],
  "properties": {
    "graphName": { "type": "string" },
    "projectPath": { "type": "string" },
    "mode": { "enum": ["full", "incremental"] },
    "headCommit": { "type": "string" },
    "moduleGroups": { "type": "array", "items": { "type": "object",
      "required": ["id", "label", "paths", "fileCount"],
      "properties": {
        "id": { "type": "string", "description": "stable slug, e.g. theloom-store" },
        "label": { "type": "string" },
        "paths": { "type": "array", "items": { "type": "string" } },
        "fileCount": { "type": "integer", "minimum": 0 } } } },
    "skippedFiles": { "type": "integer", "minimum": 0 },
    "dirtyTree": { "type": "boolean" }
  }
}
```

## Enrich — emitted by `codebase-enricher` (one per module group)

```json
{
  "type": "object", "required": ["groupId", "conceptIds", "patternIds", "claimIds", "tensionIds", "verification"],
  "properties": {
    "groupId": { "type": "string" },
    "conceptIds": { "type": "array", "items": { "type": "string" } },
    "patternIds": { "type": "array", "items": { "type": "string" } },
    "claimIds": { "type": "array", "items": { "type": "string" } },
    "tensionIds": { "type": "array", "items": { "type": "string" } },
    "supersededCount": { "type": "integer", "minimum": 0 },
    "verification": { "type": "object", "required": ["entitiesAttempted", "entitiesVerified", "failedCreations"],
      "properties": {
        "entitiesAttempted": { "type": "integer", "minimum": 0 },
        "entitiesVerified": { "type": "integer", "minimum": 0 },
        "failedCreations": { "type": "array", "items": { "type": "object" } } } }
  }
}
```

## Map — emitted by `codebase-cartographer`

```json
{
  "type": "object", "required": ["mapPath", "vizPath", "stats", "keyFindings"],
  "properties": {
    "mapPath": { "type": "string" },
    "vizPath": { "type": "string", "description": "empty string when viz rendering failed after retries" },
    "stats": { "type": "object", "required": ["entities", "relations", "cycles", "hubs"],
      "properties": {
        "entities": { "type": "integer" }, "relations": { "type": "integer" },
        "cycles": { "type": "integer" }, "hubs": { "type": "integer" } } },
    "keyFindings": { "type": "array", "items": { "type": "string" }, "maxItems": 10 }
  }
}
```

---

## Verification-block invariant (shared)

The enricher creates Loom entities and MUST return a `verification` block with
`0 <= entitiesVerified <= entitiesAttempted`. A group with `entitiesAttempted > 0` and
`entitiesVerified == 0` means the Loom write path is failing silently (check the `loom`
CLI, FalkorDB, and the `graph` field) — the workflow halts rather than shipping a map
whose semantic layer silently never landed.
````

- [ ] **Step 2: Run the contract test (harvester must not choke on the new file)**

Run: `uv run pytest tests/test_claude_examples_contract.py -q`
Expected: PASS (file contains no `loom '<json>'` invocations; harvest count unchanged)

- [ ] **Step 3: Commit**

```bash
git add .claude/references/map-codebase-schemas.md
git commit -m "Add handoff schemas for the map-codebase pipeline"
```

---

### Task 2: codebase-enricher agent

**Files:**
- Create: `.claude/agents/codebase-enricher.md`
- Test: `tests/test_claude_examples_contract.py` (harvests the new examples automatically)

**Interfaces:**
- Consumes: prompt parameters injected by the workflow — `GRAPH_NAME`, `PROJECT_PATH`, `GROUP` (JSON `{id, label, paths, fileCount}`), `MODE` (`full`|`incremental`).
- Produces: the **Enrich** contract object from Task 1 (exact field names), and graph-side semantic entities stamped `map_layer: semantic` / `module_group: <GROUP.id>` that Task 3's cartographer queries.

- [ ] **Step 1: Write the file**

````markdown
---
name: codebase-enricher
description: Read one module group's source and write the semantic layer (module purpose, patterns, invariant claims, tensions) into the codebase graph
tools: Read, Grep, Glob, Bash
model: opus
---

# Codebase Enricher Agent

Turn one module group's raw structure into explained architecture. Tree-sitter
extraction records *what exists*; this agent reads the actual source and records *what
it means*: the module's purpose, the design patterns it embodies, the invariants it
promises, and the risks it carries. The map document is only as good as this layer —
an unenriched group is a blank page in the walkthrough.

## Input Parameters

| Parameter | Description |
|-----------|-------------|
| **GRAPH_NAME** | Name of the codebase graph |
| **PROJECT_PATH** | Absolute path to the target repo root |
| **GROUP** | This agent's module group: `{id, label, paths, fileCount}` |
| **MODE** | `full` or `incremental` |

## Loom Access (CLI-only)

> Every graph operation is `loom <command> '<json>'` over Bash — kebab-case commands,
> camelCase JSON fields, plus `"graph": "GRAPH_NAME"` on every call. If `loom` is not on
> `PATH`, prefix each call with `uv run --directory "$LOOM_DIR"` (`LOOM_DIR` defaults to
> `~/Dropbox/Development/the-loom`). There is no MCP server.
>
> Two invariants the CLI enforces: `create-relation` requires `polarity` (`"+"`/`"-"`
> for the six causal types, `null` otherwise), `strength`
> (`weak|moderate|strong|foundational`), and `evidence` (a one-line justification, or
> `null`); and embedding is a separate step — run
> `loom embed-entities '{"graph": "GRAPH_NAME"}'` after each creation batch
> (idempotent), or semantic search cannot see the new entities.

## Execution

### 1. (incremental only) Supersede this group's prior semantic entities

Re-maps never overwrite history — supersession keeps the old reading queryable so
`session-changelog` can answer "how did the architecture change since <date>". For each
of `concept`, `pattern`, `claim`, `tension`:

```bash
loom list-entities '{"entityType": "<type>", "graph": "GRAPH_NAME"}'
```

Keep entities whose observations include BOTH `map_layer: semantic` and
`module_group: <GROUP.id>`, then supersede each:

```bash
loom update-entity '{"id": "<entity_id>", "status": "superseded", "statusReason": "remapped", "graph": "GRAPH_NAME"}'
```

Report the count as `supersededCount`. In `full` mode, skip this step
(`supersededCount: 0`).

### 2. Read the source

Read every file in `GROUP.paths` (paths are relative to PROJECT_PATH). Focus on what
the map needs: module docstrings and public interfaces, import structure, error paths,
and anything that looks like a promise (assertions, validation, transactional
boundaries). Grep across the group for shared symbols when a pattern seems to span
files.

### 3. Locate the extracted code entities

Semantic entities must link to real structural entities, not names. Per file:

```bash
loom list-entities '{"query": "<file path>", "graph": "GRAPH_NAME"}'
```

Collect the `system` entity id for each file (these are the link targets).

### 4. Write the semantic layer

Only create what the source justifies — a pattern you cannot point to in a file is a
guess, not an observation. Every entity carries `map_layer: semantic` and
`module_group: <GROUP.id>` observations plus provenance
(`sourceType: "observation"`, `extractor: "map-codebase"`).

```bash
# ONE module-purpose concept for the group
loom create-entity '{"name": "<GROUP.label> purpose", "entityType": "concept", "observations": ["map_layer: semantic", "module_group: <GROUP.id>", "purpose: <what this subsystem is for, one paragraph>", "key_files: <the 3-5 files that define it>", "public_surface: <what the rest of the system calls>"], "confidence": {"score": 0.9, "basis": "direct_observation"}, "provenance": {"sourceType": "observation", "sourceId": null, "externalRef": null, "extractor": "map-codebase", "extractionMethod": "llm_prompted"}, "graph": "GRAPH_NAME"}'

# design patterns genuinely observed
loom create-entity '{"name": "<pattern name, e.g. Registry-driven command dispatch>", "entityType": "pattern", "observations": ["map_layer: semantic", "module_group: <GROUP.id>", "description: <what the pattern is here>", "instances: <file:line anchors>", "mechanism: <how it works>"], "confidence": {"score": 0.85, "basis": "direct_observation"}, "provenance": {"sourceType": "observation", "sourceId": null, "externalRef": null, "extractor": "map-codebase", "extractionMethod": "llm_prompted"}, "graph": "GRAPH_NAME"}'

# invariants and contracts the code promises
loom create-entity '{"name": "<invariant, e.g. Mutations append events; state is a projection>", "entityType": "claim", "observations": ["map_layer: semantic", "module_group: <GROUP.id>", "statement: <the invariant precisely>", "anchor: <file:line where it is enforced>", "consequence_if_broken: <what fails>"], "confidence": {"score": 0.9, "basis": "direct_observation"}, "provenance": {"sourceType": "observation", "sourceId": null, "externalRef": null, "extractor": "map-codebase", "extractionMethod": "llm_prompted"}, "graph": "GRAPH_NAME"}'

# risks and contradictions
loom create-entity '{"name": "<tension, e.g. Two modules own retry policy>", "entityType": "tension", "observations": ["map_layer: semantic", "module_group: <GROUP.id>", "pole_a: <one side>", "pole_b: <other side>", "anchor: <file:line evidence>", "implications: <why it matters>"], "confidence": {"score": 0.7, "basis": "direct_observation"}, "provenance": {"sourceType": "observation", "sourceId": null, "externalRef": null, "extractor": "map-codebase", "extractionMethod": "llm_prompted"}, "graph": "GRAPH_NAME"}'
```

### 5. Link, embed, verify

Link each semantic entity to the `system` entities it describes (capture returned ids
from step 4 — never placeholder ids):

```bash
loom create-relation '{"from": "<semantic_entity_id>", "to": "<system_entity_id>", "relationType": "related_to", "polarity": null, "strength": "moderate", "evidence": "<one line: why this file grounds the entity>", "graph": "GRAPH_NAME"}'
```

Then:

```bash
loom embed-entities '{"graph": "GRAPH_NAME"}'
loom read-entity '{"id": "<entity_id>", "graph": "GRAPH_NAME"}'   # per created entity
```

Failed create → retry once → record in `failedCreations` and continue.

## Constraints

1. **Only semantic-layer entities** (`concept`/`pattern`/`claim`/`tension` stamped
   `map_layer: semantic`). Structural entities belong to the extractor — modifying
   them corrupts `update-codebase`'s incremental diffs.
2. **Supersede only entities stamped `extractor: map-codebase`** and matching this
   group — never another group's work, never structural entities.
3. **Every claim cites an anchor** (`file:line`). An uncited invariant is opinion, and
   the cartographer will print it as fact.
4. **Stay inside GROUP.paths.** Cross-group observations belong to the group that owns
   those files; note them in a tension only if the evidence is in your own files.
5. **Verify every creation; operate autonomously; never spawn agents or ask the user
   questions.**

## Structured Output Contract

My FINAL message is a single JSON object conforming to the **Enrich** schema in
`.claude/references/map-codebase-schemas.md` (repo-relative) — no prose wrapper:

```json
{
  "type": "object", "required": ["groupId", "conceptIds", "patternIds", "claimIds", "tensionIds", "verification"],
  "properties": {
    "groupId": { "type": "string" },
    "conceptIds": { "type": "array", "items": { "type": "string" } },
    "patternIds": { "type": "array", "items": { "type": "string" } },
    "claimIds": { "type": "array", "items": { "type": "string" } },
    "tensionIds": { "type": "array", "items": { "type": "string" } },
    "supersededCount": { "type": "integer", "minimum": 0 },
    "verification": { "type": "object", "required": ["entitiesAttempted", "entitiesVerified", "failedCreations"],
      "properties": {
        "entitiesAttempted": { "type": "integer", "minimum": 0 },
        "entitiesVerified": { "type": "integer", "minimum": 0 },
        "failedCreations": { "type": "array", "items": { "type": "object" } } } }
  }
}
```

"Empty is representable": a thin group returns empty arrays, never missing fields.
`entitiesAttempted > 0` with `entitiesVerified == 0` is a hard error for the workflow
(Loom write path failing), not a continue.

Silence-default: emit only the structured object; do not narrate routine steps.
````

- [ ] **Step 2: Run the contract test against the new examples**

Run: `uv run pytest tests/test_claude_examples_contract.py -q`
Expected: PASS, with a higher harvested count than before (the file's `loom` examples validate against the registry). If a failure names an unknown UUID key, add it to `ID_KEYS` in the test — but the commands used here (`list-entities`, `update-entity`, `create-entity`, `create-relation`, `embed-entities`, `read-entity`) only use keys already mapped.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/codebase-enricher.md
git commit -m "Add the codebase-enricher agent for map-codebase"
```

---

### Task 3: codebase-cartographer agent

**Files:**
- Create: `.claude/agents/codebase-cartographer.md`
- Test: `tests/test_claude_examples_contract.py`

**Interfaces:**
- Consumes: prompt parameters — `GRAPH_NAME`, `PROJECT_PATH`, `OUTPUT_DIR`, `HEAD_COMMIT`, `MODE`, `GROUPS_ENRICHED` (labels), `GROUPS_UNENRICHED` (labels), `DIRTY_TREE`, `SKIPPED_FILES`; graph-side semantic entities from Task 2.
- Produces: the **Map** contract object from Task 1; files `ARCHITECTURE-MAP.md`, `codebase-map.html`, `map-manifest.json` in `OUTPUT_DIR`.

- [ ] **Step 1: Write the file**

````markdown
---
name: codebase-cartographer
description: Analyze the codebase graph and write the architecture map document, visualization, and manifest
tools: Read, Write, Bash
model: opus
---

# Codebase Cartographer Agent

Turn the enriched graph into the deliverables: a written architecture map a newcomer
can read top-to-bottom, an interactive visualization, and the manifest that anchors
incremental re-runs. This agent is **read-only on the graph** — it describes what the
extraction and enrichment found; inventing structure here would bypass both.

## Input Parameters

| Parameter | Description |
|-----------|-------------|
| **GRAPH_NAME** | Name of the codebase graph |
| **PROJECT_PATH** | Absolute path to the target repo root |
| **OUTPUT_DIR** | Where the three output files land |
| **HEAD_COMMIT** | Commit hash the map describes |
| **MODE** | `full` or `incremental` |
| **GROUPS_ENRICHED** / **GROUPS_UNENRICHED** | Module group labels by enrichment outcome |
| **DIRTY_TREE** | Whether uncommitted changes existed at extraction |
| **SKIPPED_FILES** | Count of files tree-sitter could not parse |

## Loom Access (CLI-only)

> Every graph operation is `loom <command> '<json>'` over Bash — kebab-case commands,
> camelCase JSON fields, plus `"graph": "GRAPH_NAME"` on every call. If `loom` is not on
> `PATH`, prefix each call with `uv run --directory "$LOOM_DIR"` (`LOOM_DIR` defaults to
> `~/Dropbox/Development/the-loom`). There is no MCP server. This agent is **read-only**
> against the graph.

## Execution

### 1. Analysis (run concurrently — all read-only)

```bash
loom graph-stats '{"graph": "GRAPH_NAME"}'
loom analyze-centrality '{"metric": "degree", "limit": 15, "graph": "GRAPH_NAME"}'
loom analyze-centrality '{"metric": "betweenness", "limit": 15, "graph": "GRAPH_NAME"}'
loom detect-cycles '{"graph": "GRAPH_NAME"}'
loom find-clusters '{"graph": "GRAPH_NAME"}'
loom detect-components '{"graph": "GRAPH_NAME"}'
loom semantic-gaps '{"graph": "GRAPH_NAME"}'
```

Gather the semantic layer for the walkthrough (filter client-side to observations
containing `map_layer: semantic`):

```bash
loom list-entities '{"entityType": "concept", "graph": "GRAPH_NAME"}'
loom list-entities '{"entityType": "pattern", "graph": "GRAPH_NAME"}'
loom list-entities '{"entityType": "claim", "graph": "GRAPH_NAME"}'
loom list-entities '{"entityType": "tension", "graph": "GRAPH_NAME"}'
```

For a hub whose role is unclear from its neighbors' names, one deep dive explains it:

```bash
loom entity-deep-dive '{"entityId": "<hub_system_id>", "graph": "GRAPH_NAME"}'
```

### 2. Write `OUTPUT_DIR/ARCHITECTURE-MAP.md`

Front-matter: `repo`, `commit: HEAD_COMMIT`, `graph: GRAPH_NAME`, `generated` (ISO
date), `mode`. Then, in order:

1. **Executive overview** — the system in one paragraph; stats table (files, symbols,
   relations, language mix, skipped files; dirty-tree warning if DIRTY_TREE).
2. **Subsystem walkthrough** — one subsection per enriched group: its purpose concept,
   key files, patterns, invariant claims (with their `anchor:` citations).
3. **Load-bearing modules** — top centrality entries with one line each on *why* it is
   a hub (from its neighbors and deep-dive).
4. **Dependency cycles** — each `detect-cycles` result as a member chain, flagged
   `intentional` (e.g. re-export hubs) or `suspect`, with a one-line reason.
5. **Communities vs. directories** — where `find-clusters` groupings disagree with the
   folder structure, and what that suggests about the real seams.
6. **Risks & tensions** — tension entities, worst first, each with its anchor.
7. **Open seams** — `semantic-gaps` pairs: similar but unconnected areas.
8. **Coverage & methodology** — enriched n/m groups (name GROUPS_UNENRICHED), skipped
   file count, graph name + commit, how to re-run (`/map-codebase <path>`), and how to
   interrogate the graph afterward (`loom entity-deep-dive`, `loom hybrid-search`).

Write plain prose a newcomer can follow — no graph vocabulary ("entities", "edges") in
the walkthrough sections; those words describe the tool, not the codebase.

### 3. Render the visualization

```bash
loom visualize '{"graph": "GRAPH_NAME", "scope": {"mode": "full"}, "maxEntities": 400, "title": "<repo> architecture map", "output": "<OUTPUT_DIR>/codebase-map.html"}'
```

On failure, halve `maxEntities` and retry (400 → 200 → 100). If it still fails, ship
the map without the HTML, note it in Coverage, and return `vizPath: ""`.

### 4. Write `OUTPUT_DIR/map-manifest.json`

```json
{
  "graphName": "GRAPH_NAME",
  "projectPath": "PROJECT_PATH",
  "commit": "HEAD_COMMIT",
  "mode": "full",
  "timestamp": "<ISO>",
  "groups": ["<group ids>"],
  "outputs": {"map": "ARCHITECTURE-MAP.md", "viz": "codebase-map.html", "manifest": "map-manifest.json"}
}
```

This file is the incremental anchor — the next run reads `commit` as its `gitRef`.

## Constraints

1. **Read-only on the graph.** The cartographer reports; it never creates, updates, or
   deletes.
2. **Every claim printed in the map traces to a graph entity** — the map is a view,
   not a second source of truth.
3. **Unenriched groups appear in Coverage by name** — a silent gap reads as "nothing
   interesting here", which is a lie.
4. **Operate autonomously; never spawn agents or ask the user questions.**

## Structured Output Contract

My FINAL message is a single JSON object conforming to the **Map** schema in
`.claude/references/map-codebase-schemas.md` (repo-relative) — no prose wrapper:

```json
{
  "type": "object", "required": ["mapPath", "vizPath", "stats", "keyFindings"],
  "properties": {
    "mapPath": { "type": "string" },
    "vizPath": { "type": "string" },
    "stats": { "type": "object", "required": ["entities", "relations", "cycles", "hubs"],
      "properties": {
        "entities": { "type": "integer" }, "relations": { "type": "integer" },
        "cycles": { "type": "integer" }, "hubs": { "type": "integer" } } },
    "keyFindings": { "type": "array", "items": { "type": "string" }, "maxItems": 10 }
  }
}
```

`vizPath` is `""` when rendering failed after retries. `keyFindings` are the 3–10
things a reviewer should know first — cycles worth breaking, load-bearing hubs,
worst tensions.

Silence-default: emit only the structured object; do not narrate routine steps.
````

- [ ] **Step 2: Run the contract test**

Run: `uv run pytest tests/test_claude_examples_contract.py -q`
Expected: PASS (new examples: `graph-stats`, `analyze-centrality`, `detect-cycles`, `find-clusters`, `detect-components`, `semantic-gaps`, `list-entities`, `entity-deep-dive`, `visualize` — all keys already in the registry's models and the test's `ID_KEYS`)

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/codebase-cartographer.md
git commit -m "Add the codebase-cartographer agent for map-codebase"
```

---

### Task 4: map-codebase workflow

**Files:**
- Create: `.claude/workflows/map-codebase.js`

**Interfaces:**
- Consumes: `args` `{path, graph, output, full, include, noTests}` from the skill (Task 5); agentTypes `codebase-enricher` (Task 2) and `codebase-cartographer` (Task 3); schema shapes from Task 1.
- Produces: workflow return `{graphName, mapPath, vizPath, mode, groupsTotal, groupsEnriched, keyFindings}` that the skill reports.

- [ ] **Step 1: Write the file**

```javascript
export const meta = {
  name: 'map-codebase',
  description: 'Architecture map of a codebase: tree-sitter extraction → parallel semantic enrichment per module group → analysis + written map + visualization. Incremental re-runs via update-codebase.',
  whenToUse: 'Invoked by the /map-codebase skill to map a repo into a Loom graph with an ARCHITECTURE-MAP.md and codebase-map.html.',
  phases: [
    { title: 'Setup', detail: 'extract or incrementally update the graph; build module groups' },
    { title: 'Enrich', detail: 'parallel semantic enrichment per module group' },
    { title: 'Cartograph', detail: 'analysis + map document + visualization + manifest' },
  ],
}

const A = (typeof args === 'string' ? JSON.parse(args) : (args || {}))
const PATH = A.path || '.'
const GRAPH = A.graph || null          // null → setup derives codebase-{slug}
const OUTPUT = A.output || null        // null → {PATH}/docs/architecture/
const FULL = !!A.full
const INCLUDE = A.include || null      // array of globs or null
const NO_TESTS = !!A.noTests
// Loom access is CLI-only. LOOM is the instruction string injected into agent prompts.
const LOOM = 'run the Loom CLI over Bash as `loom <command> \'<json>\'` — kebab-case commands, camelCase JSON fields plus a `graph` field on every call. Two CLI-enforced invariants: create-relation REQUIRES polarity ("+"/"-" for causal types, null otherwise), strength (weak|moderate|strong|foundational), and evidence (string or null); and embedding is a separate step — run `loom embed-entities \'{"graph": "<GRAPH_NAME>"}\'` after each creation batch. If `loom` is not on PATH, prefix each call with `uv run --directory "$LOOM_DIR"`, where LOOM_DIR is the Loom checkout (default ~/Dropbox/Development/the-loom). There is no MCP server — do not look for the-loom MCP tools'

// ---- schemas (canonical defs in .claude/references/map-codebase-schemas.md) ----
const SETUP = { type: 'object', additionalProperties: true,
  required: ['graphName', 'projectPath', 'mode', 'headCommit', 'moduleGroups'],
  properties: { graphName: { type: 'string' }, projectPath: { type: 'string' },
    mode: { enum: ['full', 'incremental'] }, headCommit: { type: 'string' },
    moduleGroups: { type: 'array', items: { type: 'object', required: ['id', 'label', 'paths', 'fileCount'],
      properties: { id: { type: 'string' }, label: { type: 'string' },
        paths: { type: 'array', items: { type: 'string' } }, fileCount: { type: 'integer', minimum: 0 } } } },
    skippedFiles: { type: 'integer', minimum: 0 }, dirtyTree: { type: 'boolean' } } }
const VBLOCK = { type: 'object', required: ['entitiesAttempted', 'entitiesVerified', 'failedCreations'],
  properties: { entitiesAttempted: { type: 'integer', minimum: 0 }, entitiesVerified: { type: 'integer', minimum: 0 },
    failedCreations: { type: 'array', items: { type: 'object' } } } }
const ENRICH = { type: 'object', additionalProperties: true,
  required: ['groupId', 'conceptIds', 'patternIds', 'claimIds', 'tensionIds', 'verification'],
  properties: { groupId: { type: 'string' },
    conceptIds: { type: 'array', items: { type: 'string' } }, patternIds: { type: 'array', items: { type: 'string' } },
    claimIds: { type: 'array', items: { type: 'string' } }, tensionIds: { type: 'array', items: { type: 'string' } },
    supersededCount: { type: 'integer', minimum: 0 }, verification: VBLOCK } }
const MAP = { type: 'object', additionalProperties: true, required: ['mapPath', 'vizPath', 'stats', 'keyFindings'],
  properties: { mapPath: { type: 'string' }, vizPath: { type: 'string' },
    stats: { type: 'object', required: ['entities', 'relations', 'cycles', 'hubs'],
      properties: { entities: { type: 'integer' }, relations: { type: 'integer' },
        cycles: { type: 'integer' }, hubs: { type: 'integer' } } },
    keyFindings: { type: 'array', items: { type: 'string' }, maxItems: 10 } } }

// ===== Phase 0: Setup =====
phase('Setup')
const setup = await agent(`Initialize a map-codebase run. Loom access: ${LOOM}.
1. Resolve TARGET PATH "${PATH}" to an absolute path (pwd-relative if not absolute); derive slug from its dirname; GRAPH_NAME = ${GRAPH ? `"${GRAPH}"` : '"codebase-{slug}"'}; OUTPUT_DIR = ${OUTPUT ? `"${OUTPUT}"` : '"{abs path}/docs/architecture/"'} (mkdir -p it). Record \`git -C <path> rev-parse HEAD\` and whether \`git -C <path> status --porcelain\` is non-empty (dirtyTree).
2. Fail fast: \`loom graph-stats '{}'\` must succeed — if it errors on connection, throw with the remediation line "docker compose up -d falkordb".
3. Mode: if OUTPUT_DIR/map-manifest.json exists AND its graphName's graph exists AND ${FULL} is false → mode "incremental": run loom update-codebase '{"projectPath": "<abs>", "graphName": "<GRAPH_NAME>", "gitRef": "<manifest.commit>"${NO_TESTS ? ', "includeTests": false' : ''}}'. Otherwise mode "full": loom create-graph '{"name": "<GRAPH_NAME>"}' (ignore already-exists), then loom extract-codebase '{"projectPath": "<abs>", "graph": "<GRAPH_NAME>"${NO_TESTS ? ', "includeTests": false' : ''}${INCLUDE ? `, "include": ${JSON.stringify(INCLUDE)}` : ''}}'. Note skipped-file count from the output.
4. loom embed-entities '{"graph": "<GRAPH_NAME>"}'.
5. Module groups: loom list-entities '{"entityType": "system", "graph": "<GRAPH_NAME>"}' → group file paths by top-level directory; cap 25 files per group (split oversized dirs by subdirectory, then alphabetical chunks); fold dirs with <3 files into their parent. Group id = kebab slug of the dir path. In incremental mode, keep ONLY groups containing files changed in the update-codebase diff.
Return the Setup contract object.`,
  { label: 'setup', phase: 'Setup', schema: SETUP })
log(`map-codebase ${setup.mode} → graph ${setup.graphName}, ${setup.moduleGroups.length} groups @ ${setup.headCommit.slice(0, 8)}`)

// ===== Phase 1: Enrich (parallel per module group) =====
phase('Enrich')
const enrichResults = await pipeline(setup.moduleGroups, (g) =>
  agent(`Enrich one module group of the codebase graph. Loom access: ${LOOM}.
GRAPH_NAME: ${setup.graphName}
PROJECT_PATH: ${setup.projectPath}
GROUP: ${JSON.stringify(g)}
MODE: ${setup.mode}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`,
    { label: `enrich:${g.id}`, agentType: 'codebase-enricher', phase: 'Enrich', schema: ENRICH }))
const enriched = enrichResults.filter(Boolean)
for (const r of enriched) {
  if (r.verification.entitiesAttempted > 0 && r.verification.entitiesVerified === 0)
    throw new Error(`Group ${r.groupId}: ${r.verification.entitiesAttempted} entities attempted, ZERO verified — Loom write path failing silently (check the loom CLI, FalkorDB, and graph "${setup.graphName}"). Halting.`)
}
const unenriched = setup.moduleGroups.filter((g) => !enriched.some((r) => r.groupId === g.id))
log(`enriched ${enriched.length}/${setup.moduleGroups.length} groups${unenriched.length ? ` (unenriched: ${unenriched.map((g) => g.label).join(', ')})` : ''}`)

// ===== Phase 2: Cartograph =====
phase('Cartograph')
const map = await agent(`Write the architecture map deliverables. Loom access: ${LOOM}.
GRAPH_NAME: ${setup.graphName}
PROJECT_PATH: ${setup.projectPath}
OUTPUT_DIR: ${OUTPUT || `${setup.projectPath}/docs/architecture/`}
HEAD_COMMIT: ${setup.headCommit}
MODE: ${setup.mode}
GROUPS_ENRICHED: ${JSON.stringify(enriched.map((r) => r.groupId))}
GROUPS_UNENRICHED: ${JSON.stringify(unenriched.map((g) => g.label))}
DIRTY_TREE: ${!!setup.dirtyTree}
SKIPPED_FILES: ${setup.skippedFiles || 0}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`,
  { label: 'cartograph', agentType: 'codebase-cartographer', phase: 'Cartograph', schema: MAP })

return {
  graphName: setup.graphName, mapPath: map.mapPath, vizPath: map.vizPath,
  mode: setup.mode, groupsTotal: setup.moduleGroups.length, groupsEnriched: enriched.length,
  keyFindings: map.keyFindings,
}
```

- [ ] **Step 2: Parse-check**

Run:
```bash
printf 'async function _wf(){\n%s\n}\n' "$(sed 's/^export const meta/const meta/' .claude/workflows/map-codebase.js)" > /tmp/wfchk.mjs && node --check /tmp/wfchk.mjs && rm /tmp/wfchk.mjs
```
Expected: no output (parse OK)

- [ ] **Step 3: Run the contract test (the JS is not harvested, but confirm nothing regressed)**

Run: `uv run pytest tests/test_claude_examples_contract.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add .claude/workflows/map-codebase.js
git commit -m "Add the map-codebase workflow"
```

---

### Task 5: SKILL.md shim + stale doc fix

**Files:**
- Create: `.claude/skills/map-codebase/SKILL.md`
- Modify: `.claude/skills/the-loom/references/workflows.md` (§4 Layer 1 — the stale "SCIP-based TS/JS" line)

**Interfaces:**
- Consumes: `Workflow({ name: "map-codebase", args })` with the exact arg names from Task 4: `path`, `graph`, `output`, `full`, `include`, `noTests`.

- [ ] **Step 1: Write the skill**

````markdown
---
description: Build an explained architecture map of a codebase — a Loom graph holding structure plus a semantic layer (module purposes, patterns, invariant claims, risks), a written ARCHITECTURE-MAP.md, and an interactive visualization, with incremental re-runs. Use for "/map-codebase PATH", "map this codebase", "architecture map", "how does this system fit together", onboarding to an unfamiliar repo, or orienting before a large review or refactor.
allowed-tools: Workflow, Bash, Read, Glob, Grep
---

# Map Codebase

Runs the **map-codebase Workflow** at `.claude/workflows/map-codebase.js` — tree-sitter
extraction (py/ts/tsx/js/go/rust) into a Loom graph, parallel semantic enrichment per
module group via the `codebase-enricher` agent, then analysis + deliverables via
`codebase-cartographer`: `ARCHITECTURE-MAP.md`, `codebase-map.html`, and
`map-manifest.json` under the target's `docs/architecture/`.

## Invoke

1. Parse `$ARGUMENTS`: **PATH** = first non-flag token (default `.`). Optional flags:
   `--graph NAME`, `--output DIR`, `--full`, `--include GLOB` (repeatable),
   `--no-tests`.
2. Call the Workflow tool — this is the valid opt-in (a skill instructing a Workflow run):
   ```
   Workflow({ name: "map-codebase", args: { path: <PATH>, graph: <NAME or omit>, output: <DIR or omit>, full: <true if --full>, include: <[globs] or omit>, noTests: <true if --no-tests> } })
   ```
3. It runs in the background and notifies on completion. Report: `graphName`, `mode`,
   `groupsEnriched`/`groupsTotal`, `mapPath`, `vizPath`, and the `keyFindings`.

**Re-runs are incremental by default** when the target's `map-manifest.json` and graph
exist (`update-codebase` from the last mapped commit; only changed groups re-enrich);
`--full` forces fresh extraction. The graph persists for follow-up queries
(`loom entity-deep-dive`, `loom hybrid-search`).

> **Home:** this pipeline lives in the-loom repository — the skill, the workflow
> (`.claude/workflows/map-codebase.js`), the agents
> (`.claude/agents/codebase-enricher.md`, `.claude/agents/codebase-cartographer.md`),
> and the schemas (`.claude/references/map-codebase-schemas.md`). Run it from the repo
> root; requires FalkorDB (`docker compose up -d falkordb`). Do not keep copies in
> `~/.claude/` — duplicates shadow and drift.
````

- [ ] **Step 2: Fix the stale extraction line in workflows.md**

In `.claude/skills/the-loom/references/workflows.md`, §4 "Codebase Cognition Workflow"
Layer 1, replace:

```
extract-codebase → SCIP-based TS/JS extraction
  OR
ingest-directory → for Python/other languages
```

with:

```
extract-codebase → tree-sitter extraction (py, ts, tsx, js, go, rust)
  OR
ingest-directory → for other languages
```

- [ ] **Step 3: Run the contract test**

Run: `uv run pytest tests/test_claude_examples_contract.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/map-codebase/SKILL.md .claude/skills/the-loom/references/workflows.md
git commit -m "Add the /map-codebase skill and fix the stale extraction description"
```

---

### Task 6: Full validation + CLI smoke tests

**Files:**
- None created; validation only.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -q`
Expected: PASS (baseline was 670 passed, 2 skipped; the contract test's harvested count grows with the new examples)

- [ ] **Step 2: Lint/typecheck (no Python changed — confirm)**

Run: `uv run ruff check . && uv run mypy --strict theloom`
Expected: clean

- [ ] **Step 3: Extraction smoke (dry run, no writes)**

Run: `uv run loom extract-codebase '{"projectPath": ".", "dryRun": true}'`
Expected: JSON with `stats.totalFiles` ≈ 262, `extractionMethod: "tree-sitter"`

- [ ] **Step 4: Visualization smoke (against the default graph, scratch output)**

Run: `uv run loom visualize '{"scope": {"mode": "full"}, "maxEntities": 50, "output": "/tmp/map-codebase-viz-smoke.html", "title": "smoke"}' && test -s /tmp/map-codebase-viz-smoke.html && echo VIZ_OK && rm /tmp/map-codebase-viz-smoke.html`
Expected: `VIZ_OK`

- [ ] **Step 5: Commit anything outstanding, push, open PR**

```bash
git status --short   # should be clean
git push -u origin feat/map-codebase-skill
gh pr create --title "Add the /map-codebase architecture-map skill" --body "Implements docs/superpowers/specs/2026-08-03-map-codebase-design.md: tree-sitter extraction + parallel semantic enrichment + written map + visualization, with incremental re-runs. All loom examples validated by the existing contract test."
```

---

### Task 7: Dogfood acceptance run (post-merge or on-branch, user-facing)

Not a subagent task — run from the main session with the user watching costs:

- [ ] **Step 1:** From the repo root, invoke `/map-codebase .` (full mode; ~11 groups on the-loom).
- [ ] **Step 2:** Review `docs/architecture/ARCHITECTURE-MAP.md` and `codebase-map.html` by hand — walkthrough reads true, cycles/hubs plausible, coverage complete.
- [ ] **Step 3:** Make one small commit, re-run `/map-codebase .`, and verify: mode `incremental`, only the touched group re-enriched, superseded semantic entities visible via `loom list-entities '{"entityType": "claim", "includeSuperseded": true, "graph": "codebase-the-loom"}'`.
