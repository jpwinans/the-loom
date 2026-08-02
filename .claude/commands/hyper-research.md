---
description: Meta-orchestrator — extracts questions from context, runs sequential deep-research, synthesizes report
argument-hint: <CONTEXT_DOC> [--topic TOPIC] [--graph GRAPH_NAME] [--output REPORT_PATH]
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task, Skill
model: opus
hooks:
    Stop:
      - hooks:
          - type: command
            command: "node .claude/hooks/check-completion.cjs"
            timeout: 5
---

# HyperResearch Command

Meta-orchestrator that reads a context document, enriches understanding via Loom graph exploration,
extracts critical open research questions, runs sequential `/deep-research` sessions for each,
runs `/loom-expedition` on the accumulated graph, and builds an incremental report.

## Usage

```
/hyper-research CONTEXT_DOC [--topic TOPIC] [--graph GRAPH_NAME] [--output REPORT_PATH]
```

## Arguments

- **`$ARGUMENTS[0]`** (required): Path to the context document (e.g., a loom-expedition report, research synthesis, or any document with open questions)
- **`--topic TOPIC`** (optional): Override topic name. If not provided, derived from context document content.
- **`--graph GRAPH_NAME`** (optional): Loom graph to use. If not provided, creates `hyper-{SESSION_ID}`.
- **`--output REPORT_PATH`** (optional): Custom output path for final report. Default: `DeepResearch/hyper-sessions/{SESSION_ID}/report.md`

## Examples

```bash
# From a loom expedition report
/hyper-research "DeepResearch/expeditions/externalized-rl-2026-02-11.md"

# With explicit topic and graph
/hyper-research "path/to/report.md" --topic "Externalized RL" --graph externalized-rl

# With custom output
/hyper-research "path/to/report.md" --output "Research/my-synthesis.md"
```

---

## CRITICAL CONSTRAINTS

<critical-constraint>
### Autonomous Execution
This is an unattended, fully autonomous workflow. You MUST NOT:
- Ask the user questions (no questions allowed)
- Request clarification or confirmation
- Pause for human input at any point

Instead: Make reasonable assumptions, log decisions to state, continue forward.

### One interface: the CLI
The Loom ships a single JSON-in/JSON-out CLI (`theloom.cli.app:main`, installed as
`loom`). There is no MCP server in this repository, so every phase drives the graph
the same way — `loom <command> '<json>'` over Bash.
Phases 0, 2, and 7 use `create-graph`, `hybrid-search`, `graph-stats`, `detect-loops`,
`list-entities`, and `ingest-document`. Task agents (Phases 4, 5) inherit the same
pattern from the skills they invoke.

### Incremental Report
The report file is created in Phase 0 and sections are appended after each phase.
The report is readable at any point; partial success yields a partial report.
</critical-constraint>

---

## MAIN EXECUTION LOOP

<critical>
**YOU MUST EXECUTE THIS ENTIRE WORKFLOW FROM START TO FINISH WITHOUT STOPPING.**

After completing Phase 0 initialization, immediately continue executing phases until phase = "complete":

```
LOOP:
  1. Read hyper-research-state.json to get current phase
  2. Execute the appropriate phase handler (see sections below)
  3. After phase completes, read hyper-research-state.json again
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
- Append to the report after each phase
</critical>

---

## State Machine Overview

```
Phase 0: Initialize (this command)
  - Parse args, create session folder, create/use graph, write state, create blank report
  |
  v
Phase 1: Comprehension (phase = "comprehension")
  - Read context doc, extract claims/evidence/conclusions/tensions/open questions
  - Derive topic if not provided
  - Write first report section
  |
  v
Phase 2: Graph Exploration (phase = "graph_exploration")
  - Query Loom graph via the loom CLI
  - Amend understanding with existing graph knowledge
  - Identify gaps between context doc and graph
  - Append to report
  |
  v
Phase 3: Question Extraction (phase = "question_extraction")
  - Synthesize 1-5 research questions from understanding + gaps
  - Rank by impact
  - Append to report
  - If 0 questions -> skip to Phase 5
  |
  v
Phase 4: Deep Research (phase = "deep_research")
  - For each question sequentially:
    - Spawn Task agent -> agent invokes Skill(deep-research ...) -> wait
    - Read results from DeepResearch/sessions/{dr-session-id}/research-state.json
    - Append findings to report
  - Partial success: failed questions logged and skipped
  |
  v
