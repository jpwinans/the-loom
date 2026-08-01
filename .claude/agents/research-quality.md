---
name: research-quality
description: Evaluate research quality using Lakatos and flexibility tests, determine continue or terminate decision
tools: Read, Write, Bash
model: sonnet
---

# Research Quality Agent

Evaluate the quality of research using scientific criteria including Lakatos progressiveness tests and cognitive flexibility tests. Determine whether to continue the research loop or conclude the session.

## Input Parameters

The agent receives these parameters from the orchestrator:

| Parameter | Description |
|-----------|-------------|
| **SESSION_FOLDER** | Path to the session folder |
| **GRAPH_NAME** | Name of the Loom graph for this session |
| **ITERATION** | Current iteration number (0-indexed) |
| **MAX_ITERATIONS** | Maximum iterations allowed for the session |

---

## Purpose

The quality agent performs research evaluation:

1. **Lakatos tests** - Evaluate scientific progressiveness
2. **Flexibility tests** - Assess cognitive openness
3. **Overall scoring** - Calculate composite quality score
4. **Decision logic** - Determine CONTINUE or TERMINATE
5. **Quality report** - Document detailed assessment

---

## Quality Framework

### Lakatos Progressiveness Tests

Based on Imre Lakatos's philosophy of science, a research program is **progressive** if it:
- Generates novel predictions
- Has predictions corroborated by evidence
- Expands understanding to new domains

A research program is **degenerative** if it:
- Only explains existing observations
- Fails to predict new phenomena
- Contracts rather than expands scope

### Flexibility Tests

Cognitive flexibility indicators:
- **Paradigm Flexibility**: Ability to consider alternative frameworks
- **Integration**: Synthesis of diverse sources
- **Self-Correction**: Recognition and correction of errors

---

## Execution Steps

### Step 1: Read Current State and History

```typescript
// Read research state
const statePath = "${SESSION_FOLDER}/research-state.json";
const state = JSON.parse(await Read(statePath));

// Read previous quality reports if any
const previousReports = [];
for (let i = 0; i < ${ITERATION}; i++) {
  try {
    const reportPath = `${SESSION_FOLDER}/quality/iteration-${i}-quality.json`;
    const report = JSON.parse(await Read(reportPath));
    previousReports.push(report);
  } catch {
    // No previous report
  }
}
```

### Step 2: Gather Graph Metrics

```bash
# Get graph statistics via loom CLI
STATS=$(loom graph-stats '{"graph":"'"${GRAPH_NAME}"'"}')

# Get entity counts by type
INSIGHTS=$(loom list-entities '{"entityType":"insight","graph":"'"${GRAPH_NAME}"'"}')

PATTERNS=$(loom list-entities '{"entityType":"pattern","graph":"'"${GRAPH_NAME}"'"}')

QUESTIONS=$(loom list-entities '{"entityType":"question","graph":"'"${GRAPH_NAME}"'"}')

TENSIONS=$(loom list-entities '{"entityType":"tension","graph":"'"${GRAPH_NAME}"'"}')

SOURCES=$(loom list-entities '{"entityType":"source","graph":"'"${GRAPH_NAME}"'"}')
```

### Step 3: Evaluate Lakatos Tests

#### Novel Predictions Test (0-10)

Does the research generate testable predictions?

```typescript
// Count new questions generated (predictions to test)
const newQuestionsThisIteration = questions.filter(
  q => q.observations.some(o => o.includes(`iteration: ${ITERATION}`))
);

// Score based on question generation
// 0: No new questions
// 5: Some new questions
// 10: Rich set of testable predictions
const novelPredictionsScore = Math.min(10, newQuestionsThisIteration.length * 2);

// Qualitative assessment
const novelPredictionsAssessment = novelPredictionsScore >= 7
  ? "Research is generating novel testable predictions"
  : novelPredictionsScore >= 4
    ? "Some predictions emerging, could be more specific"
    : "Few novel predictions; research may be degenerative";
```

#### Corroboration Test (0-10)

Are predictions supported by evidence?

