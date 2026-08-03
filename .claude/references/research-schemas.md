# Research Pipeline — Structured Output Schemas

Canonical handoff contracts for the `/deep-research` and `/hyper-research` pipelines. Each `research-*` agent emits one of these objects as its **final message**; orchestrators treat the object as the source of truth for control flow (the on-disk `findings/*.json` + `research-state.json` remain as the human-readable audit trail, not the control surface).

**Why this exists:** the handoffs were previously freeform JSON files read with defensive `|| null` parsing — a malformed agent output silently degraded a downstream step. With these contracts, a missing/invalid required field is a *typed error at the boundary* (retryable), not a silent skip three steps later. In Stage 3 (Workflow tool) these become the literal JSON Schemas passed to `agent(..., {schema})`.

Conventions: all objects are `{"type":"object","additionalProperties":false}` unless noted. "Empty is representable" — e.g. a red-team that finds nothing returns `survivedClaimIds:[]`, not a missing field.

---

## QuestionClassification — emitted in deep-research Phase 0

Tailors iteration limits + feature flags to the question type.

```json
{
  "type": "object", "required": ["type", "maxIterations", "enableRedTeam", "enableCheckpoint", "rationale"],
  "properties": {
    "type": { "enum": ["A", "B", "C", "D"] },
    "maxIterations": { "type": "integer", "minimum": 2, "maximum": 7 },
    "enableRedTeam": { "type": "boolean" },
    "enableCheckpoint": { "type": "boolean" },
    "rationale": { "type": "string" }
  }
}
```

## ResearchContract — emitted by `research-orientation`

```json
{
  "type": "object", "required": ["coreQuestion", "scope", "successCriteria", "initialQuestions", "seededEntityIds"],
  "properties": {
    "coreQuestion": { "type": "string" },
    "intention": { "type": "string" },
    "scope": { "type": "object", "properties": {
      "included": { "type": "array", "items": { "type": "string" } },
      "excluded": { "type": "array", "items": { "type": "string" } } } },
    "successCriteria": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
    "initialQuestions": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
    "seededEntityIds": { "type": "array", "items": { "type": "string" } }
  }
}
```

## Findings — emitted by `research-agent`

The `verification` block is load-bearing: `entitiesVerified <= entitiesAttempted` and "verification present" become schema guarantees, collapsing the orchestrator's `no_entities_verified` / `missing_verification_data` branches into one assertion.

```json
{
  "type": "object", "required": ["iteration", "newEntityIds", "verification"],
  "properties": {
    "iteration": { "type": "integer", "minimum": 0 },
    "newEntityIds": { "type": "array", "items": { "type": "string" } },
    "threads": { "type": "array", "items": { "type": "object",
      "properties": { "question": { "type": "string" }, "entityIds": { "type": "array", "items": { "type": "string" } } } } },
    "deduplication": { "type": "object", "properties": {
      "existingEntitiesChecked": { "type": "integer" }, "duplicatesAvoided": { "type": "integer" },
      "relationsToExisting": { "type": "integer" } } },
    "verification": { "type": "object", "required": ["entitiesAttempted", "entitiesVerified", "failedCreations"],
      "properties": {
        "entitiesAttempted": { "type": "integer", "minimum": 0 },
        "entitiesVerified": { "type": "integer", "minimum": 0 },
        "failedCreations": { "type": "array", "items": { "type": "object",
          "properties": { "name": { "type": "string" }, "entityType": { "type": "string" }, "error": { "type": "string" } } } } } }
  }
}
```

## Synthesis — emitted by `research-synthesis`

```json
{
  "type": "object", "required": ["patternIds", "insightIds", "tensionIds", "verification"],
  "properties": {
    "patternIds": { "type": "array", "items": { "type": "string" } },
    "insightIds": { "type": "array", "items": { "type": "string" } },
    "tensionIds": { "type": "array", "items": { "type": "string" } },
    "convergenceIds": { "type": "array", "items": { "type": "string" } },
    "hypothesisUpdates": { "type": "array", "items": { "type": "object" } },
    "verification": { "type": "object", "required": ["entitiesAttempted", "entitiesVerified", "failedCreations"],
      "properties": {
        "entitiesAttempted": { "type": "integer", "minimum": 0 },
        "entitiesVerified": { "type": "integer", "minimum": 0 },
        "failedCreations": { "type": "array", "items": { "type": "object" } } } }
  }
}
```

## Consolidation — emitted by `research-consolidation`

`entityCount` is typed because it gates the expedition step (`iter >= 1 && entityCount >= 20`).

```json
{
  "type": "object", "required": ["entityCount", "relationCount"],
  "properties": {
    "entityCount": { "type": "integer", "minimum": 0 },
    "relationCount": { "type": "integer", "minimum": 0 },
    "mergedDuplicates": { "type": "integer", "minimum": 0 },
    "orphansConnected": { "type": "integer", "minimum": 0 },
    "creditPropagation": { "type": "object", "properties": {
      "propagationRuns": { "type": "integer" }, "totalDownstreamAffected": { "type": "integer" }, "maxDepthReached": { "type": "integer" } } },
    "recommendations": { "type": "array", "items": { "type": "string" } }
  }
}
```

