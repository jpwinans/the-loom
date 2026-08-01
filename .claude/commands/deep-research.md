---
description: Autonomous deep research workflow using The Loom knowledge substrate
argument-hint: <TOPIC>
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task
model: opus
hooks:
    Stop:
      - hooks:
          - type: command
            command: "node .claude/hooks/check-completion.cjs"
            timeout: 5
---

# DeepResearch v3 Command

Orchestrate an autonomous deep research workflow using The Loom as the knowledge substrate.

## Usage

```
/deep-research TOPIC [--graph GRAPH_NAME]
```

## Arguments

- **`$ARGUMENTS[0]`** (required): The research topic to investigate
- **`--graph GRAPH_NAME`** (optional): Custom graph name for this research. If provided, the session will use this graph instead of creating a new `research-{SESSION_ID}` graph. This allows multiple research sessions to build into the same graph.

## Examples

```bash
# Basic usage (creates new graph per session)
/deep-research "Systems thinking and organizational change"

# More specific topics
/deep-research "Causal loop analysis in feedback systems"
/deep-research "Epistemic frameworks for knowledge representation"

# Use a custom graph (multiple sessions build into same graph)
/deep-research "Computational Analogy" --graph computational-invention
/deep-research "Causal Discovery" --graph computational-invention
/deep-research "Constraint Synthesis" --graph computational-invention

# Resume interrupted work (automatically detects existing session)
/deep-research "Systems thinking and organizational change"
```

## Multi-Session Research Projects

When researching a large topic across multiple sessions, use the `--graph` parameter to build all findings into a single graph:

```bash
# Session 1: Survey existing systems
/deep-research "Scientific Discovery Systems survey" --graph computational-invention

# Session 2: Deep dive on analogy
/deep-research "Computational Analogy and Structure Mapping" --graph computational-invention

# Session 3: Deep dive on causal discovery
/deep-research "Causal Discovery from Observational Data" --graph computational-invention
```

Each session adds to the same `computational-invention` graph, building a comprehensive knowledge base across sessions. The graph persists and accumulates knowledge.

---

## CRITICAL CONSTRAINTS

<critical-constraint>
### Autonomous Execution
This is an unattended, fully autonomous workflow. You MUST NOT:
- Ask the user questions (no questions allowed)
- Request clarification or confirmation
- Pause for human input at any point

Instead: Make reasonable assumptions, log decisions to research-state.json, continue forward.

### Flat Agent Hierarchy
ALL agents are spawned DIRECTLY by this command. NO agent spawns another agent.
This ensures reliable Task tool usage and full orchestrator visibility.

### No Direct Research
You are FORBIDDEN from performing research yourself. ALL research goes through the research-agent.
The command coordinates phases; agents do the work.
</critical-constraint>

---

## MAIN EXECUTION LOOP

<critical>
**YOU MUST EXECUTE THIS ENTIRE WORKFLOW FROM START TO FINISH WITHOUT STOPPING.**

After completing Phase 0 initialization, immediately continue executing phases until phase = "complete":

```
LOOP:
  1. Read research-state.json to get current phase
  2. Execute the appropriate phase handler (see sections below)
  3. After phase completes, read research-state.json again
  4. If phase != "complete": GOTO LOOP
  5. If phase == "complete": Report completion and exit
```

**DO NOT:**
- Stop after completing a single phase
- Wait for user input between phases
- Report progress and pause

**DO:**
- Execute phases sequentially and continuously
- Only stop when phase = "complete" or unrecoverable error
- Log progress to phaseSummary in state file
</critical>

---

## State Machine Overview

```
Phase 0: Initialize Session (this command)
  |
  v
Phase 1: ORIENTATION (phase = "orientation")
  - Spawn: research-orientation agent
  - Purpose: Clarify intention, gather context, create initial Loom entities
  - On complete: phase -> "research_loop"
  |
  v
Phase 2: RESEARCH LOOP (phase = "research_loop")
  - Iterates until quality threshold or maxIterations
  - Each iteration runs steps in sequence:
    1. research-agent: Gather findings
    2. research-synthesis: Identify patterns
    3. research-consolidation: Clean graph
    4. research-expedition: Discover emergent theories (conditional)
    5. research-red-team: Adversarial challenge (conditional)
    6. research-quality: Evaluate and decide continue/terminate
  - On complete: phase -> "documentation"
  |
  v
Phase 3: DOCUMENTATION (phase = "documentation")
  - Spawn: research-documentation agent
  - Purpose: Create research artifacts
  - On complete: phase -> "finalize"
  |
  v
Phase 4: FINALIZE (phase = "finalize")
  - Write artifact manifest
  - Update final state
  - Report completion
  - phase -> "complete"
```

