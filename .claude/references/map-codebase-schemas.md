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