```bash
# Count claims with supporting evidence
CLAIMS=$(loom list-entities '{"entityType":"claim","graph":"'"${GRAPH_NAME}"'"}')

# For each claim, check for supporting relations
SUPPORTS=$(loom list-relations '{"targetId":"'"${CLAIM_ID}"'","relationType":"supports","graph":"'"${GRAPH_NAME}"'"}')
```

```typescript
let corroboratedClaims = 0;
// Count claims with at least one support relation
// Score based on corroboration ratio
const corroborationRatio = claims.length > 0
  ? corroboratedClaims / claims.length
  : 0;
const corroborationScore = Math.round(corroborationRatio * 10);

const corroborationAssessment = corroborationScore >= 7
  ? "Strong evidentiary support for claims"
  : corroborationScore >= 4
    ? "Moderate support; some claims need more evidence"
    : "Weak evidentiary base; more research needed";
```

#### Expanding Scope Test (0-10)

Does understanding extend to new domains?

```bash
# Count unique domains/concepts covered
CONCEPTS=$(loom list-entities '{"entityType":"concept","graph":"'"${GRAPH_NAME}"'"}')
```

```typescript
// Look for domain diversity in observations
const domains = new Set();
for (const entity of [...concepts, ...insights, ...patterns]) {
  for (const obs of entity.observations) {
    if (obs.startsWith("domain:")) {
      domains.add(obs.split(":")[1].trim());
    }
  }
}

// Compare to previous iteration
const previousDomainCount = previousReports.length > 0
  ? previousReports[previousReports.length - 1].domainCount || 0
  : 0;

const domainGrowth = domains.size - previousDomainCount;

// Score based on scope expansion
// Positive growth is progressive, stagnation is concerning
const expandingScopeScore = Math.min(10, Math.max(0,
  domains.size >= 5 ? 7 : domains.size * 1.4 +
  (domainGrowth > 0 ? 3 : domainGrowth === 0 ? 0 : -2)
));

const expandingScopeAssessment = expandingScopeScore >= 7
  ? "Research is expanding to new domains"
  : expandingScopeScore >= 4
    ? "Moderate scope; consider exploring adjacent areas"
    : "Narrow scope; research may be contracting";
```

### Step 4: Evaluate Flexibility Tests

#### Paradigm Flexibility Test (0-10)

Can the researcher consider alternative frameworks?

```typescript
// Look for tensions (indicate multiple perspectives)
const unresolvedTensions = tensions.filter(
  t => !t.observations.some(o => o.includes("resolution_status: resolved"))
);

// Look for questions that challenge assumptions
const challengingQuestions = questions.filter(
  q => q.observations.some(o =>
    o.toLowerCase().includes("alternative") ||
    o.toLowerCase().includes("assumption") ||
    o.toLowerCase().includes("challenge")
  )
);

// Score based on paradigm exploration
const paradigmFlexibilityScore = Math.min(10,
  unresolvedTensions.length * 2 +
  challengingQuestions.length * 1.5 +
  (tensions.length > 0 ? 3 : 0)
);

const paradigmFlexibilityAssessment = paradigmFlexibilityScore >= 7
  ? "Actively exploring alternative frameworks"
  : paradigmFlexibilityScore >= 4
    ? "Some paradigm flexibility; could explore more alternatives"
    : "May be locked into single paradigm; consider alternatives";
```

#### Integration Test (0-10)

Are diverse sources being synthesized?

```bash
# Count convergences (indicate synthesis)
CONVERGENCES=$(loom list-entities '{"entityType":"convergence","graph":"'"${GRAPH_NAME}"'"}')
```

```typescript
// Count unique source types
const sourceTypes = new Set();
for (const source of sources) {
  for (const obs of source.observations) {
    if (obs.startsWith("type:")) {
      sourceTypes.add(obs.split(":")[1].trim());
    }
  }
}

// Score based on source diversity and synthesis
const integrationScore = Math.min(10,
  sourceTypes.size * 2 +
  convergences.length * 2 +
  (patterns.length > 0 ? 2 : 0)
);

const integrationAssessment = integrationScore >= 7
  ? "Strong integration of diverse sources"
  : integrationScore >= 4
    ? "Moderate integration; consider more diverse sources"
    : "Limited integration; research is siloed";
```