---

## Phase 0: Initialize Session

**Parse arguments:**
```bash
TOPIC="${ARGUMENTS[0]}"

# Parse optional --graph parameter
CUSTOM_GRAPH=""
for i in "${!ARGUMENTS[@]}"; do
  if [[ "${ARGUMENTS[$i]}" == "--graph" ]]; then
    CUSTOM_GRAPH="${ARGUMENTS[$((i+1))]}"
  fi
done
```

**Generate Session ID:**

Format: `YYYY-MM-DD-{topic-slug}-NNN`

Where:
- `YYYY-MM-DD` is the current date
- `{topic-slug}` is a lowercase, hyphenated version of the topic (max 30 chars)
- `NNN` is a 3-digit counter (001, 002, etc.) to handle multiple sessions on same topic/day

Example: `2026-01-27-systems-thinking-001`

**Check for existing session:**
```bash
SESSION_BASE="DeepResearch/sessions"
# Generate slug from topic
SLUG=$(echo "$TOPIC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | cut -c1-30)
DATE=$(date +%Y-%m-%d)

# Find highest existing counter for this date+slug
COUNTER=1
while [ -d "${SESSION_BASE}/${DATE}-${SLUG}-$(printf '%03d' $COUNTER)" ]; do
    COUNTER=$((COUNTER + 1))
done

SESSION_ID="${DATE}-${SLUG}-$(printf '%03d' $COUNTER)"
SESSION_FOLDER="${SESSION_BASE}/${SESSION_ID}"
```

**Create Session Folder Structure:**

```
DeepResearch/sessions/{SESSION_ID}/
├── research-state.json          # Unified state file (from template)
│
├── findings/                    # Raw research outputs
├── quality/                     # Quality check documentation
├── artifacts/                   # Output artifacts
│   ├── zettelkasten/           # Atomic notes
│   ├── research/               # Long-form documents
│   └── journal/                # Reflective entries
│
└── artifact-manifest.json       # Index of artifacts written here (created later)
```

```bash
# Create directory structure
mkdir -p "${SESSION_FOLDER}/findings"
mkdir -p "${SESSION_FOLDER}/quality"
mkdir -p "${SESSION_FOLDER}/artifacts/zettelkasten"
mkdir -p "${SESSION_FOLDER}/artifacts/research"
mkdir -p "${SESSION_FOLDER}/artifacts/journal"
```

**Copy State Template:**

```bash
cp .claude/harness-templates/research-state.template.json "${SESSION_FOLDER}/research-state.json"
```

Note: Template paths are relative to the project root (the expected CWD for all commands).

**Update State File:**

Read the template, update these fields, write back:
- `sessionId` -> `${SESSION_ID}`
- `topic` -> `${TOPIC}`
- `graphName` -> `${CUSTOM_GRAPH}` if provided, otherwise `research-${SESSION_ID}`
- `sessionFolder` -> `${SESSION_FOLDER}`
- `phase` -> `"orientation"`
- `metadata.createdAt` -> current ISO timestamp
- `metadata.updatedAt` -> current ISO timestamp
- `metadata.customGraph` -> `true` if CUSTOM_GRAPH provided, otherwise `false`

**Determine Graph Name:**

```bash
if [ -n "$CUSTOM_GRAPH" ]; then
  GRAPH_NAME="$CUSTOM_GRAPH"
  echo "Using custom graph: $GRAPH_NAME"
else
  GRAPH_NAME="research-${SESSION_ID}"
  echo "Creating session graph: $GRAPH_NAME"
fi
```

**Create or Use Loom Graph:**

If using a custom graph name, check if the graph already exists:
- If exists: Use it (add to existing knowledge)
- If not exists: Create it

If using default session graph name: Always create new.

```bash
# Ensure GRAPH_FOLDER is set for loom CLI multi-graph mode
export GRAPH_FOLDER="${GRAPH_FOLDER:-./data/graphs}"

# Using the loom CLI
if [ -n "$CUSTOM_GRAPH" ]; then
  # Check if graph exists
  EXISTING=$(loom list-graphs '{}' 2>/dev/null)
  # If not exists, create graph
  loom create-graph '{"name":"'"$CUSTOM_GRAPH"'"}'
  # If exists, log "Adding to existing graph: $CUSTOM_GRAPH"
else
  # Always create new graph for session-specific naming
  loom create-graph '{"name":"research-'"${SESSION_ID}"'"}'
fi
```

