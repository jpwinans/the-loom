# HyperResearch — Autonomous Research Workflows

Autonomous research workflows powered by The Loom knowledge graph. Includes two commands:

- **`/deep-research`** — Single-topic autonomous research with quality-driven termination
- **`/hyper-research`** — Meta-orchestrator that extracts questions from context, runs sequential deep-research sessions, and synthesizes results

## Overview

### DeepResearch

Single-topic autonomous research sessions with:
- **Session isolation** - Each research session has its own folder and Loom graph
- **Quality-driven termination** - Research continues until quality threshold met
- **Self-contained sessions** - Everything a run produces lives under its session folder
- **Persistent knowledge graph** - All findings stored in The Loom with epistemic metadata
- **Resume capability** - Can pause and resume research sessions

### HyperResearch

Meta-orchestrator that reads a context document and runs multiple deep-research sessions:
- **Context comprehension** - Reads and structures understanding from any document
- **Graph enrichment** - Queries existing Loom graph to amend understanding and find gaps
- **Question extraction** - Synthesizes 1-5 critical research questions ranked by impact
- **Sequential deep-research** - Runs `/deep-research` for each question into the same graph
- **Expedition synthesis** - Runs `/loom-expedition` on the accumulated graph
- **Incremental reporting** - Report is readable at any phase; partial success yields partial report

## Quick Start

### Installation

1. **All files are already in the `.claude/` directory:**
   - Commands: `.claude/commands/` (deep-research, hyper-research, loom-expedition)
   - Agents: `.claude/agents/`
   - Templates: `.claude/harness-templates/`
   - Hook: `.claude/hooks/check-completion.cjs`

2. **Verify installation:**
   ```bash
   # Commands should be available
   claude /deep-research --help
   claude /hyper-research --help
   ```

### CLI Configuration (Required)

DeepResearch agents interact with The Loom knowledge graph via the `loom` CLI. The CLI must be built and accessible.

1. **Install The Loom CLI:**
   ```bash
   uv sync
   ```

2. **Configure GRAPH_FOLDER for multi-graph mode:**
   ```bash
   export GRAPH_FOLDER="./data/graphs"
   ```
   The `GRAPH_FOLDER` environment variable enables multi-graph mode, which allows each research session to have its own isolated graph.

3. **Verify CLI is working:**
   ```bash
   loom graph-stats
   ```

**Important:** Without this configuration, research agents will be unable to create entities in The Loom graph. They may appear to succeed (writing findings files) but no entities will be persisted to the graph.

### Basic Usage

```bash
# Single-topic research
/deep-research "Systems thinking and organizational change"

# Meta-research from a context document
/hyper-research "path/to/expedition-report.md" --graph my-graph

# Standalone expedition over an accumulated graph
/loom-expedition my-graph --seed "Systems thinking"
```

## Architecture

### Files Structure

```
.claude/
├── agents/                    # 8 specialized agents
│   ├── research-orientation.md       # Context gathering & planning
│   ├── research-agent.md             # Web + document research & Loom population
│   ├── research-synthesis.md         # Pattern recognition & insights
│   ├── research-consolidation.md     # Graph analysis & cleanup
│   ├── research-expedition.md        # Emergent theory discovery
│   ├── research-red-team.md          # Adversarial challenge
│   ├── research-quality.md           # Quality evaluation (Lakatos/flexibility)
│   └── research-documentation.md     # Artifact generation
├── commands/
│   ├── deep-research.md              # Single-topic orchestrator
│   ├── hyper-research.md             # Meta-orchestrator (context → questions → research)
│   └── loom-expedition.md            # Expedition over an accumulated graph
├── harness-templates/
│   ├── research-state.template.json          # Deep-research state template
│   └── hyper-research-state.template.json    # Hyper-research state template
├── hooks/
│   ├── check-completion.cjs          # Workflow completion check (both workflows)
│   └── README.md                     # Hook documentation
└── skills/
    └── the-loom/                     # Loom data model, tool catalog, workflows
```

### Workflow Phases

