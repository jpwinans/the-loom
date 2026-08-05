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
  "type": "object", "required": ["mapPath", "vizPath", "queryingDoc", "stats", "keyFindings"],
  "properties": {
    "mapPath": { "type": "string" },
    "vizPath": { "type": "string", "description": "empty string when viz rendering failed after retries" },
    "queryingDoc": { "type": "string", "description": "path to QUERYING.md, the agent-facing query cheat sheet" },
    "stats": { "type": "object", "required": ["entities", "relations", "cycles", "hubs"],
      "properties": {
        "entities": { "type": "integer" }, "relations": { "type": "integer" },
        "cycles": { "type": "integer" }, "hubs": { "type": "integer" } } },
    "keyFindings": { "type": "array", "items": { "type": "string" }, "maxItems": 10 }
  }
}
```

---

## Embed phase and `--no-enrich`

The workflow runs a single `embed-entities` pass after Enrich (or immediately after
extraction when `--no-enrich` skips Enrich) instead of a per-group embed call inside
each `codebase-enricher` invocation — one call in place of up to 29 redundant ones per
run. It has no dedicated schema: the workflow issues it as a plain agent step and
discards the reply. `--no-enrich` runs Setup, Embed, and Cartograph only, and the
`Setup.mode` string the Cartograph agent receives is annotated inline
(`"... (structural-only: --no-enrich skipped the Enrich phase, ...)"`) rather than
carried as a separate schema field, so `enrichResults` stays legitimately empty without
tripping the verification-block invariant below.

## Verification-block invariant (shared)

The enricher creates Loom entities and MUST return a `verification` block with
`0 <= entitiesVerified <= entitiesAttempted`. A group with `entitiesAttempted > 0` and
`entitiesVerified == 0` means the Loom write path is failing silently (check the `loom`
CLI, FalkorDB, and the `graph` field) — the workflow halts rather than shipping a map
whose semantic layer silently never landed.

"Verified" means two things, not one: the entity exists (`read-entity` returns) AND its
`module_group` observation equals the enriching agent's own `GROUP.id` exactly. A stamp
mismatch is not a pass — the entity landed in the wrong group's coverage and must be
corrected (superseded and recreated), same as any other wrong-content correction.
`entitiesVerified` counts only entities that cleared both checks.

Creation is idempotent per group: before each `create-entity`, the enricher checks for
an already-live entity of the same name under its own `GROUP.id` and reuses it instead
of creating a duplicate. Retrying a failed write is scoped to that one `create-entity` /
`create-relation` call — never to the whole batch — so a transient failure or a slow
verification read on entity N cannot cause entities 1..N-1 to be recreated.

## Repo hygiene

Nothing in this pipeline writes to the repo tree except: (a) the `loom` CLI's own graph
writes, and (b) the cartographer's four named `OUTPUT_DIR` deliverables
(`ARCHITECTURE-MAP.md`, `codebase-map.html`, `QUERYING.md`, `map-manifest.json`). Any
scratch or bookkeeping file an agent's tooling needs along the way — batch results,
retry state, dedupe logs — goes under `/tmp`, never `PROJECT_PATH`, never the repo root.
Every agent prompt in the workflow carries this constraint verbatim (`SCRATCH_GUARD` in
`.claude/workflows/map-codebase.js`); both agent definitions restate it as a numbered
constraint.