The graph will store:
- Concepts discovered during research
- Claims and evidence gathered
- Questions driving the research
- Patterns and insights synthesized
- Sources referenced
- Relations connecting all entities

**Classify Research Question (Phase 0 Classification):**

Before starting the research loop, classify the research question to determine iteration limits and feature flags. This classification governs the entire session's behavior.

```
CLASSIFY question "${TOPIC}":
  IF question seeks a specific fact, date, name, or number:
    type = "A" (Lookup)
    maxIterations = 2
    enableRedTeam = false
    enableCheckpoint = false
  ELSE IF question asks to combine or compare known information:
    type = "B" (Synthesis)
    maxIterations = 3
    enableRedTeam = false
    enableCheckpoint = true
  ELSE IF question requires evaluating evidence or analyzing tradeoffs:
    type = "C" (Analysis)
    maxIterations = 5
    enableRedTeam = true
    enableCheckpoint = true
  ELSE (open-ended exploration, investigation):
    type = "D" (Investigation)
    maxIterations = 7
    enableRedTeam = true
    enableCheckpoint = true
```

Write the classification result to state:
```typescript
state.questionClassification = {
  type: "<A|B|C|D>",
  maxIterations: <2|3|5|7>,
  enableRedTeam: <true|false>,
  enableCheckpoint: <true|false>,
  classificationRationale: "<brief explanation of why this type was chosen>"
};
// Override the top-level maxIterations with the classification-derived value
state.maxIterations = state.questionClassification.maxIterations;
```

**After initialization:** Set phase to "orientation" and **IMMEDIATELY CONTINUE TO PHASE 1.**

---

## Phase 1: Orientation (phase = "orientation")

**Entry State:** `phase = "orientation"`

**Actions:**
1. Read research-state.json
2. Spawn research-orientation agent using Task tool:
   ```
   Task tool with subagent_type: "research-orientation"

   Arguments:
     - SESSION_FOLDER: {state.sessionFolder}
     - TOPIC: {state.topic}
     - GRAPH_NAME: {state.graphName}
   ```
3. Wait for agent completion
4. Read state to get context.intention and context.initialQuestions
5. Update state:
   - `phase` -> `"research_loop"`
   - `phaseSummary` -> summary of orientation results
   - `metadata.updatedAt` -> current ISO timestamp

**Expected Agent Output:**
- context.priorFindings populated (may be empty if no prior graphs were declared)
- context.relatedConcepts populated (from the prior-knowledge query + new entities)
- context.intention populated (informed by priorFindings)
- context.constraints populated
- context.initialQuestions populated (targeting gaps in prior knowledge)
- Initial Loom entities created (no duplicates of prior knowledge)

**IMMEDIATELY CONTINUE TO PHASE 2.**

---

## Phase 2: Research Loop (phase = "research_loop")

**Entry State:** `phase = "research_loop"`

This phase runs iteratively until quality threshold is met or `state.questionClassification.maxIterations` is reached. The iteration limit is determined by the question classification performed in Phase 0.

**Loop Structure:**
```
RESEARCH_LOOP:
  // Use classification-derived iteration limit
  MAX_ITER = state.questionClassification.maxIterations || state.maxIterations

  1. Check: iterationCount >= MAX_ITER?
     YES -> phase = "documentation", exit loop
     NO -> continue

  2. Execute research_loop steps in order:
     a. RESEARCH STEP
     b. SYNTHESIS STEP
     c. VERIFICATION STEP (orchestrator reads agent outputs)
     d. CONSOLIDATION STEP

     // Expedition (conditional -- needs accumulated structure)
     f. IF iterationCount >= 1 AND graph has >= 20 entities:
          Spawn research-expedition agent
          Read expedition results from findings/expedition-iteration-{iterationCount}.json

     // Red Team (conditional -- activated for Type C/D only)
     g. IF state.questionClassification.enableRedTeam == true
        AND iterationCount >= 2:
          Spawn research-red-team agent
          Read red team results from findings/red-team-iteration-{iterationCount}.json

     // Checkpoint Aggregation (conditional -- activated for Type B/C/D)
     h. IF state.questionClassification.enableCheckpoint == true
        AND iterationCount == 2:
          Perform checkpoint aggregation (orchestrator, not a separate agent)
          Write checkpoint file to {sessionFolder}/checkpoint-aggregation.json
          Update state.checkpointAggregation
          Feed recommendations into next iteration research queries

     i. QUALITY STEP

  3. Increment iterationCount

  4. Check quality agent result:
     - If continueResearch = true AND iterationCount < MAX_ITER:
       GOTO RESEARCH_LOOP
     - If continueResearch = false OR iterationCount >= MAX_ITER:
       phase = "documentation", exit loop
```