#### Self-Correction Test (0-10)

Are errors being identified and corrected?

```bash
# Look for superseded entities (corrections)
SUPERSEDED=$(loom list-entities '{"graph":"'"${GRAPH_NAME}"'","includeSuperseded":true}')
```

```typescript
// Look for entities with updated confidence
const confidenceAdjustments = [];
for (const claim of claims) {
  const adjustments = claim.observations.filter(
    o => o.includes("confidence_adjusted")
  );
  if (adjustments.length > 0) {
    confidenceAdjustments.push(claim);
  }
}

// Score based on self-correction activity
const selfCorrectionScore = Math.min(10,
  confidenceAdjustments.length * 2 +
  (supersededEntities.length > 0 ? 3 : 0) +
  (unresolvedTensions.length > 0 ? 2 : 0)
);

const selfCorrectionAssessment = selfCorrectionScore >= 7
  ? "Active self-correction and refinement"
  : selfCorrectionScore >= 4
    ? "Some self-correction; could be more critical"
    : "Limited self-correction; may be accepting claims uncritically";
```

### Step 5: Evaluate Scope Coverage (Research Contract)

Evaluate how well the research covers the scope defined in the research contract. This dimension connects the quality evaluation to the contract formed during orientation.

#### Scope Coverage Test (0-10)

Are the included scope items being addressed?

```typescript
// Read the research contract from state (backward compatible: handle empty/missing contract)
const researchContract = state.researchContract || { coreQuestion: '', scope: { included: [], excluded: [] }, successCriteria: [] };
const scopeIncluded = researchContract.scope?.included || [];
const scopeExcluded = researchContract.scope?.excluded || [];

let scopeCoverageScore = 10; // Default to full score if no contract defined (backward compatibility)
let scopeCoverageAssessment = "No research contract defined; scope coverage not evaluated";
let driftWarnings: string[] = [];

if (scopeIncluded.length > 0) {
  // For each included scope item, check if any entities in the graph relate to it
  let coveredItems = 0;
  for (const item of scopeIncluded) {
    // Search for entities with observations or names that relate to the scope item
    const relatedEntities = allEntities.filter(e =>
      e.name.toLowerCase().includes(item.toLowerCase()) ||
      e.observations.some(o => o.toLowerCase().includes(item.toLowerCase()))
    );
    if (relatedEntities.length > 0) {
      coveredItems++;
    }
  }

  const coverageRatio = coveredItems / scopeIncluded.length;
  scopeCoverageScore = Math.round(coverageRatio * 10);

  scopeCoverageAssessment = scopeCoverageScore >= 7
    ? "Strong scope coverage; most included areas are addressed"
    : scopeCoverageScore >= 4
      ? "Moderate scope coverage; some included areas need more attention"
      : "Low scope coverage; many included areas lack research";
}

// Drift detection: check if research entities relate to excluded scope items
if (scopeExcluded.length > 0) {
  for (const excludedItem of scopeExcluded) {
    const driftEntities = allEntities.filter(e =>
      e.name.toLowerCase().includes(excludedItem.toLowerCase()) ||
      e.observations.some(o => o.toLowerCase().includes(excludedItem.toLowerCase()))
    );
    if (driftEntities.length > 0) {
      driftWarnings.push(`Drift detected: research touches excluded scope item "${excludedItem}" (${driftEntities.length} entities)`);
    }
  }
}
```

### Step 6: Evaluate Hypothesis Coverage

Assess how well hypotheses are being tested and resolved across the research session.

#### Hypothesis Coverage Test (0-10)

Are hypotheses being tested? How many have been resolved?

```bash
# Get hypotheses from the graph
HYPOTHESES=$(loom list-entities '{"entityType":"hypothesis","graph":"'"${GRAPH_NAME}"'"}')
```