```
Phase 0: Session Initialization
  ├─ Generate session ID
  ├─ Create folder structure
  ├─ Sandbox memory files
  ├─ Create Loom graph
  └─ Initialize state

Phase 1: Orientation (research-orientation agent)
  └─ Activate researcher identity from sandboxed memory

Phase 2: Orientation (research-orientation agent)
  ├─ Clarify research intention
  ├─ Search ingested documents for context
  ├─ Query existing Loom graphs
  ├─ Create research threads
  └─ Create seed entities

Phase 3: Research Loop (iterates until quality threshold)
  ├─ RESEARCH (research-agent)
  │   ├─ Web search (2-4 searches)
  │   ├─ Fetch sources (3-5 sources)
  │   ├─ Document search
  │   └─ Create source/evidence/claim entities
  ├─ SYNTHESIS (research-synthesis)
  │   ├─ Identify patterns
  │   ├─ Create insights
  │   └─ Detect tensions
  ├─ CONSOLIDATION (research-consolidation)
  │   ├─ Analyze graph structure
  │   ├─ Merge duplicates
  │   └─ Update confidence scores
  └─ QUALITY (research-quality)
      ├─ Lakatos tests (progressive vs degenerative)
      ├─ Flexibility tests
      └─ Decide: CONTINUE or TERMINATE

Phase 4: Documentation (research-documentation agent)
  ├─ Create zettelkasten notes
  ├─ Create research synthesis document
  └─ Create journal reflection

Phase 5: Finalize
  ├─ Write artifact manifest
  └─ Mark session complete
```

### HyperResearch Workflow Phases

```
Phase 0: Initialize
  ├─ Parse arguments (context doc, topic, graph, output)
  ├─ Create session folder (DeepResearch/hyper-sessions/)
  ├─ Create/use Loom graph (loom CLI)
  └─ Create blank incremental report

Phase 1: Comprehension
  ├─ Read context document
  ├─ Extract claims, evidence, conclusions, tensions
  ├─ Identify open questions
  └─ Derive topic if not provided

Phase 2: Graph Exploration (loom CLI)
  ├─ Query Loom graph (hybrid_search, graph_stats, detect_loops)
  ├─ Find existing relevant knowledge
  ├─ Identify gaps between context and graph
  └─ Amend understanding

Phase 3: Question Extraction
  ├─ Synthesize 1-5 research questions from understanding + gaps
  ├─ Rank by expected impact
  └─ If 0 questions → skip to Phase 5

Phase 4: Deep Research (Task agents)
  ├─ For each question sequentially:
  │   ├─ Spawn Task agent → Skill(deep-research ...) → wait
  │   ├─ Read results from sub-session
  │   └─ Append findings to report
  └─ Partial success: failed questions logged, skipped

Phase 5: Expedition (Task agent)
  ├─ Spawn Task agent → Skill(loom-expedition ...) → wait
  └─ Read expedition report → append to report

Phase 6: Synthesis
  ├─ Cross-cutting themes across all sessions
  └─ What remains open

Phase 7: Ingestion (loom CLI)
  ├─ ingest_document into Loom
  └─ Mark complete
```

### Session Folder Structures

#### Deep-Research Sessions

```
DeepResearch/sessions/{SESSION_ID}/
├── research-state.json          # Unified state file
│
├── findings/                    # Raw research outputs
│   ├── iteration-1-research.json
│   ├── iteration-1-synthesis.json
│   └── ...
│
├── quality/                     # Quality check documentation
│   ├── iteration-1-quality.json
│   ├── iteration-1-quality.md
│   └── ...
│
├── artifacts/                   # Output artifacts
│   ├── zettelkasten/           # Atomic notes
│   ├── research/               # Long-form documents
│   └── journal/                # Reflective entries
│
└── artifact-manifest.json       # Index of artifacts written under this session
```

#### Hyper-Research Sessions

```
DeepResearch/hyper-sessions/{SESSION_ID}/
└── hyper-research-state.json    # Central state file

# Sub-sessions live in their standard location:
DeepResearch/sessions/{DR-SESSION-ID}/
└── (standard deep-research structure)

# Incremental report written directly to final location:
DeepResearch/hyper-sessions/{SESSION_ID}/report.md
```

## The Loom Integration

### Entity Types Used

| Type | Usage |
|------|-------|
| `concept` | Core ideas being explored |
| `claim` | Assertions from sources |
| `question` | Open questions driving research |
| `evidence` | Supporting data from sources |
| `source` | Academic papers, articles, ingested documents |
| `pattern` | Recurring structures across domains |
| `insight` | Novel connections discovered |
| `tension` | Productive contradictions |
| `convergence` | Multiple sources pointing same direction |