### Research Loop Step: Research

**Spawn research-agent using Task tool:**
```
Task tool with subagent_type: "research-agent"

Arguments:
  - SESSION_FOLDER: {state.sessionFolder}
  - GRAPH_NAME: {state.graphName}
  - ITERATION: {state.iterationCount}
  - THREADS: {state.researchThreads}
  - QUESTIONS: {state.context.initialQuestions or thread questions}
```

**Expected Output:**
- New findings added to Loom graph (claims, evidence, sources)
- Findings file written to `{sessionFolder}/findings/`
- Findings file includes `verification` field with entity creation verification results
- researchThreads updated with new findings references

### Research Loop Step: Synthesis

**Spawn research-synthesis agent using Task tool:**
```
Task tool with subagent_type: "research-synthesis"

Arguments:
  - SESSION_FOLDER: {state.sessionFolder}
  - GRAPH_NAME: {state.graphName}
  - ITERATION: {state.iterationCount}
```

**Expected Output:**
- Patterns identified and added to Loom
- Insights created from findings
- Tensions and convergences documented
- Verification summary included in synthesis output

### Research Loop Step: Verification

**This step is performed directly by the orchestrator (not a subagent).** It reads verification data from the research-agent and research-synthesis outputs to confirm that entities were actually created in the Loom graph.

**Actions:**

1. Read the findings file from the current iteration:
   ```
   {sessionFolder}/findings/iteration-{iterationCount}.json
   ```

2. Check for the `verification` field in the findings JSON:
   - `verification.entitiesAttempted` - How many entities the research-agent tried to create
   - `verification.entitiesVerified` - How many were confirmed via read_entity
   - `verification.failedCreations` - Array of entities that failed verification

3. Read the synthesis agent output (from state or Task tool return value):
   - `verification.totalAttempted` - How many synthesis entities were attempted
   - `verification.totalVerified` - How many were confirmed
   - `verification.failedCreations` - Array of failed synthesis entities

4. Evaluate verification results:

```
// Read findings file for research verification
const findingsPath = `${state.sessionFolder}/findings/iteration-${state.iterationCount}.json`;
const findings = JSON.parse(await Read(findingsPath));

const researchVerification = findings.verification || null;
const synthesisVerification = synthesisOutput.verification || null;

// Calculate totals across both agents
const totalAttempted =
  (researchVerification?.entitiesAttempted || 0) +
  (synthesisVerification?.totalAttempted || 0);
const totalVerified =
  (researchVerification?.entitiesVerified || 0) +
  (synthesisVerification?.totalVerified || 0);
const totalFailed =
  (researchVerification?.failedCreations?.length || 0) +
  (synthesisVerification?.failedCreations?.length || 0);

if (totalAttempted > 0 && totalVerified === 0) {
  // ZERO entities verified despite attempts - critical failure
  state.errors.push({
    phase: "research_loop",
    type: "no_entities_verified",
    message: `Iteration ${state.iterationCount}: Agents attempted to create ${totalAttempted} entities but ZERO were verified. Entity creation is likely failing silently. Check loom CLI installation and GRAPH_FOLDER configuration.`,
    timestamp: new Date().toISOString(),
    severity: "critical",
    recoveryAction: "Check loom CLI is available and GRAPH_FOLDER is set"
  });
  state.phaseSummary = `CRITICAL: No entities verified in iteration ${state.iterationCount}. Entity creation may be failing silently.`;
} else if (totalFailed > 0) {
  // Some failures - log warning
  state.errors.push({
    phase: "research_loop",
    type: "partial_verification_failure",
    message: `Iteration ${state.iterationCount}: ${totalFailed} of ${totalAttempted} entity creations failed verification. Verified: ${totalVerified}.`,
    timestamp: new Date().toISOString(),
    severity: "warning",
    recoveryAction: "Review failed entity types and check for pattern in failures"
  });
  state.phaseSummary = `WARNING: ${totalFailed} entity creation failures in iteration ${state.iterationCount}. ${totalVerified}/${totalAttempted} verified.`;
} else if (!researchVerification && !synthesisVerification) {
  // No verification data at all - agents may not have run verification step
  state.errors.push({
    phase: "research_loop",
    type: "missing_verification_data",
    message: `Iteration ${state.iterationCount}: No verification data found in research or synthesis outputs. Agents may not have executed the verification step.`,
    timestamp: new Date().toISOString(),
    severity: "warning",
    recoveryAction: "Ensure agents include verification step in their execution"
  });
  state.phaseSummary = `WARNING: No verification data in iteration ${state.iterationCount}.`;
} else {
  // All good - log success
  state.phaseSummary = `Iteration ${state.iterationCount}: ${totalVerified}/${totalAttempted} entities verified successfully.`;
}

// Write updated state
await Write(statePath, JSON.stringify(state, null, 2));
```