```typescript
// Read hypothesis state (backward compatible: handle missing)
const hypothesesState = state.hypotheses || { items: [] };
const hypothesisEntities = hypotheses || [];

// Count resolved hypotheses (those with resolved/confirmed/rejected observation)
const resolvedHypotheses = hypothesisEntities.filter(h =>
  h.observations.some(o =>
    o.includes('status: resolved') ||
    o.includes('status: confirmed') ||
    o.includes('status: disconfirmed')
  )
);

// Score based on hypothesis testing progress
let hypothesisCoverageScore = 10; // Default if no hypotheses expected
let hypothesisCoverageAssessment = "No hypotheses tracked; hypothesis coverage not evaluated";

if (hypothesisEntities.length > 0) {
  const resolvedRatio = resolvedHypotheses.length / hypothesisEntities.length;
  // Also check if hypotheses have associated evidence
  const testedHypotheses = hypothesisEntities.filter(h =>
    h.observations.some(o => o.includes('evidence') || o.includes('tested'))
  );
  const testedRatio = testedHypotheses.length / hypothesisEntities.length;

  hypothesisCoverageScore = Math.round(
    (resolvedRatio * 5 + testedRatio * 5)
  );
  hypothesisCoverageScore = Math.min(10, Math.max(0, hypothesisCoverageScore));

  hypothesisCoverageAssessment = hypothesisCoverageScore >= 7
    ? "Strong hypothesis coverage; most hypotheses tested and many resolved"
    : hypothesisCoverageScore >= 4
      ? "Moderate hypothesis coverage; some hypotheses need more testing"
      : "Low hypothesis coverage; hypotheses are not being systematically tested";
}
```

### Step 7: Evaluate Independence Score

Assess what fraction of convergences have multiple independent source groups.

#### Independence Score Test (0-10)

What fraction of convergences have multiple independent source groups?

```bash
# Get convergences
CONVERGENCES=$(loom list-entities '{"entityType":"convergence","graph":"'"${GRAPH_NAME}"'"}')

# Get sources for independence group analysis
SOURCES=$(loom list-entities '{"entityType":"source","graph":"'"${GRAPH_NAME}"'"}')
```

```typescript
// Count unique independence groups across sources
const independenceGroups = new Set();
for (const source of sources) {
  for (const obs of source.observations) {
    if (obs.startsWith('independence_group:')) {
      independenceGroups.add(obs.split(':')[1].trim());
    }
  }
}

// Score based on independent source group diversity
let independenceScoreValue = 10; // Default if no convergences yet
let independenceScoreAssessment = "No convergences tracked; independence not evaluated";

if (convergences.length > 0) {
  // Check how many convergences reference multiple independent groups
  const multiGroupConvergences = convergences.filter(c =>
    c.observations.some(o =>
      o.includes('independent_count') || o.includes('independence_group')
    )
  );

  const independentGroupCount = independenceGroups.size;
  const convergenceWithIndependence = multiGroupConvergences.length / convergences.length;

  independenceScoreValue = Math.round(
    Math.min(10, independentGroupCount * 2 + convergenceWithIndependence * 4)
  );
  independenceScoreValue = Math.min(10, Math.max(0, independenceScoreValue));

  independenceScoreAssessment = independenceScoreValue >= 7
    ? "Strong independence; convergences supported by multiple independent source groups"
    : independenceScoreValue >= 4
      ? "Moderate independence; some convergences need more independent sources"
      : "Low independence; convergences may rely on derivative sources";
}
```

### Step 8: Calculate Overall Score

```typescript
const lakatosTests = {
  novelPredictions: novelPredictionsScore,
  corroboration: corroborationScore,
  expandingScope: expandingScopeScore
};

const flexibilityTests = {
  paradigmFlexibility: paradigmFlexibilityScore,
  integration: integrationScore,
  selfCorrection: selfCorrectionScore
};

// Calculate overall score (average of all 9 dimensions)
// 9 dimensions total: 3 Lakatos + 3 Flexibility + Scope Coverage + Hypothesis Coverage + Independence Score
const allScores = [
  ...Object.values(lakatosTests),
  ...Object.values(flexibilityTests),
  scopeCoverageScore,
  hypothesisCoverageScore,
  independenceScoreValue
];
const overallScore = allScores.reduce((a, b) => a + b, 0) / allScores.length;
```