## Expedition — emitted by `research-expedition`

```json
{
  "type": "object", "required": ["emergentTheory"],
  "properties": {
    "emergentTheory": { "type": "object", "required": ["found"],
      "properties": { "found": { "type": "boolean" }, "plainLanguageSummary": { "type": "string" } } },
    "discoveries": { "type": "array", "items": { "type": "string" } }
  }
}
```

## RedTeam — emitted by `research-red-team`

```json
{
  "type": "object", "required": ["counterEvidenceIds", "survivedClaimIds", "weakenedClaimIds", "verification"],
  "properties": {
    "counterEvidenceIds": { "type": "array", "items": { "type": "string" } },
    "survivedClaimIds": { "type": "array", "items": { "type": "string" } },
    "weakenedClaimIds": { "type": "array", "items": { "type": "string" } },
    "verification": { "type": "object", "required": ["entitiesAttempted", "entitiesVerified", "failedCreations"],
      "properties": {
        "entitiesAttempted": { "type": "integer", "minimum": 0 },
        "entitiesVerified": { "type": "integer", "minimum": 0 },
        "failedCreations": { "type": "array", "items": { "type": "object" } } } }
  }
}
```

## QualityVerdict — emitted by `research-quality` (THE loop controller)

Must be airtight: `continueResearch` drives `while(...)`, so it can never be `undefined`. `stoppingReason` aligns with the agent's own termination logic (ANY 2 of 4 multi-criteria + thresholds + max-iterations).

```json
{
  "type": "object", "required": ["overallScore", "continueResearch", "stoppingReason"],
  "properties": {
    "overallScore": { "type": "number", "minimum": 0, "maximum": 10 },
    "continueResearch": { "type": "boolean" },
    "stoppingReason": { "enum": ["continue", "quality_threshold", "multi_criteria", "max_iterations", "saturation", "error"] },
    "lakatos": { "type": "object" },
    "flexibility": { "type": "object" },
    "feedback": { "type": "string" },
    "nextIterationQueries": { "type": "array", "items": { "type": "string" } }
  }
}
```

## Documentation — emitted by `research-documentation`

```json
{
  "type": "object", "required": ["artifactsCreated"],
  "properties": {
    "artifactsCreated": { "type": "array", "items": { "type": "object",
      "required": ["type", "path"],
      "properties": { "type": { "enum": ["zettelkasten", "research", "journal"] }, "path": { "type": "string" } } } }
  }
}
```

---

# Hyper-research handoffs

## Questions — emitted in hyper-research Phase 3

The stable `id` is the Stage-3 resume cache key (`label: dr-${id}`) and the report section anchor — it must be a deterministic slug, never an array index.

```json
{
  "type": "array", "minItems": 0, "maxItems": 5,
  "items": { "type": "object", "required": ["id", "text", "rationale", "priority"],
    "properties": {
      "id": { "type": "string", "description": "stable slug, e.g. q1-feedback-latency" },
      "text": { "type": "string" },
      "rationale": { "type": "string" },
      "priority": { "type": "integer", "minimum": 1, "maximum": 5 } } }
}
```

## DeepResearchResult — emitted by each Phase 4 deep-research run

```json
{
  "type": "object", "required": ["questionId", "sessionId", "status", "qualityScore", "keyFindings"],
  "properties": {
    "questionId": { "type": "string" },
    "sessionId": { "type": "string", "description": "research/sessions/{id}" },
    "status": { "enum": ["complete", "failed", "error"] },
    "qualityScore": { "type": "number", "minimum": 0, "maximum": 10 },
    "iterations": { "type": "integer" },
    "keyFindings": { "type": "array", "items": { "type": "string" }, "maxItems": 12 },
    "subgraphTag": { "type": "string", "description": "provenance tag stamped on this run's entities, e.g. hyper-{session}-{questionId}" }
  }
}
```

## FinalSynthesis — emitted in hyper-research Phase 6

```json
{
  "type": "object", "required": ["crossCuttingThemes", "whatRemainsOpen"],
  "properties": {
    "crossCuttingThemes": { "type": "array", "items": { "type": "object", "required": ["theme", "supportingQuestionIds"],
      "properties": { "theme": { "type": "string" },
        "supportingQuestionIds": { "type": "array", "items": { "type": "string" } },
        "evidence": { "type": "string" } } } },
    "whatRemainsOpen": { "type": "array", "items": { "type": "string" } },
    "suggestedNextResearch": { "type": "array", "items": { "type": "string" } }
  }
}
```

---

## Verification-block invariant (shared)

Any agent that creates Loom entities (`research-agent`, `research-synthesis`, `research-red-team`) MUST return a `verification` block. The orchestrator asserts `0 <= entitiesVerified <= entitiesAttempted`. A run with `entitiesAttempted > 0` and `entitiesVerified == 0` is a hard error (Loom write path failing — check that the `loom` CLI is reachable, FalkorDB is running, and the `graph` field matches `GRAPH_NAME`), not a continue. This is the single highest-value reliability guarantee in the pipeline.