**Important:** This verification step uses only the Read tool (to read findings files) and Write tool (to update state). It does NOT require loom CLI access, keeping the orchestrator's tool list unchanged.

### Research Loop Step: Consolidation

**Spawn research-consolidation agent using Task tool:**
```
Task tool with subagent_type: "research-consolidation"

Arguments:
  - SESSION_FOLDER: {state.sessionFolder}
  - GRAPH_NAME: {state.graphName}
  - ITERATION: {state.iterationCount}
```

**Expected Output:**
- Loom graph analyzed for quality
- Duplicate or low-quality entities flagged
- Graph statistics updated
- Recommendations for next iteration

### Research Loop Step: Expedition (Conditional)

**Activation Condition:** This step executes ONLY when:
- `iterationCount >= 1` (need at least one full cycle of research+synthesis to analyze)
- AND the graph has at least 20 entities (below this, expedition analysis is not meaningful)

For iteration 0 (first pass), this step is skipped -- there is not enough accumulated structure yet.

**Check entity count before spawning:**
```bash
# Get graph stats to check entity count
STATS=$(loom graph-stats '{"graph":"'"${GRAPH_NAME}"'"}')
ENTITY_COUNT=$(echo "$STATS" | jq '.nodeCount')

if [ "$ENTITY_COUNT" -ge 20 ]; then
  # Proceed with expedition
else
  # Skip expedition -- graph too sparse
  # Log: "Expedition skipped: only ${ENTITY_COUNT} entities (need >= 20)"
fi
```

**Spawn research-expedition agent using Task tool:**
```
Task tool with subagent_type: "research-expedition"

Arguments:
  - SESSION_FOLDER: {state.sessionFolder}
  - GRAPH_NAME: {state.graphName}
  - ITERATION: {state.iterationCount}
  - TOPIC: {state.topic}
```

**Expected Output:**
- Expedition findings written to `{sessionFolder}/findings/expedition-iteration-{iterationCount}.json`
- Emergent theories identified (if any)
- Discoveries array with plain-language summaries

**After Expedition:**
Read the expedition findings file. If `emergentTheory.found` is true:
- Log the plain-language summary to `state.phaseSummary`
- The quality agent should factor this into its evaluation
- If the emergent theory reveals gaps, these inform the next iteration's research queries

```typescript
const expeditionPath = `${state.sessionFolder}/findings/expedition-iteration-${state.iterationCount}.json`;
const expeditionFindings = JSON.parse(await Read(expeditionPath));

if (expeditionFindings.emergentTheory?.found) {
  state.phaseSummary += ` | Expedition: ${expeditionFindings.emergentTheory.plainLanguageSummary}`;
}
```

### Research Loop Step: Red Team (Conditional)

**Activation Condition:** This step executes ONLY when:
- `state.questionClassification.enableRedTeam == true` (Type C or D questions)
- AND `iterationCount >= 2` (enough data to challenge)

For Type A and Type B questions, this step is skipped entirely.

**Spawn research-red-team agent using Task tool:**
```
Task tool with subagent_type: "research-red-team"

Arguments:
  - SESSION_FOLDER: {state.sessionFolder}
  - GRAPH_NAME: {state.graphName}
  - ITERATION: {state.iterationCount}
```

