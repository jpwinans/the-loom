---
description: Run a standalone Loom expedition to discover emergent theories from graph structure
argument-hint: <GRAPH_NAME> [--seed TOPIC] [--session-folder PATH] [--iteration N]
allowed-tools: Read, Write, Bash, Grep, Glob, mcp__the-loom__*
model: opus
---

# Loom Expedition Command

Run a standalone Loom expedition to discover emergent theories from accumulated graph structure.
This command excavates implicit causal chains, emergent dynamics, and surprising long-range
connections that no individual research step explicitly created.

Can be invoked standalone or as a Skill from hyper-research Phase 5.

## Usage

```
/loom-expedition GRAPH_NAME [--seed TOPIC] [--session-folder PATH] [--iteration N]
```

## Arguments

- **`$ARGUMENTS[0]`** (required): Name of the Loom graph to explore
- **`--seed TOPIC`** (optional): Topic string used as initial search seed. If not provided, uses graph centrality to find starting point.
- **`--session-folder PATH`** (optional): Path to write expedition findings. Default: `DeepResearch/expeditions/`
- **`--iteration N`** (optional): Iteration number for the findings filename. Default: 1

## Examples

```bash
# Standalone expedition on a graph
/loom-expedition computational-invention

# With a seed topic
/loom-expedition externalized-rl --seed "Externalized RL"

# From hyper-research (with session folder)
/loom-expedition hyper-2026-02-12-rl-001 --seed "Reinforcement Learning" --session-folder "DeepResearch/hyper-sessions/hyper-2026-02-12-rl-001"
```

---

## CRITICAL CONSTRAINTS

<critical-constraint>
### Read-Only on Graph
This command MUST NOT create, modify, or delete any entities or relations. It only READS
the graph structure to find what the accumulated knowledge implies.

### Autonomous Execution
This is an unattended workflow. Do not ask the user questions. Make reasonable assumptions
and continue forward.
</critical-constraint>

---

## Execution Steps

### Step 0: Parse Arguments

```
GRAPH_NAME = $ARGUMENTS[0]   # Required: graph name

# Parse optional flags
SEED_TOPIC = ""
SESSION_FOLDER = ""
ITERATION = 1

for each argument pair:
  --seed -> SEED_TOPIC
  --session-folder -> SESSION_FOLDER
  --iteration -> ITERATION
```

If SESSION_FOLDER is not provided, create a default:
```
SESSION_FOLDER = "DeepResearch/expeditions/expedition-{GRAPH_NAME}-{date}"
mkdir -p "${SESSION_FOLDER}/findings"
```

If SESSION_FOLDER is provided, ensure `findings/` subdirectory exists:
```
mkdir -p "${SESSION_FOLDER}/findings"
```

### Step 1: Reconnaissance

Gather the graph landscape using Loom CLI:

```bash
# Graph landscape
loom graph-stats '{"graph":"'"${GRAPH_NAME}"'"}'

# Feedback dynamics
loom detect-loops '{"graph":"'"${GRAPH_NAME}"'","maxSize":6}'

# Cross-graph bridges
loom list-bridges '{"graph":"'"${GRAPH_NAME}"'"}'
```

Parse results. Early exit if graph has < 20 entities:
- Write minimal findings with `discoveries: []` and status "complete"
- Report: "Graph has only N entities -- too sparse for meaningful expedition analysis."
- Exit successfully

### Step 2: Thread Selection

Find the most interesting thread to follow.

If SEED_TOPIC is provided, search for topic-relevant entities:
```bash
loom list-entities '{"query":"'"${SEED_TOPIC}"'","graph":"'"${GRAPH_NAME}"'","limit":10}'
```

Selection criteria (in priority order):
1. **Topic-relevant loop/bridge member**: Entity matching seed topic that also appears in loops or bridges
2. **Most-looped entity**: Entity appearing in the most feedback loops
3. **Bridge entity**: Entity connecting separate knowledge domains
4. **Highest-degree entity**: Most connections (fallback via centrality)

If priorities 1-3 yield nothing, use centrality:
```bash
loom analyze-centrality '{"metric":"degree","limit":5,"graph":"'"${GRAPH_NAME}"'"}'
```

Record THREAD_SEED (entity ID) and selection reason.

### Step 3: Influence Mapping

Map causal influence from the selected seed:

```bash
# Confidence-weighted causal reach
loom semiring-distances '{"source":"'"${THREAD_SEED}"'","semiring":"viterbi","graph":"'"${GRAPH_NAME}"'"}'

# Immediate neighborhood
loom get-neighbors '{"entityId":"'"${THREAD_SEED}"'","graph":"'"${GRAPH_NAME}"'"}'

# Read the seed entity
loom read-entity '{"id":"'"${THREAD_SEED}"'","graph":"'"${GRAPH_NAME}"'"}'
```