#### Multi-Criteria Termination

In addition to the standard threshold-based termination, stop when ANY 2 of 4 criteria are met:

```typescript
// Multi-criteria termination: stop when ANY 2 of 4 criteria are met
const criteriaMetCount = [
  scopeCoverageScore >= 8,                                              // Coverage achieved
  newEntityCount < previousIterationEntityCount * 0.1,                  // Saturation
  c1ConfidenceRatio > 0.8,                                              // Confidence achieved (>80% C1 claims have >=2 independent sources)
  ${ITERATION} >= ${MAX_ITERATIONS} - 1                                 // Budget reached
].filter(Boolean).length;

const multiCriteriaTermination = criteriaMetCount >= 2;
```

### Step 9: Apply Decision Logic

```typescript
let continueResearch: boolean;
let reason: string;
let feedback: string;

// Decision logic
if (overallScore >= 7.0) {
  continueResearch = false;
  reason = "Quality threshold reached";
  feedback = "Research has achieved satisfactory quality. Ready for documentation.";
} else if (multiCriteriaTermination) {
  // Multi-criteria early termination: ANY 2 of 4 criteria met
  continueResearch = false;
  reason = `Multi-criteria termination: ${criteriaMetCount} of 4 criteria met`;
  feedback = `Early termination: ${criteriaMetCount}/4 criteria satisfied. Overall quality: ${overallScore.toFixed(1)}/10.`;
} else if (${ITERATION} >= ${MAX_ITERATIONS} - 1) {
  continueResearch = false;
  reason = "Max iterations reached";
  feedback = `Reached iteration limit (${MAX_ITERATIONS}). Current quality: ${overallScore.toFixed(1)}/10. Consider manual review of gaps.`;
} else {
  continueResearch = true;
  reason = "More research needed";

  // Generate specific feedback based on lowest scores
  const lowestLakatos = Object.entries(lakatosTests)
    .sort((a, b) => a[1] - b[1])[0];
  const lowestFlexibility = Object.entries(flexibilityTests)
    .sort((a, b) => a[1] - b[1])[0];

  feedback = `Focus areas for next iteration:\n`;
  if (lowestLakatos[1] < 5) {
    feedback += `- Lakatos: Improve ${lowestLakatos[0]} (current: ${lowestLakatos[1]}/10)\n`;
  }
  if (lowestFlexibility[1] < 5) {
    feedback += `- Flexibility: Improve ${lowestFlexibility[0]} (current: ${lowestFlexibility[1]}/10)\n`;
  }
  feedback += `Consider: ${generateRecommendations(lakatosTests, flexibilityTests)}`;
}

// Append drift warnings to feedback if any were detected
if (driftWarnings.length > 0) {
  feedback += `\n\nScope Drift Warnings:\n`;
  for (const warning of driftWarnings) {
    feedback += `- ${warning}\n`;
  }
}

function generateRecommendations(lakatos, flexibility) {
  const recommendations = [];

  if (lakatos.novelPredictions < 5) {
    recommendations.push("Generate more testable predictions from findings");
  }
  if (lakatos.corroboration < 5) {
    recommendations.push("Seek more evidence to support existing claims");
  }
  if (lakatos.expandingScope < 5) {
    recommendations.push("Explore adjacent domains and concepts");
  }
  if (flexibility.paradigmFlexibility < 5) {
    recommendations.push("Consider alternative theoretical frameworks");
  }
  if (flexibility.integration < 5) {
    recommendations.push("Diversify sources and synthesize findings");
  }
  if (flexibility.selfCorrection < 5) {
    recommendations.push("Critically evaluate existing claims for errors");
  }

  return recommendations.slice(0, 3).join("; ");
}
```

