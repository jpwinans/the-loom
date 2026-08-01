# Loom Workflows

Common multi-step patterns for effective Loom usage.

---

## 1. Orientation: Understanding an Existing Graph

Start any session by understanding what's already there.

```
graph_stats → understand scale
list_entities (by type) → see what exists
analyze_centrality (degree) → find hubs
detect_components → find isolated clusters
```

**Fast version:** Use `graph_reconnaissance` composite — does all of the above in one call.

---

## 2. Research Workflow

Building knowledge from sources.

### Phase 1: Seed Sources
```
create_entity (type: source) → register each source
ingest_document (with category) → make content searchable
```

### Phase 2: Extract Claims
```
hybrid_search (query, category) → find relevant chunks
create_entity (type: claim) → atomic assertions
create_relation (sources) → claim → sources → source
create_relation (supports) → evidence → supports → claim
```

### Phase 3: Synthesize
```
semantic_gaps → find disconnected similar entities
suggest_relations → propose missing connections
create_entity (type: insight) → synthesized understanding
create_entity (type: pattern) → recurring structures
```

### Phase 4: Validate
```
uncertain_claims → find weak claims
single_source_claims → find under-supported claims
contested_claims → find contradictions
check_consistency → verify graph integrity
```

---

## 3. System Modeling Workflow

Building causal models.

### Step 1: Define System
```
create_entity (type: system) → boundary and purpose
create_entity (type: variable) → each measurable quantity
create_relation (part_of) → variable → part_of → system
```

### Step 2: Map Causation
```
create_relation (causes/enables/inhibits) → with polarity + strength
```

**Polarity rules:**
- A increases → B increases: polarity `+`
- A increases → B decreases: polarity `-`

### Step 3: Detect Dynamics
```
detect_loops → find feedback loops
```

**Loop classification:**
- Even number of `-` edges → reinforcing (exponential)
- Odd number of `-` edges → balancing (stabilizing)

### Step 4: Find Leverage Points
```
create_entity (type: leverage_point) → with level, intervention
list_leverage_points → review by depth category
leverage_point_details → full analysis
```

### Step 5: Simulate
```
simulate_change → what-if analysis on entities
influence_map → trace causal flow from seed
```

---

## 4. Codebase Cognition Workflow

Understanding code through graph analysis.

### Layer 1: Extract Structure
```
extract_codebase → SCIP-based TS/JS extraction
  OR
ingest_directory → for Python/other languages
extract_from_documents → LLM extraction
```

### Layer 2: Semantic Enrichment
Read code module by module. For each:
```
create_entity (type: pattern) → design patterns found
create_entity (type: claim) → invariants and contracts
create_entity (type: evidence) → risks identified
create_entity (type: tension) → contradictions found
```

### Layer 3: Analyze Architecture
```
analyze_centrality (degree) → find hub modules
detect_cycles → circular dependencies
find_clusters → module communities
semantic_gaps → implicit connections
```

---

## 5. Document-to-Knowledge Pipeline

Converting documents into structured knowledge.

```
ingest_document (filePath, category) → chunk and embed
hybrid_search (query) → verify ingestion
extract_from_documents (category, focus) → LLM extraction into entities
embed_entities → make new entities searchable
check_consistency → verify integrity
```

**Best practice:** Use `extract_preview` before `extract_from_documents` to preview what will be created.

---

## 6. Hypothesis Generation Workflow

Discovering what's missing and generating novel proposals.

### Quick: Use the Composite
```
hypothesis_engine → full pipeline in one call
  Sections: gaps → proposals → quality filter → dedup gate → ranking
```

Returns scored hypotheses with novelty, plausibility, and testability metrics.

### Manual: Step by Step
```
semantic_gaps → find similar but unconnected entities
propose_entities → generate candidates for missing concepts
```

Then review and create:
```
create_entity (type: hypothesis) → with prediction and test plan
create_relation (supports/contradicts) → link to existing evidence
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
find_frequent_subgraphs → discover recurring patterns
  e.g., {"frequencyThreshold": 3, "maxMotifSize": 4}
```

### Step 2: Find Structural Matches
```
find_subgraph_matches → search for a specific pattern shape
  e.g., pattern with concept → causes → variable → enables → pattern
```

### Step 3: Map Across Domains
```
cross_domain_mapping → map concepts by structural role
  sourceDomain: {"entityType": "concept", "label": "Biology"}
  targetDomain: {"entityType": "concept", "label": "Economics"}
```

Maps by relational signature (degree profile, relation types, neighbor types), not surface similarity. Set `entityTypeWeight: 0` to map purely by structure.

### Step 4: Explore Concept Slippage
```
concept_slippage → find creative substitutions
  conceptId: <uuid>, temperature: 0.5
```

Temperature controls distance:
- **Low (0-0.3):** Near-synonyms, same entity type
- **Medium (0.3-0.7):** Role-based analogues, different surface form
- **High (0.7-1.0):** Creative leaps, cross-domain associations

### Combining Tools
A powerful pattern: use `find_frequent_subgraphs` to discover motifs, then `cross_domain_mapping` to see if the same motif appears in another domain, then `concept_slippage` to explore what substitutions would make the analogy work.

---

## 8. Graph Maintenance Workflow

Keeping the graph healthy.

### Regular Checks
```
stale_beliefs (daysOld: 30) → find outdated entities
unprovenanced → find entities missing provenance
check_invariants → verify constraints
embedding_status → check embedding coverage
```

### Cleanup
```
update_entity (status: superseded) → mark outdated
update_entity (status: retracted) → mark invalid
semantic_gaps → find disconnected knowledge
gap_fill_cycle → automated gap detection + resolution
```

### Quality Improvement
```
self_improve → autonomous graph refinement
postmortem_evaluate → evaluate session quality
propagate_credit → cascade confidence changes
reify_patterns → discover and materialize patterns
```

---

## 9. Search Strategy Guide

### Which search to use:

| Situation | Tool | Why |
|-----------|------|-----|
| Natural language question | `hybrid_search` | Combines all modalities |
| Find similar entities | `semantic_neighbors` | Vector similarity |
| Find entities by name/type | `list_entities` | Exact filtering |
| Find document content | `hybrid_search` with `category` | Scoped doc search |
| Explore around an entity | `get_neighbors` + `get_relations` | Graph traversal |
| Find structural gaps | `semantic_gaps` | Disconnected but similar |

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
list_graphs → see all graphs
graph_stats (graph: "name") → inspect specific graph

# Create cross-graph connections
create_relation (from: entityA, to: entityB, graph: "bridges") → bridge relation
list_bridges → see all cross-graph connections
find_related_graphs (graph: "main") → discover connected graphs

# Search across graphs
hybrid_search (query, graph: "*") → search all graphs
multi_graph_landscape → comprehensive cross-graph view
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
uncertain_claims → find low-confidence claims
single_source_claims → find under-corroborated
needs_evidence → find unsupported claims
contested_claims → find contradictions
```

### Resolve Contradictions
When `contested_claims` returns results:
1. Read both sides with `read_entity`
2. Trace provenance with `provenance_chain`
3. Create `tension` entity capturing the contradiction
4. Or resolve: update confidence, retract one claim

### Version Knowledge
When knowledge evolves:
```
update_entity (id, status: "superseded", statusReason: "outdated_knowledge")
create_entity → new version with updated content
create_relation (supersedes) → new → supersedes → old
```
