# Loom Workflows

Common multi-step patterns for effective Loom usage.

## Contents

1. [Orientation](#1-orientation-understanding-an-existing-graph) — first contact with any graph
2. [Research](#2-research-workflow) — sources → claims → synthesis → validation
3. [System Modeling](#3-system-modeling-workflow) — causal models, loops, leverage points
4. [Codebase Cognition](#4-codebase-cognition-workflow) — code structure into graphs
5. [Document-to-Knowledge](#5-document-to-knowledge-pipeline) — ingest → extract → verify
6. [Hypothesis Generation](#6-hypothesis-generation-workflow) — gaps → proposals → dedup
7. [Analogical Reasoning](#7-analogical-reasoning-workflow) — motifs → cross-domain maps
8. [Graph Maintenance](#8-graph-maintenance-workflow) — health checks and cleanup
9. [Search Strategy](#9-search-strategy-guide) — which search tool when
10. [Multi-Graph](#10-multi-graph-workflow) — bridges and cross-graph search
11. [Epistemic Rigor](#11-epistemic-rigor-workflow) — provenance, validation, versioning

---

## 1. Orientation: Understanding an Existing Graph

Start any session by understanding what's already there.

```
graph-stats → understand scale
list-entities (by type) → see what exists
analyze-centrality (degree) → find hubs
detect-components → find isolated clusters
```

**Fast version:** Use `graph-reconnaissance` composite — does all of the above in one call.

---

## 2. Research Workflow

Building knowledge from sources.

### Phase 1: Seed Sources
```
create-entity (type: source) → register each source
ingest-document (with category) → make content searchable
```

### Phase 2: Extract Claims
```
hybrid-search (query, category) → find relevant chunks
create-entity (type: claim) → atomic assertions
create-relation (sources) → claim → sources → source
create-relation (supports) → evidence → supports → claim
```

### Phase 3: Synthesize
```
semantic-gaps → find disconnected similar entities
suggest-relations → propose missing connections
create-entity (type: insight) → synthesized understanding
create-entity (type: pattern) → recurring structures
```

### Phase 4: Validate
```
uncertain-claims → find weak claims
single-source-claims → find under-supported claims
contested-claims → find contradictions
check-consistency → verify graph integrity
```

---

## 3. System Modeling Workflow

Building causal models.

### Step 1: Define System
```
create-entity (type: system) → boundary and purpose
create-entity (type: variable) → each measurable quantity
create-relation (part_of) → variable → part_of → system
```

### Step 2: Map Causation
```
create-relation (causes/enables/inhibits) → with polarity + strength
```

**Polarity rules:**
- A increases → B increases: polarity `+`
- A increases → B decreases: polarity `-`

### Step 3: Detect Dynamics
```
detect-loops → find feedback loops
```

**Loop classification:**
- Even number of `-` edges → reinforcing (exponential)
- Odd number of `-` edges → balancing (stabilizing)

### Step 4: Find Leverage Points
```
create-entity (type: leverage_point) → with level, intervention
list-leverage-points → review by depth category
leverage-point-details → full analysis
```

### Step 5: Simulate
```
simulate-change → what-if analysis on entities
influence-map → trace causal flow from seed
```

---

## 4. Codebase Cognition Workflow

Understanding code through graph analysis.

### Layer 1: Extract Structure
```
extract-codebase → tree-sitter extraction (py, ts, tsx, js, go, rust)
  OR
ingest-directory → for other languages
extract-from-documents → LLM extraction
```

### Layer 2: Semantic Enrichment
Read code module by module. For each:
```
create-entity (type: pattern) → design patterns found
create-entity (type: claim) → invariants and contracts
create-entity (type: evidence) → risks identified
create-entity (type: tension) → contradictions found
```

### Layer 3: Analyze Architecture
```
analyze-centrality (degree) → find hub modules
detect-cycles → circular dependencies
find-clusters → module communities
semantic-gaps → implicit connections
```

---

## 5. Document-to-Knowledge Pipeline

Converting documents into structured knowledge.

```
ingest-document (file_path, category) → chunk and embed
hybrid-search (query) → verify ingestion
extract-from-documents (category, focus) → LLM extraction into entities
embed-entities → make new entities searchable
check-consistency → verify integrity
```

**Best practice:** Use `extract-preview` before `extract-from-documents` to preview what will be created.

---

## 6. Hypothesis Generation Workflow

Discovering what's missing and generating novel proposals.

### Quick: Use the Composite
```
hypothesis-engine → full pipeline in one call
  Sections: gaps → proposals → quality filter → dedup gate → ranking
```

Returns scored hypotheses with novelty, plausibility, and testability metrics.

### Manual: Step by Step
```
semantic-gaps → find similar but unconnected entities
propose-entities → generate candidates for missing concepts
```

Then review and create:
```
create-entity (type: hypothesis) → with prediction and test plan
create-relation (supports/contradicts) → link to existing evidence
```

### Dedup Configuration
The dedup gate prevents redundant proposals:
- **reject** (default): Drop proposals similar to existing entities
- **flag**: Include but mark as potential duplicates
- **merge**: Merge with existing entity observations
- Threshold default: 0.85 similarity

---

## 7. Analogical Reasoning Workflow

Finding structural parallels across domains.

### Step 1: Mine Structural Motifs
```
find-frequent-subgraphs → discover recurring patterns
  e.g., {"frequencyThreshold": 3, "maxMotifSize": 4}
```

### Step 2: Find Structural Matches
```
find-subgraph-matches → search for a specific pattern shape
  e.g., pattern with concept → causes → variable → enables → pattern
```

### Step 3: Map Across Domains
```
cross-domain-mapping → map concepts by structural role
  sourceDomain: {"entityType": "concept", "label": "Biology"}
  targetDomain: {"entityType": "concept", "label": "Economics"}
```

Maps by relational signature (degree profile, relation types, neighbor types), not surface similarity. Set `entityTypeWeight: 0` to map purely by structure.

### Step 4: Explore Concept Slippage
```
concept-slippage → find creative substitutions
  conceptId: <uuid>, temperature: 0.5
```

Temperature controls distance:
- **Low (0-0.3):** Near-synonyms, same entity type
- **Medium (0.3-0.7):** Role-based analogues, different surface form
- **High (0.7-1.0):** Creative leaps, cross-domain associations

### Combining Tools
A powerful pattern: use `find-frequent-subgraphs` to discover motifs, then `cross-domain-mapping` to see if the same motif appears in another domain, then `concept-slippage` to explore what substitutions would make the analogy work.

---

## 8. Graph Maintenance Workflow

Keeping the graph healthy.

### Regular Checks
```
stale-beliefs (daysOld: 30) → find outdated entities
unprovenanced → find entities missing provenance
check-invariants → verify constraints
embedding-status → check embedding coverage
```

### Cleanup
```
update-entity (status: superseded) → mark outdated
update-entity (status: retracted) → mark invalid
semantic-gaps → find disconnected knowledge
gap-fill-cycle → automated gap detection + resolution
```

### Quality Improvement
```
self-improve → autonomous graph refinement
postmortem-evaluate → evaluate session quality
propagate-credit → cascade confidence changes
reify-patterns → discover and materialize patterns
```

---

## 9. Search Strategy Guide

### Which search to use:

| Situation | Tool | Why |
|-----------|------|-----|
| Natural language question | `hybrid-search` | Combines all modalities |
| Find similar entities | `semantic-neighbors` | Vector similarity |
| Find entities by name/type | `list-entities` | Exact filtering |
| Find document content | `hybrid-search` with `category` | Scoped doc search |
| Explore around an entity | `get-neighbors` + `get-relations` | Graph traversal |
| Find structural gaps | `semantic-gaps` | Disconnected but similar |

### Hybrid search weights:

```json
{
  "weights": {
    "semantic": 0.6,    // Meaning-based matching
    "keyword": 0.3,     // Exact term matching
    "structural": 0.1   // Graph expansion
  }
}
```

Adjust weights based on query type:
- **Conceptual questions** → increase semantic weight
- **Exact term lookup** → increase keyword weight
- **Relationship exploration** → increase structural weight

---

## 10. Multi-Graph Workflow

Working across multiple knowledge domains.

```
list-graphs → see all graphs
graph-stats (graph: "name") → inspect specific graph

# Create cross-graph connections
create-relation (from: entityA, to: entityB, graph: "bridges") → bridge relation
list-bridges → see all cross-graph connections
find-related-graphs (graph: "main") → discover connected graphs

# Search across graphs
hybrid-search (query, graph: "*") → search all graphs
multi-graph-landscape → comprehensive cross-graph view
```

---

## 11. Epistemic Rigor Workflow

Maintaining knowledge quality.

### Track Provenance
Every entity should have:
1. A `source` entity it traces to
2. A `sources` relation connecting them
3. Confidence score with appropriate basis

### Validate Claims
```
uncertain-claims → find low-confidence claims
single-source-claims → find under-corroborated
needs-evidence → find unsupported claims
contested-claims → find contradictions
```

### Resolve Contradictions
When `contested-claims` returns results:
1. Read both sides with `read-entity`
2. Trace provenance with `provenance-chain`
3. Create `tension` entity capturing the contradiction
4. Or resolve: update confidence, retract one claim

### Version Knowledge
When knowledge evolves:
```
update-entity (id, status: "superseded", statusReason: "outdated_knowledge")
create-entity → new version with updated content
create-relation (supersedes) → new → supersedes → old
```
