# Data Model Reference

## Entity Types (16)

| Type | Purpose | Key Observations |
|------|---------|-----------------|
| `concept` | Ideas, abstractions | `definition:`, `domain:` |
| `claim` | Evaluable assertions | `statement:`, `confidence:`, `source:` |
| `source` | Information origins | `author:`, `credibility:`, `type:` |
| `evidence` | Support for claims | `finding:`, `strength:` |
| `pattern` | Recurring structures | `domains:`, `instances:`, `mechanism:` |
| `insight` | Synthesized understanding | `content:`, `derived_from:` |
| `tension` | Productive contradictions | `pole_a:`, `pole_b:`, `status:` |
| `convergence` | Multi-source agreement | `sources:`, `strength:`, `independent:` |
| `system` | Bounded interacting elements | `purpose:`, `components:`, `boundary:` |
| `variable` | Measurable quantities | `range:`, `units:`, `influenced_by:` |
| `loop` | Feedback cycles | `type:` (reinforcing/balancing), `path:`, `behavior:` |
| `leverage_point` | Intervention points | `level:` (1-12), `intervention:`, `rationale:` |
| `event` | Time-anchored occurrences | `date:`, `significance:`, `actors:` |
| `procedure` | Step sequences | `steps:`, `inputs:`, `outputs:` |
| `question` | Open inquiries | `question_text:`, `status:`, `blocking:` |
| `hypothesis` | Testable predictions | `prediction:`, `test:`, `status:` |

### Type Selection Flowchart

```
Is it a specific occurrence in time? → EVENT
Is it instructions for doing something? → PROCEDURE
Is it asking something? → QUESTION
Is it about a dynamic system?
  ├─ Bounded interacting whole? → SYSTEM
  ├─ Something that changes? → VARIABLE
  ├─ Circular causation? → LOOP
  └─ Where to intervene? → LEVERAGE_POINT
Is it derived from combining knowledge?
  ├─ Recurring across contexts? → PATTERN
  ├─ New understanding from synthesis? → INSIGHT
  ├─ Conflict between valid ideas? → TENSION
  └─ Multiple sources agreeing? → CONVERGENCE
Is it a fundamental knowledge element?
  ├─ Makes a specific assertion? → CLAIM
  ├─ Where information came from? → SOURCE
  ├─ Data supporting/refuting a claim? → EVIDENCE
  └─ An abstract idea or category? → CONCEPT
```

### Confusable Pairs

- **claim vs insight**: Did someone say this (claim), or did you realize it by connecting things (insight)?
- **pattern vs convergence**: Something that repeats (pattern), or different sources agreeing (convergence)?
- **system vs variable**: The whole machine (system), or one of its dials (variable)?
- **event vs procedure**: Happened once at a specific time (event), or how to do something repeatedly (procedure)?

---

## Relation Types (14)

### Structural (polarity: null)
| Type | Meaning |
|------|---------|
| `related_to` | General connection |
| `instance_of` | Example of pattern |
| `part_of` | Component of whole |
| `sources` | Originated from |

### Epistemic (polarity: null)
| Type | Meaning |
|------|---------|
| `supports` | Evidence for claim |
| `contradicts` | Conflicts with |
| `questions` | Raises doubt about |
| `supersedes` | Replaces/obsoletes |

### Causal (polarity: + or -)
| Type | Meaning |
|------|---------|
| `causes` | A causes B |
| `enables` | A makes B possible |
| `requires` | B needs A |
| `inhibits` | A suppresses B |
| `amplifies` | A strengthens B |
| `dampens` | A weakens B |

### Polarity Semantics
- **+** (positive): A↑ → B↑, A↓ → B↓ (same direction)
- **-** (negative): A↑ → B↓, A↓ → B↑ (opposite direction)
- **null**: Non-causal relations

### Strength
- `strong` — Direct dependency, import, inheritance
- `moderate` — Pattern observation, inferred coupling
- `weak` — Semantic similarity, loose connection

---

## Epistemic Metadata

### Confidence
```json
{
  "score": 0.85,
  "basis": "peer_reviewed",
  "lastEvaluated": "2026-03-05T10:00:00Z"
}
```

**Score → Label:**
| Range | Label |
|-------|-------|
| 0.9-1.0 | very_high |
| 0.7-0.9 | high |
| 0.5-0.7 | moderate |
| 0.3-0.5 | low |
| 0.0-0.3 | speculative |

**Basis types:** `direct_observation`, `peer_reviewed`, `multiple_sources`, `single_source`, `inference`, `speculation`, `llm_extraction`, `calculated`

### Entity Status
`active` (default) | `superseded` | `deprecated` | `retracted` | `investigating`

**Status reasons:** `outdated_knowledge`, `error_correction`, `source_retracted`, `duplicate`, `scope_change`, `verification_failed`, `under_review`

### Provenance
```json
{
  "sourceType": "document",
  "sourceId": "entity-uuid-or-null",
  "externalRef": "https://example.com/paper",
  "extractionDate": "2026-03-05T10:00:00Z",
  "extractor": "claude",
  "extractionMethod": "llm_prompted"
}
```

**Source types:** `document`, `conversation`, `observation`, `inference`, `synthesis`, `external`
**Extraction methods:** `manual`, `llm_prompted`, `automated`, `postmortem_gap_resolution`, `postmortem_pattern_reification`, `postmortem_credit_propagation`

### Version Tracking
- `version`: Starts at 1, auto-incremented on update
- `previousVersionId`: Points to prior version
- `changeType`: `created` | `content_updated` | `confidence_updated` | `status_changed` | `merged`
- `changeReason`: Human-readable explanation

---

## Observation Format

Use `key: value` format for structured, searchable observations. One fact per observation.

**Examples:**
```
"definition: A holistic approach to analysis"
"domain: management, ecology"
"purpose: Enable reasoning through persistent structures"
"level: 6"
"risk: No input validation on entity names"
"severity: high"
```

---

## Graph Storage

### File Format
```json
{
  "nodes": [ { "id": "uuid", "name": "...", "entityType": "concept", "observations": [...], ... } ],
  "edges": [ { "id": "uuid", "from": "uuid1", "to": "uuid2", "relationType": "causes", ... } ],
  "metadata": {}
}
```

### Multi-Graph
- Every graph lives in FalkorDB; there is no per-graph file on disk
- Cross-graph relations are held in the bridge registry, also in FalkorDB
- Default graph configured in `~/.loom/config.json`
- Specify non-default graph with `graph` parameter on any tool

### Constraints
- No self-loops allowed
- Entity names are not unique (duplicates possible)
- UUIDs are auto-generated
- Timestamps are ISO 8601 UTC
- `statusFilter` defaults to `['active']` — pass explicitly to include non-active entities

---

## Document Store

### Categories
Documents are partitioned by category for filtered search:
Categories are free-form strings you choose — they simply partition documents so
searches can be filtered. For example:
- `docs` — project documentation
- `specs` — design and specification material
- `library` — external reference materials
- Custom categories as needed

### Supported Formats
PDF, DOCX, Markdown, HTML, TXT, JSON

### Chunking
- Default target: ~4000 chars per chunk
- 2-sentence overlap between chunks
- Strategies: `structural` (default), `paragraph`, `fixed`
- SHA-256 content hashing for change detection on re-ingestion