### Relation Types Used

| Type | Usage |
|------|-------|
| `sources` | Links evidence to sources |
| `supports` | Evidence supporting claims/insights |
| `contradicts` | Conflicting information |
| `causes` | Causal relationships |
| `enables` | Prerequisite relationships |
| `related_to` | General connections |
| `questions` | Raises doubt about |
| `supersedes` | Newer understanding replacing old |

### Epistemic Metadata

**Confidence Tracking:**
```json
{
  "confidence": {
    "score": 0.75,
    "basis": "peer_reviewed",
    "lastEvaluated": "2026-01-27T10:30:00Z"
  }
}
```

**Basis Values:**
- `peer_reviewed` - Academic source
- `multiple_sources` - Convergent findings
- `single_source` - One authoritative reference
- `inference` - Derived from synthesis
- `speculation` - Exploratory hypothesis
- `llm_extraction` - Extracted by research agent

**Provenance Tracking:**
```json
{
  "provenance": {
    "sourceType": "document",
    "sourceId": "source-entity-id",
    "externalRef": "https://doi.org/...",
    "extractionDate": "2026-01-27T10:25:00Z",
    "extractor": "research-agent",
    "extractionMethod": "llm_prompted"
  }
}
```

### Cross-Graph Bridges

Research sessions create isolated graphs. Bridges connect session insights to another graph:

```bash
# Create bridge from the session graph to another graph
loom create-relation '{"from":"<session_insight_id>","to":"<target_concept_id>","relationType":"supports","polarity":null,"strength":"moderate","evidence":"<why this relation holds>","sourceGraph":"research-2026-01-27-topic-001","targetGraph":"default","observations":["Research iteration 2 synthesis"]}'
# Auto-creates a bridge relation, held in FalkorDB
```

## Quality Evaluation

### Lakatos Tests (Progressive vs Degenerative)

Research is **progressive** if it:
1. **Novel Predictions** - Generates testable predictions (0-10)
2. **Corroboration** - Predictions supported by evidence (0-10)
3. **Expanding Scope** - Understanding extends to new domains (0-10)

Research is **degenerative** if it:
- Repeats previous findings
- Generates no new predictions
- Becomes circular or ad-hoc

### Flexibility Tests

Research is **flexible** if it:
1. **Paradigm Flexibility** - Can consider alternative frameworks (0-10)
2. **Integration** - Synthesizes diverse sources (0-10)
3. **Self-Correction** - Identifies and corrects errors (0-10)

Research is **rigid** if it:
- Stuck in one paradigm
- Ignores contradictory evidence
- Cannot adapt approach

### Termination Logic

```typescript
const overallScore = average([
  lakatosTests.novelPredictions,
  lakatosTests.corroboration,
  lakatosTests.expandingScope,
  flexibilityTests.paradigmFlexibility,
  flexibilityTests.integration,
  flexibilityTests.selfCorrection
]);

if (overallScore >= 7.0) {
  return { continueResearch: false, reason: "Quality threshold met" };
}

if (iterationCount >= maxIterations) {
  return { continueResearch: false, reason: "Max iterations reached" };
}

return { continueResearch: true };
```

## Autonomous Execution

### Critical Constraints

1. **No User Questions** - Makes reasonable assumptions, logs decisions
2. **Flat Agent Hierarchy** - All agents spawned directly by command
3. **No Direct Research** - Command coordinates; agents do the work
4. **Continuous Execution** - Runs until phase = "complete" or error

### Error Handling

| Error | Recovery |
|-------|----------|
| Agent fails | Retry once, log, continue |
| Web search fails | Continue with ingested documents only |
| RAG unavailable | Continue with web only |
| Quality unclear | Default CONTINUE for iterations 1-5, then TERMINATE |

## Hook

### Check-completion Hook
Ensures autonomous workflows run to completion. Supports two workflow types with priority order:
1. `/hyper-research` — checks `DeepResearch/hyper-sessions/*/hyper-research-state.json`
2. `/deep-research` — checks `DeepResearch/sessions/*/research-state.json`

Hyper-research is checked before deep-research because during Phase 4, active deep-research sub-sessions exist. The hook must prioritize the hyper-research orchestrator to avoid blocking on a sub-session.

See `hooks/README.md` for detailed documentation.