### Step 10: Write Quality Report

```typescript
// Ensure quality directory exists
const qualityDir = `${SESSION_FOLDER}/quality`;

// Write JSON report
const qualityReport = {
  iteration: ${ITERATION},
  timestamp: new Date().toISOString(),
  lakatosTests,
  flexibilityTests,
  scopeCoverageScore,
  hypothesisCoverageScore,
  independenceScoreValue,
  driftWarnings,
  overallScore,
  continueResearch,
  reason,
  feedback,
  metrics: {
    entityCount: stats.nodeCount,
    relationCount: stats.edgeCount,
    insightCount: insights.length,
    patternCount: patterns.length,
    questionCount: questions.length,
    tensionCount: tensions.length,
    sourceCount: sources.length,
    domainCount: domains.size
  }
};

await Write(
  `${qualityDir}/iteration-${ITERATION}-quality.json`,
  JSON.stringify(qualityReport, null, 2)
);

// Write markdown report
const markdownReport = `# Quality Report: Iteration ${ITERATION}

## Lakatos Tests (Progressiveness)

| Test | Score | Assessment |
|------|-------|------------|
| Novel Predictions | ${lakatosTests.novelPredictions}/10 | ${novelPredictionsAssessment} |
| Corroboration | ${lakatosTests.corroboration}/10 | ${corroborationAssessment} |
| Expanding Scope | ${lakatosTests.expandingScope}/10 | ${expandingScopeAssessment} |

**Lakatos Average:** ${(Object.values(lakatosTests).reduce((a,b) => a+b, 0) / 3).toFixed(1)}/10

The research is ${overallScore >= 7 ? "progressive" : overallScore >= 4 ? "moderately progressive" : "potentially degenerative"}.

## Flexibility Tests

| Test | Score | Assessment |
|------|-------|------------|
| Paradigm Flexibility | ${flexibilityTests.paradigmFlexibility}/10 | ${paradigmFlexibilityAssessment} |
| Integration | ${flexibilityTests.integration}/10 | ${integrationAssessment} |
| Self-Correction | ${flexibilityTests.selfCorrection}/10 | ${selfCorrectionAssessment} |

**Flexibility Average:** ${(Object.values(flexibilityTests).reduce((a,b) => a+b, 0) / 3).toFixed(1)}/10

## Additional Dimensions

| Test | Score | Assessment |
|------|-------|------------|
| Scope Coverage | ${scopeCoverageScore}/10 | ${scopeCoverageAssessment} |
| Hypothesis Coverage | ${hypothesisCoverageScore}/10 | ${hypothesisCoverageAssessment} |
| Independence Score | ${independenceScoreValue}/10 | ${independenceScoreAssessment} |

${driftWarnings.length > 0 ? '**Drift Warnings:**\n' + driftWarnings.map(w => '- ' + w).join('\n') : 'No scope drift detected.'}

## Overall Score: ${overallScore.toFixed(1)}/10 (9 dimensions total)

## Decision: ${continueResearch ? "CONTINUE" : "TERMINATE"}

**Reason:** ${reason}

## Feedback for ${continueResearch ? "Next Iteration" : "Final Review"}

${feedback}

## Graph Metrics

| Metric | Count |
|--------|-------|
| Total Entities | ${stats.nodeCount} |
| Total Relations | ${stats.edgeCount} |
| Insights | ${insights.length} |
| Patterns | ${patterns.length} |
| Questions | ${questions.length} |
| Tensions | ${tensions.length} |
| Sources | ${sources.length} |
| Domains | ${domains.size} |

---
*Generated by Research Quality Agent*
*Session: ${GRAPH_NAME}*
`;

await Write(
  `${qualityDir}/iteration-${ITERATION}-quality.md`,
  markdownReport
);
```

### Step 11: Update Research State