Phase 5: Expedition (phase = "expedition")
  - Spawn Task agent -> agent invokes Skill(loom-expedition ...) -> wait
  - Read expedition report -> append to report
  |
  v
Phase 5.5: Absence Analysis (phase = "absence_analysis")
  - Run creativity-loop with purpose={topic} to find analogies
  - Analyze transfer gaps — what structures exist in other graphs but are absent here?
  - Append "What's Missing" section to report
  |
  v
Phase 6: Synthesis (phase = "synthesis")
  - Read all accumulated results
  - Write cross-cutting themes + "what remains open" sections
  |
  v
Phase 7: Ingestion (phase = "ingestion")
  - ingest-document (category: "hyper-research")
  - Mark complete
  - phase -> "complete"
```

---

## Phase 0: Initialize

**Parse arguments:**

```
CONTEXT_DOC = $ARGUMENTS[0]   # Required: path to context document

# Parse optional flags
TOPIC = ""
GRAPH_NAME = ""
OUTPUT_PATH = ""

for each argument pair:
  --topic -> TOPIC
  --graph -> GRAPH_NAME
  --output -> OUTPUT_PATH
```

**Validate context document exists:**
Read the context document. If it doesn't exist, log error and abort.

**Generate Session ID:**

Format: `hyper-YYYY-MM-DD-{slug}-NNN`

Where:
- `YYYY-MM-DD` is the current date
- `{slug}` is derived from TOPIC (if provided) or context doc filename (max 30 chars)
- `NNN` is a 3-digit counter (001, 002, etc.)

Example: `hyper-2026-02-12-externalized-rl-001`

**Create Session Folder Structure:**

```
DeepResearch/hyper-sessions/{SESSION_ID}/
├── hyper-research-state.json    # Central state file
└── report.md                    # Incremental report, readable at any phase
```

The report is written incrementally from Phase 0 onward, so a partial run still
yields a readable report.

```bash
SESSION_BASE="DeepResearch/hyper-sessions"
mkdir -p "${SESSION_BASE}/${SESSION_ID}"
```

**Determine Graph Name:**

```
if GRAPH_NAME provided:
  Use GRAPH_NAME (create if doesn't exist)
else:
  GRAPH_NAME = "hyper-${SESSION_ID}"
  Create new graph
```

**Create or verify Loom graph:**

Create the graph with `loom create-graph '{"name": "{graphName}"}'`.

If the graph already exists (e.g., custom graph), that's fine — we add to it.

**Determine report output path:**

```
if OUTPUT_PATH provided:
  REPORT_PATH = OUTPUT_PATH
else:
  REPORT_PATH = "${SESSION_FOLDER}/report.md"
```

**Copy state template and initialize:**

```bash
cp .claude/harness-templates/hyper-research-state.template.json \
   "${SESSION_FOLDER}/hyper-research-state.json"
```

Update state fields:
- `sessionId` -> `${SESSION_ID}`
- `contextDocPath` -> `${CONTEXT_DOC}`
- `topic` -> `${TOPIC}` (may be empty — derived in Phase 1)
- `graphName` -> `${GRAPH_NAME}`
- `phase` -> `"comprehension"`
- `reportPath` -> `${REPORT_PATH}`
- `sessionFolder` -> `${SESSION_FOLDER}`
- `metadata.createdAt` -> current ISO timestamp
- `metadata.updatedAt` -> current ISO timestamp

**Create blank report:**

Write initial report skeleton to `REPORT_PATH`:

```markdown
# Hyper-Research Report: {TOPIC or "Pending"}
*Generated: {date} | Graph: {GRAPH_NAME} | Context: {CONTEXT_DOC}*

---

```

**After initialization:** phase is "comprehension". **IMMEDIATELY CONTINUE TO PHASE 1.**

---

## Phase 1: Comprehension (phase = "comprehension")

**Entry State:** `phase = "comprehension"`

**Purpose:** Read and deeply understand the context document. Extract structured understanding.

**Actions:**

1. Read `hyper-research-state.json`
2. Read the context document at `state.contextDocPath`
3. Analyze the document and extract:

   - **Claims**: Key assertions made in the document
   - **Evidence**: Data, examples, or references supporting claims
   - **Conclusions**: Final positions or recommendations
   - **Open Questions**: Explicitly stated or implied questions that remain unresolved
   - **Tensions**: Contradictions, competing frameworks, or unresolved debates

4. If `state.topic` is empty, derive a concise topic from the document content

5. Update state:
   - `understanding.claims` -> array of claim strings
   - `understanding.evidence` -> array of evidence strings
   - `understanding.conclusions` -> array of conclusion strings
   - `understanding.openQuestions` -> array of question strings
   - `understanding.tensions` -> array of tension strings
   - `topic` -> derived topic (if was empty)
   - `phase` -> `"graph_exploration"`
   - `phaseSummary` -> brief summary of comprehension results
   - `metadata.updatedAt` -> current ISO timestamp

6. Update report title if topic was derived

7. Append to report:

```markdown
## Current Understanding

### Claims
{bulleted list of claims}

### Evidence
{bulleted list of evidence}

### Conclusions
{bulleted list of conclusions}

### Open Questions
{bulleted list of open questions}

### Tensions
{bulleted list of tensions}

---

```

**IMMEDIATELY CONTINUE TO PHASE 2.**

---

## Phase 2: Graph Exploration (phase = "graph_exploration")

**Entry State:** `phase = "graph_exploration"`

**Purpose:** Query the Loom graph to find existing knowledge relevant to the context document.
Amend understanding with what the graph already knows. Identify gaps.

**Actions:**

1. Read `hyper-research-state.json`

2. Query the graph with the loom CLI:

   a. **Graph stats** — `loom graph-stats '{"graph": "{graphName}"}'` to understand graph size and structure

   b. **Hybrid search** — `loom hybrid-search '{"query": "<search text>", "graph": "{graphName}"}'` with queries derived from:
      - The topic
      - Key claims from comprehension phase
      - Key tensions identified
      Run 2-4 searches depending on topic complexity.

   c. **Loop detection** — `loom detect-loops '{"graph": "{graphName}"}'` to find feedback structures

   d. **Entity listing** — `loom list-entities '{"entityType": "claim", "graph": "{graphName}"}'` with relevant type filters for key entity types

4. Analyze results:
   - **Existing knowledge**: What the graph already knows that's relevant
   - **Gaps**: What the context document discusses that the graph doesn't cover
   - **Amendments**: Ways the context doc's understanding should be updated based on graph knowledge

5. Update state:
   - `graphExploration.existingKnowledge` -> array of relevant existing entities/findings
   - `graphExploration.gaps` -> array of identified gaps
   - `graphExploration.amendments` -> array of understanding amendments
   - `phase` -> `"question_extraction"`
   - `phaseSummary` -> summary of graph exploration
   - `metadata.updatedAt` -> current ISO timestamp

6. Append to report:

```markdown
## Graph Exploration

### Existing Knowledge
{summary of what the graph already knows}

### Gaps Identified
{bulleted list of gaps between context doc and graph}

### Understanding Amendments
{bulleted list of how understanding was updated based on graph knowledge}

---

```

**IMMEDIATELY CONTINUE TO PHASE 3.**

---

## Phase 3: Question Extraction (phase = "question_extraction")

**Entry State:** `phase = "question_extraction"`

**Purpose:** Synthesize the most impactful research questions from the combined understanding
(context doc + graph exploration). These questions will drive the deep-research sessions.

**Actions:**

1. Read `hyper-research-state.json`

2. Synthesize questions considering:
   - Open questions from the context document
   - Gaps identified during graph exploration
   - Tensions that need resolution
   - Areas where evidence is weak or contradictory
   - High-impact unknowns that would change conclusions if answered

3. Generate 1-5 research questions:
   - Each question should be specific enough for a focused deep-research session
   - Each question should be answerable through web research + graph analysis
   - Rank by expected impact on the overall understanding
   - Questions should be complementary, not overlapping

4. If 0 questions are generated (understanding is complete):
   - Set `phase` -> `"expedition"` (skip Phase 4)
   - Log reason in phaseSummary

5. Otherwise update state:
   - `researchQuestions` -> array of question objects: `[{question: "", rationale: "", priority: 1-5}]`
   - `totalQuestions` -> `researchQuestions.length`
   - `phase` -> `"deep_research"`
   - `phaseSummary` -> summary of extracted questions
   - `metadata.updatedAt` -> current ISO timestamp

6. Append to report:

```markdown
## Research Questions

{numbered list of questions with rationale}

1. **{question}**
   *Rationale: {why this question matters}*

2. **{question}**
   *Rationale: {why this question matters}*

...

---

## Research Findings

```

**IMMEDIATELY CONTINUE TO PHASE 4 (or Phase 5 if 0 questions).**

---

## Phase 4: Deep Research (phase = "deep_research")

**Entry State:** `phase = "deep_research"`

**Purpose:** Run sequential `/deep-research` sessions for each extracted question.
Each session invokes deep-research as a Skill (not a subagent), so deep-research runs in the
main conversation context and CAN spawn its own agents (research-agent, research-synthesis, etc.).

**Why Skill instead of Task:** Subagents cannot spawn other subagents. deep-research needs to
spawn its own agents (research-agent, research-synthesis, etc.). By invoking deep-research as a
Skill from the main conversation, it runs with full agent-spawning capability.

**Loop Structure:**

```
FOR i = state.currentQuestionIndex TO state.researchQuestions.length - 1:
  1. Get question = state.researchQuestions[i]
  2. Invoke deep-research as a Skill (runs in main conversation context)
  3. Wait for completion
  4. Find the session folder in DeepResearch/sessions/
  5. Read DeepResearch/sessions/{SESSION_ID}/research-state.json
  6. Append results to report
  7. Update state: currentQuestionIndex++, completedQuestions++ (or failedQuestions++)
  8. Write state after each question (enables resume)
```

**Skill Invocation:**

For each question, invoke deep-research directly as a Skill:

```
Skill("deep-research", "{question.question}" --graph {state.graphName})
```

This runs deep-research in the main conversation context, allowing it to spawn its own
Task agents (research-agent, research-synthesis, research-consolidation, etc.) without
hitting the subagent nesting limitation.

**After each Skill invocation completes:**

1. Find the most recent session folder in `DeepResearch/sessions/` (ls -t, take first)
2. Read `DeepResearch/sessions/{SESSION_ID}/research-state.json` to verify:
   - `phase` == "complete"
   - `quality.overallScore`
   - `iterationCount`
   - `artifacts` array
3. If verification succeeds: `completedQuestions++`
4. If verification fails (phase != "complete", state not found):
   - `failedQuestions++`
   - Log error to `state.errors[]`
   - Continue to next question (don't abort)

5. Append to report:

```markdown
### Question {i+1}: {question.question}

**Session:** {SESSION_ID}
**Status:** {complete|failed}
**Quality Score:** {score}/10
**Iterations:** {count}

**Key Findings:**
{summary of findings from the session — read from artifacts or state}

```

6. Clean up stale deep-research state files from the sub-session to prevent interference
   with the hyper-research check-completion hook. The hook checks for `wip-*` directories
   and `state.json` files; stale deep-research state files from sub-sessions could
   cause the hook to block hyper-research from advancing between questions.

   ```bash
   # Clean up deep-research session state files that could interfere with
   # the check-completion hook. Deep-research creates state in
   # DeepResearch/sessions/{SESSION_ID}/research-state.json — the hook
   # could detect these as active sessions and block hyper-research.
   # The session data has already been captured above.
   ```

7. Update state after each question:
   - `currentQuestionIndex` -> i + 1
   - `completedQuestions` or `failedQuestions` incremented
   - Append to `deepResearchSessions`: `{questionIndex: i, sessionId: SESSION_ID, status: "complete"|"failed", qualityScore: score}`
   - `metadata.updatedAt` -> current ISO timestamp
   - Write state (enables resume if interrupted — `deepResearchSessions` preserves the session mapping)

**After all questions processed:**

Update state:
- `phase` -> `"expedition"`
- `phaseSummary` -> "{completedQuestions}/{totalQuestions} completed, {failedQuestions} failed"
- `metadata.updatedAt` -> current ISO timestamp

**IMMEDIATELY CONTINUE TO PHASE 5.**

---

## Phase 5: Expedition (phase = "expedition")

**Entry State:** `phase = "expedition"`

**Purpose:** Run a Loom expedition on the accumulated graph to discover emergent theories
and cross-cutting patterns across all the deep-research sessions.

**Actions:**

1. Read `hyper-research-state.json`

2. Invoke loom-expedition as a Skill (runs in main conversation context):

```
Skill("loom-expedition", "{state.graphName}" --seed "{state.topic}" --session-folder "{state.sessionFolder}")
```

This runs the expedition in the main conversation context. The loom-expedition command
performs graph reconnaissance, thread selection, influence mapping, path analysis, and
writes an expedition report.

3. Wait for completion

4. Find the expedition report in the session folder or parse output for report path

5. Read the expedition report

6. Update state:
   - `expeditionReport` -> path to expedition report (or summary)
   - `phase` -> `"absence_analysis"`
   - `phaseSummary` -> summary of expedition results
   - `metadata.updatedAt` -> current ISO timestamp

7. Append to report:

```markdown
---

## Expedition Synthesis

{expedition report content or key findings summary}

```

**Error handling:** If expedition fails, log error, set `expeditionReport` to null, continue to absence analysis.

**IMMEDIATELY CONTINUE TO PHASE 5.5.**

---

## Phase 5.5: Absence Analysis (phase = "absence_analysis")

**Entry State:** `phase = "absence_analysis"`

**Purpose:** Use the analogy engine's absence surprise scoring to identify what the research
*should* have found but didn't. Runs a purpose-directed creativity loop cycle on the research
graph to discover structural patterns present in other graphs that are absent from this research.

**Actions:**

1. Read `hyper-research-state.json`

2. Check if the research graph has enough structure for meaningful analysis:

   ```
   loom graph-stats '{"graph": "{state.graphName}"}'
   ```

   If fewer than 15 entities, skip this phase (set phase -> "synthesis", append note to report).

3. Run a purpose-directed creativity loop on the research graph:

   ```
   loom creativity-loop '{"graph": "{state.graphName}", "purpose": "{state.topic}", "maxCycles": 2, "acceptanceThreshold": 0.3, "detectPlateau": true}'
   ```

   This leverages TL-284's purpose parameter (MCT pragmatic bias) and absence surprise scoring
   to find analogies relevant to the research topic. The creativity loop will:
   - Explore frontier regions of the research graph
   - Retrieve far analogies from other loaded graphs (via proactive triggers or retrieval)
   - Score proposals with the 4-signal formula (including absence surprise)
   - Return proposals that represent knowledge the research graph is missing

4. Analyze the creativity loop results:
   - Extract proposals with high absence surprise scores
   - Identify structural patterns from other graphs that have no counterpart in the research
   - Note which domains the missing structures come from

5. Update state:
   - `absenceAnalysis.proposalsFound` -> number of proposals
   - `absenceAnalysis.highAbsenceProposals` -> proposals with absenceSurprise > 0.5
   - `absenceAnalysis.missingPatterns` -> plain-language descriptions of what's absent
   - `phase` -> `"synthesis"`
   - `phaseSummary` -> summary of absence analysis
   - `metadata.updatedAt` -> current ISO timestamp

6. Append to report:

```markdown
---

## What's Missing: Absence Analysis

{If proposals found:}

The analogy engine identified structural patterns present in other knowledge domains
that are absent from this research:

{bulleted list of missing patterns with their absence surprise scores}

**Domains with relevant absent structure:**
{list of source graphs/domains where the missing patterns exist}

**Implications for further research:**
{brief analysis of what these absences suggest about gaps in the current investigation}

{If no proposals found:}

No significant structural absences detected. The research graph's coverage appears
comprehensive relative to other available knowledge domains.

```

**Error handling:** If creativity loop fails or returns no results, log warning, note "absence
analysis inconclusive" in report, continue to synthesis.

**IMMEDIATELY CONTINUE TO PHASE 6.**

---

## Phase 6: Synthesis (phase = "synthesis")

**Entry State:** `phase = "synthesis"`

**Purpose:** Read all accumulated results and write cross-cutting themes and remaining open questions.
This is the orchestrator's own synthesis — connecting threads across all deep-research sessions
and the expedition.

**Actions:**

1. Read `hyper-research-state.json`

2. Read the report built so far

3. Synthesize across all phases:

   a. **Cross-Cutting Themes**: Patterns or insights that appear across multiple research questions.
      Look for:
      - Concepts that recur across sessions
      - Causal chains that span multiple questions
      - Convergent evidence from different angles
      - Structural isomorphisms between domains explored

   b. **What Remains Open**: Questions or uncertainties that persist even after research.
      Include:
      - Questions from Phase 1 that weren't fully resolved
      - New questions that emerged during research
      - Areas where evidence remains contradictory
      - Suggested next steps for future research

4. Update state:
   - `phase` -> `"ingestion"`
   - `phaseSummary` -> synthesis summary
   - `metadata.updatedAt` -> current ISO timestamp

5. Append to report:

```markdown
---

## Cross-Cutting Themes

{numbered list of themes with supporting evidence from across sessions}

## What Remains Open

{bulleted list of unresolved questions and suggested next steps}

---

*Hyper-Research session: {sessionId} | {completedQuestions}/{totalQuestions} questions completed | Graph: {graphName}*
```

**IMMEDIATELY CONTINUE TO PHASE 7.**

---

## Phase 7: Ingestion (phase = "ingestion")

**Entry State:** `phase = "ingestion"`

**Purpose:** Ingest the final report into the Loom as a document and mark the session complete.

**Actions:**

1. Read `hyper-research-state.json`

2. Ingest the report:
   ```
   loom ingest-document '{"file_path": "{state.reportPath}", "category": "hyper-research"}'
   ```

3. Update state:
   - `phase` -> `"complete"`
   - `phaseSummary` -> "Session complete. Report at: {reportPath}"
   - `metadata.updatedAt` -> current ISO timestamp
   - `metadata.completedAt` -> current ISO timestamp

4. Write final state

**Completion Report:**

After setting phase to "complete", report to the user:

```
Hyper-Research Complete
=======================
Session:    {sessionId}
Topic:      {topic}
Graph:      {graphName}
Questions:  {completedQuestions}/{totalQuestions} completed ({failedQuestions} failed)
Report:     {reportPath}

Phase Summary:
- Comprehension: {claims} claims, {questions} open questions, {tensions} tensions
- Graph Exploration: {existing} existing entities, {gaps} gaps identified
- Research Questions: {totalQuestions} questions extracted
- Deep Research: {completedQuestions} sessions completed
- Expedition: {expedition status}
- Synthesis: {themes} cross-cutting themes, {remaining} open questions
```

**WORKFLOW COMPLETE.**

---

## Error Handling (Autonomous Mode)

### Deep Research Session Failure

```
ON DEEP-RESEARCH FAILURE:
  1. Log error to state.errors[]:
     {
       "phase": "deep_research",
       "question": "{question text}",
       "questionIndex": {index},
       "error": "{error message}",
       "timestamp": "{ISO timestamp}",
       "recoveryAction": "skipped"
     }

  2. Increment failedQuestions
  3. Continue to next question (don't abort entire workflow)
  4. Note failure in report under the question's section
```

### Expedition Failure

```
ON EXPEDITION FAILURE:
  1. Log error to state.errors[]
  2. Set expeditionReport to null
  3. Continue to synthesis (work with what we have)
  4. Note in report: "Expedition could not be completed"
```

### Loom CLI Failure

```
ON LOOM CLI FAILURE:
  1. Log warning to state.errors[]
  2. Skip the specific loom operation
  3. Continue workflow (loom issues are non-blocking for most phases)
  4. For Phase 2 (graph exploration): degrade gracefully, work with whatever data available
  5. For Phase 7 (ingestion): log warning, mark complete anyway (report file exists)
```

### State File Failure

```
ON STATE FILE ERROR:
  - This is CRITICAL - workflow cannot continue
  - Log error message
  - Report failure to user
  - EXIT with error status
```

### Resume Capability

The workflow can be resumed from any phase. To resume:
1. Read `hyper-research-state.json` from the session folder
2. Check `phase` field
3. Resume from that phase

For Phase 4, the `currentQuestionIndex` tracks which question to resume from.

---

## Autonomous Decision Defaults

When facing ambiguity, use these defaults:

| Situation | Default Decision |
|-----------|------------------|
| Topic unclear from context doc | Use first heading or filename as topic |
| Graph doesn't exist | Create new graph with session name |
| Context doc too short | Extract what's available, note limitation |
| No open questions found | Generate questions from gaps and tensions |
| Deep-research session hangs | Task agent has its own timeout handling |
| Expedition finds nothing | Note "no emergent theories" in report |
| Report path conflict | Append counter to filename |
| `loom` not on PATH | Run via `uv run loom` from the repo root |
| 0 questions extracted | Skip to expedition (Phase 5) |
| All questions fail | Continue to expedition with whatever graph has |

---

## Session Folder Reference

```
DeepResearch/hyper-sessions/{SESSION_ID}/
└── hyper-research-state.json    # Central state file

# Deep-research sub-sessions live in their standard location:
DeepResearch/sessions/{DR-SESSION-ID}/
├── research-state.json
├── findings/
├── quality/
└── artifacts/

# Incremental report written directly into the session folder:
DeepResearch/hyper-sessions/{SESSION_ID}/report.md
```