## Testing

Run the repo's test suite:
```bash
uv run pytest
```

`tests/test_claude_examples_contract.py` covers this directory: it harvests every
`loom <command> '<json>'` in `.claude/` and validates the payload against that
command's CLI input model, so an example that drifts out of contract fails the
build.

## Example Session

```bash
# Start research
$ /deep-research "How does systems thinking enable organizational change?"

[Phase 0] Initializing session: 2026-01-27-systems-thinking-001
[Phase 2] Orientation: Context gathered, 2 threads created
[Phase 3] Research Loop Iteration 1:
  - Research: 3 web searches, 5 sources, 12 entities created
  - Synthesis: 3 insights, 1 tension identified
  - Consolidation: Graph analyzed, 2 duplicates merged
  - Quality: Score 8.0/10 (progressive, flexible) → TERMINATE
[Phase 4] Documentation: 1 zettelkasten, 1 research doc, 1 journal created
[Phase 5] Finalize: Session complete

Session: 2026-01-27-systems-thinking-001
Topic: How does systems thinking enable organizational change?
Iterations: 1
Quality: 8.0/10
Loom Graph: research-2026-01-27-systems-thinking-001 (45 entities, 78 relations)

Artifacts written to: DeepResearch/sessions/2026-01-27-systems-thinking-001/artifacts/
- zettelkasten/20260127143000-feedback-loops-organizational-change.md
- research/systems-thinking-organizational-change-synthesis.md
- journal/2026-01-27-systems-thinking-reflection.md
```

### HyperResearch Example

```bash
# Start hyper-research from an expedition report
$ /hyper-research "DeepResearch/expeditions/externalized-rl-2026-02-11.md" \
    --graph externalized-rl

[Phase 0] Initializing session: hyper-2026-02-12-externalized-rl-001
[Phase 1] Comprehension: 8 claims, 5 open questions, 3 tensions extracted
[Phase 2] Graph Exploration: 15 existing entities, 4 gaps identified
[Phase 3] Question Extraction: 3 questions ranked by impact
[Phase 4] Deep Research:
  - Q1 "Practice logging protocols for epistemic RL": Session complete (7.5/10)
  - Q2 "Confidence evolution as gradient signal": Session complete (8.0/10)
  - Q3 "Bootstrap loop activation conditions": Session complete (7.2/10)
[Phase 5] Expedition: Emergent theory discovered across accumulated graph
[Phase 6] Synthesis: 4 cross-cutting themes, 2 remaining open questions
[Phase 7] Ingestion: Report ingested to Loom

Session:    hyper-2026-02-12-externalized-rl-001
Topic:      Externalized RL via The Loom
Graph:      externalized-rl (expanded from 15 to 180+ entities)
Questions:  3/3 completed (0 failed)
Report:     DeepResearch/hyper-sessions/hyper-2026-02-12-externalized-rl-001/report.md
```

## Architecture Patterns

### Orchestration Pattern

✓ **Command orchestrates directly** (no orchestrator skill)
✓ **Agents in agents/ directory** (not skills/)
✓ **Flat hierarchy** (all agents spawned by command)
✓ **Autonomous execution** (no user questions)
✓ **Hook integration** (check-completion ensures workflow completes)

| Aspect | /deep-research |
|--------|----------------|
| Workspace | `DeepResearch/sessions/` |
| State file | `research-state.json` |
| Units | Research Iterations |
| Termination | Quality threshold |
| Output | Research artifacts |
| Knowledge | None | The Loom graph |

## Migration from v1

**v1 used:**
- Python `concept_map.py` script
- Multiple state files
- Manual quality assessment

**v2 uses:**
- The Loom knowledge graph
- Single `research-state.json`
- Autonomous quality evaluation

**Existing v1 sessions:**
- Can remain as-is
- Won't be migrated to v2
- New sessions use v2 automatically

## Contributing

When adding features:
1. Update agent specifications in `agents/`
2. Update command orchestration in `commands/`
3. Keep `loom` invocations valid — `uv run pytest tests/test_claude_examples_contract.py`
4. Update this README

## License

Part of The Loom project. See main repository for license information.

---

**DeepResearch:** Single-topic autonomous research with quality-driven termination
**HyperResearch:** Meta-orchestrator layered on top of DeepResearch
**Architecture:** Multi-agent orchestration with The Loom integration