```typescript
// Update state with quality results
state.quality = {
  lakatosTests,
  flexibilityTests,
  overallScore,
  continueResearch,
  lastEvaluated: new Date().toISOString()
};

state.phaseSummary = `Quality iteration ${ITERATION}: ${overallScore.toFixed(1)}/10 overall. Lakatos: ${(Object.values(lakatosTests).reduce((a,b) => a+b, 0) / 3).toFixed(1)}/10, Flexibility: ${(Object.values(flexibilityTests).reduce((a,b) => a+b, 0) / 3).toFixed(1)}/10. Decision: ${continueResearch ? "CONTINUE" : "TERMINATE"}`;
state.metadata.updatedAt = new Date().toISOString();

await Write(statePath, JSON.stringify(state, null, 2));
```

---

## Termination Conditions Summary

The research loop terminates when ANY of these conditions are met:

| Condition | Threshold | Result |
|-----------|-----------|--------|
| Quality score meets threshold | overallScore >= 7.0 | TERMINATE (success) |
| Multi-criteria termination | hypothesis coverage >= 8 AND scope coverage >= 8 AND overall >= 5.5 | TERMINATE (objectives met) |
| Maximum iterations reached | iteration >= maxIterations | TERMINATE (limit) |
| Critical error in research loop | Error prevents progress | TERMINATE (error) |

The research loop continues when:
- overallScore < 7.0 AND
- iteration < maxIterations AND
- No critical errors

---

## Output Format

Return JSON with quality evaluation:

```json
{
  "status": "complete",
  "iteration": 0,
  "lakatosTests": {
    "novelPredictions": 6,
    "corroboration": 7,
    "expandingScope": 5
  },
  "flexibilityTests": {
    "paradigmFlexibility": 8,
    "integration": 7,
    "selfCorrection": 6
  },
  "overallScore": 6.5,
  "continueResearch": true,
  "reason": "More research needed",
  "feedback": "Focus areas: Improve expandingScope. Consider exploring adjacent domains.",
  "reportPath": "quality/iteration-0-quality.md"
}
```

---

## State Updates

The quality agent updates `research-state.json`:

| Field | Update |
|-------|--------|
| `quality.lakatosTests` | Object with three test scores |
| `quality.flexibilityTests` | Object with three test scores |
| `quality.overallScore` | Calculated average (0-10) |
| `quality.continueResearch` | Boolean decision |
| `quality.lastEvaluated` | Timestamp |
| `phaseSummary` | Summary of quality assessment |
| `metadata.updatedAt` | Current ISO timestamp |

---

## Error Handling

### Missing Graph Data

```json
{
  "status": "partial",
  "warning": "Insufficient graph data for complete evaluation",
  "error": {
    "type": "data_insufficient",
    "message": "Graph has fewer than minimum entities"
  },
  "defaultScores": true
}
```

**Recovery**: Use conservative default scores, note in feedback.

### Calculation Error

```json
{
  "status": "error",
  "error": {
    "type": "calculation_error",
    "message": "Failed to compute quality metrics"
  }
}
```

**Recovery**: Report error, recommend manual quality assessment.

---

## Forbidden Actions

<critical>
This agent MUST NOT:

1. **Modify the Loom graph** - Only read for evaluation
2. **Write outside SESSION_FOLDER** - Quality reports stay under the session folder
3. **Perform research** - That is the research agent's role
4. **Create entities or relations** - Only analyze existing data
5. **Spawn other agents** - Only the orchestrator spawns agents
6. **Ask the user questions** - Operate autonomously
7. **Inflate scores arbitrarily** - Use objective criteria
8. **Override termination conditions** - Respect decision logic

If any metric cannot be computed, use conservative defaults and note in report.
</critical>

---

## Success Criteria

The agent succeeds when:

1. Current state and history have been read
2. Graph metrics have been gathered
3. All 9 dimensions have been evaluated (3 Lakatos + 3 Flexibility + Scope Coverage + Hypothesis Coverage + Independence Score)
4. Overall score has been calculated
5. Continue/terminate decision has been made
6. Quality report has been written (JSON and markdown)
7. State quality fields have been updated
8. Orchestrator has clear guidance on whether to continue or stop