From semiring-distances output, identify 3-5 most distant reachable targets.
Read each distant target entity. Identify the most surprising long-range connection --
two entities that are causally linked but whose relationship is not obvious from their
names and observations alone.

Record THEORY_SOURCE, THEORY_TARGET.

### Step 4: Path Analysis

If a surprising long-range connection was found, analyze the path:

```bash
# Weakest link in the chain
loom semiring-bottleneck '{"source":"'"${THEORY_SOURCE}"'","target":"'"${THEORY_TARGET}"'","semiring":"capacity","graph":"'"${GRAPH_NAME}"'"}'

# How many paths exist
loom semiring-traverse '{"source":"'"${THEORY_SOURCE}"'","target":"'"${THEORY_TARGET}"'","semiring":"counting","graph":"'"${GRAPH_NAME}"'"}'

# Most direct chain
loom find-shortest-path '{"source":"'"${THEORY_SOURCE}"'","target":"'"${THEORY_TARGET}"'","graph":"'"${GRAPH_NAME}"'"}'
```

Read every entity along the shortest path to understand the substance.

### Step 5: Context Check

Gather broader causal context:

```bash
# Pure causal network from seed
loom cross-type-query '{"source":"'"${THREAD_SEED}"'","relationTypes":["causes","enables","inhibits","amplifies","dampens","requires"],"graph":"'"${GRAPH_NAME}"'"}'

# Semantic neighbors of the conclusion
loom semantic-neighbors '{"entityId":"'"${THEORY_TARGET}"'","limit":5,"graph":"'"${GRAPH_NAME}"'"}'
```

### Step 6: Write Findings

Write expedition results to the findings directory:

```
{SESSION_FOLDER}/findings/expedition-iteration-{ITERATION}.json
```

The findings JSON includes:
- `status`: "complete"
- `iteration`: iteration number
- `graphStats`: graph statistics
- `loopsFound`: feedback loops with size and type
- `bridgesFound`: bridge entities
- `threadSeed`: selected seed with rationale
- `emergentTheory`: surprising connection analysis (or "not found")
- `discoveries`: array of all notable findings with plain-language summaries

Also write a human-readable report to:
```
{SESSION_FOLDER}/findings/expedition-report-{ITERATION}.md
```

The report includes:
- Graph overview
- Thread selection rationale
- Emergent theory (if found) with plain-language narrative
- All discoveries
- Structural observations

### Step 7: Report Completion

Output completion summary:

```
Loom Expedition Complete
========================
Graph:       {GRAPH_NAME}
Seed:        {SEED_TOPIC or "centrality-based"}
Entities:    {entity count}
Loops:       {loop count}
Bridges:     {bridge count}
Discoveries: {discovery count}
Theory:      {found/not found}
Report:      {report path}
Findings:    {findings JSON path}
```

---

## Error Handling

### Semiring Operations Fail
Log limitation, skip Steps 3-4, report only topology findings from Step 1.

### No Loops Found
Proceed with centrality-based thread selection. The expedition can still discover
cross-domain bridges and surprising connections through semiring distances.

### Graph Has < 20 Entities
Report "too sparse for expedition" and write minimal findings. This is normal for
early iterations.

### All Loom CLI Failures Are Non-Blocking
Log the error, record what was attempted, continue with whatever data is available.
A partial expedition report is better than no report.

---

## Loom Operations Reference

### Reconnaissance Tools

| Tool | Purpose |
|------|---------|
| `graph-stats` | Get entity/relation counts, graph overview |
| `detect-loops` | Find feedback cycles up to maxSize |
| `list-bridges` | Find entities that bridge separate components |
| `list-entities` | Search for entities by query string |
| `analyze-centrality` | Calculate node importance (degree, betweenness) |

### Influence Mapping Tools

| Tool | Purpose |
|------|---------|
| `semiring-distances` | Confidence-weighted causal reach from a source |
| `get-neighbors` | Immediate connections of an entity |
| `read-entity` | Read full entity details |

### Path Analysis Tools

| Tool | Purpose |
|------|---------|
| `semiring-bottleneck` | Find weakest link in a causal chain |
| `semiring-traverse` | Count paths between two entities |
| `find-shortest-path` | Find most direct chain between two entities |

### Context Tools

| Tool | Purpose |
|------|---------|
| `cross-type-query` | Follow only causal relation types from a starting point |
| `semantic-neighbors` | Find conceptually similar entities |