**Expected Output:**
- Counter-evidence entities created with `type: counter_evidence` observation
- Contradicts relations linking counter-evidence to challenged claims
- Red team report at `{sessionFolder}/findings/red-team-iteration-{iterationCount}.json`
- Claims that survived challenge have `survived_red_team` observation
- Report includes verification field confirming entity creation

**After Red Team:**
Read the red team report to understand which claims were weakened vs strengthened.
This informs the quality evaluation in the next step.

### Research Loop Step: Checkpoint Aggregation (Conditional)

**Activation Condition:** This step executes ONLY when:
- `state.questionClassification.enableCheckpoint == true` (Type B, C, or D questions)
- AND `iterationCount == 2` (mid-flight correction point)

For Type A questions, this step is skipped entirely.

**This step is performed directly by the orchestrator (not a separate agent).** It analyzes
accumulated research progress and identifies gaps for course correction.

**Checkpoint Aggregation Logic:**

```
CHECKPOINT AGGREGATION (at iteration 2):
  Read all findings files (iterations 0-2)
  Read graph stats via loom CLI

  Analyze:
    overlap = detect diminishing returns (similar findings across iterations)
    gaps = identify areas from research contract not yet addressed
    dead_ends = identify threads that produced no new evidence in last iteration
    contradiction_clusters = group related tensions

  Write checkpoint file:
    {sessionFolder}/checkpoint-aggregation.json
    {
      "iteration": 2,
      "timestamp": ISO,
      "overlap": { "score": 0-1, "details": [...] },
      "gaps": ["gap 1", "gap 2", ...],
      "deadEnds": ["thread X", ...],
      "contradictionClusters": [...],
      "recommendations": [
        "Focus next iteration on <gap>",
        "Abandon <dead end>",
        "Resolve <contradiction cluster>"
      ]
    }

  Update state.checkpointAggregation:
    enabled: true
    lastCheckpointIteration: 2
    checkpointFile: "checkpoint-aggregation.json"

  Feed recommendations into next iteration's research queries:
    For each gap identified, add a targeted query to the next iteration
    For each dead end, mark thread as deprioritized
    For each contradiction cluster, prioritize resolution queries
```

**Note:** The checkpoint file always includes a `gaps` array, even if empty (no gaps detected).
A checkpoint with no gaps still writes a valid file.

### Research Loop Step: Quality

**Spawn research-quality agent using Task tool:**
```
Task tool with subagent_type: "research-quality"

Arguments:
  - SESSION_FOLDER: {state.sessionFolder}
  - GRAPH_NAME: {state.graphName}
  - ITERATION: {state.iterationCount}
```

**Expected Output:**
- quality.lakatosTests evaluated
- quality.flexibilityTests evaluated
- quality.overallScore updated
- continueResearch decision (boolean)
- Quality report written to `{sessionFolder}/quality/`

**Loop Termination Conditions:**
- `continueResearch = false` from quality agent (threshold met)
- `iterationCount >= maxIterations` (hard limit)
- Both conditions trigger transition to documentation phase

**WHEN LOOP EXITS: Set phase to "documentation" and IMMEDIATELY CONTINUE TO PHASE 3.**

---

## Phase 3: Documentation (phase = "documentation")

**Entry State:** `phase = "documentation"`

**Actions:**
1. Read research-state.json
2. Spawn research-documentation agent using Task tool:
   ```
   Task tool with subagent_type: "research-documentation"

   Arguments:
     - SESSION_FOLDER: {state.sessionFolder}
     - GRAPH_NAME: {state.graphName}
     - ARTIFACTS_PATH: {state.sessionFolder}/artifacts
   ```
3. Wait for agent completion
4. Read state to get artifacts list
5. Update state:
   - `phase` -> `"finalize"`
   - `artifacts` -> list of created artifacts
   - `phaseSummary` -> summary of documentation results
   - `metadata.updatedAt` -> current ISO timestamp

**Expected Agent Output:**
- Zettelkasten notes created in `artifacts/zettelkasten/`
- Research document created in `artifacts/research/`
- Journal entry created in `artifacts/journal/`
- artifacts array populated with file references

**IMMEDIATELY CONTINUE TO PHASE 4.**

---

## Phase 4: Finalize (phase = "finalize")

**Entry State:** `phase = "finalize"`

**Actions:**
1. Read research-state.json
2. Write artifact manifest:
   ```json
   // {sessionFolder}/artifact-manifest.json
   {
     "sessionId": "{state.sessionId}",
     "topic": "{state.topic}",
     "artifacts": [
       {
         "type": "zettelkasten",
         "path": "artifacts/zettelkasten/...",
         "format": "zettelkasten"
       },
       // ... other artifacts
     ],
     "graphName": "{state.graphName}",
     "createdAt": "{ISO timestamp}"
   }
   ```
3. Update final state:
   - `phase` -> `"complete"`
   - `phaseSummary` -> final summary
   - `metadata.updatedAt` -> current ISO timestamp
   - `metadata.completedAt` -> current ISO timestamp

**Completion Report:**
After setting phase to "complete", report to the user:
- Session ID
- Topic investigated
- Number of iterations completed
- Artifacts created
- Loom graph name
- Path to artifact manifest

**WORKFLOW COMPLETE.**

---

## Agent Roster

| Agent | Type | Model | Purpose |
|-------|------|-------|---------|
| research-orientation | Agent | sonnet | Clarify intention, gather context |
| research-agent | Agent | opus | Gather findings from web and ingested documents |
| research-synthesis | Agent | opus | Identify patterns, create insights |
| research-consolidation | Agent | sonnet | Analyze and clean Loom graph |
| research-expedition | Agent | opus | Discover emergent theories from graph structure |
| research-red-team | Agent | opus | Adversarial challenge - seek counter-evidence |
| research-quality | Agent | sonnet | Evaluate quality, decide continue/terminate |
| research-documentation | Agent | opus | Create research artifacts |

All agents are located in `.claude/agents/`.

---

## Error Handling (Autonomous Mode)

### Agent Failure Recovery

```
ON AGENT FAILURE:
  1. Log error to state.errors[]:
     {
       "phase": "{current_phase}",
       "agent": "{agent_name}",
       "error": "{error_message}",
       "timestamp": "{ISO timestamp}",
       "recoveryAction": "retry"
     }

  2. RETRY once:
     - Wait briefly
     - Spawn agent again with same arguments

  3. IF retry fails:
     - Update error.recoveryAction to "skipped"
     - Log warning to phaseSummary
     - Continue to next phase (don't block workflow)
```

### State Read/Write Failures

```
ON STATE FILE ERROR:
  - This is CRITICAL - workflow cannot continue
  - Log error message
  - Report failure to user
  - EXIT with error status
```

### Max Iterations Handling

```
IF iterationCount >= maxIterations:
  - Log to phaseSummary: "Max iterations ({maxIterations}) reached"
  - Proceed to documentation phase regardless of quality score
  - This prevents infinite loops
```

### Loom Operation Failures

```
ON LOOM ERROR:
  - Log warning to errors[]
  - Continue workflow (Loom issues are non-blocking)
  - Document limitation in final summary
```

**Only abort if:**
- Cannot create session folder (filesystem issue)
- Cannot write state file
- Topic is empty or invalid

Even on abort, write final state and error log before stopping.

---

## Autonomous Decision Defaults

When facing ambiguity, use these defaults:

| Situation | Default Decision |
|-----------|------------------|
| Graph naming conflict | Append timestamp to graph name |
| Iteration count unclear | Default maxIterations = 5 |
| Quality threshold unclear | Overall score >= 0.7 to exit loop |
| Agent model unclear | Use Sonnet for lightweight, Opus for complex |
| Agent timeout | Wait 5 minutes, then retry once |
| Thread selection | Process all active threads |
| Error severity | Default to "warning" (non-blocking) |
| Missing Loom graph | Create new graph with session name |
| Custom graph specified | Use existing if found, create if not |
| Custom graph name invalid | Sanitize to lowercase alphanumeric with hyphens |

---

## Session Folder Reference

```
DeepResearch/sessions/{SESSION_ID}/
├── research-state.json          # Central state file
│
├── findings/                    # Raw research outputs per thread
│   ├── thread-001-findings.json
│   ├── thread-002-findings.json
│   └── ...
│
├── quality/                     # Quality assessments
│   ├── iteration-001-quality.md
│   ├── iteration-002-quality.md
│   └── ...
│
├── artifacts/                   # Output documents
│   ├── zettelkasten/           # Atomic notes
│   │   ├── 202601271234-concept.md
│   │   └── ...
│   ├── research/               # Long-form documents
│   │   └── final-synthesis.md
│   └── journal/                # Reflective entries
│       └── research-journal.md
│
└── artifact-manifest.json       # Index of artifacts written under this session
```
