export const meta = {
  name: 'deep-research',
  description: 'Autonomous deep-research engine: orientation → quality-gated research loop (research/synthesis/verify/consolidation + parallel red-team & expedition) → documentation → finalize. Reuses the research-* subagents via agentType; schema-validated handoffs. Drives the Loom through its JSON-in/JSON-out CLI; portable (no vault/identity coupling).',
  whenToUse: 'Run one research question end-to-end into a Loom graph. Invoked by the /deep-research skill and (per-question, in parallel) by the hyper-research workflow.',
  phases: [
    { title: 'Setup', detail: 'session folder, graph, question classification' },
    { title: 'Orient', detail: 'orientation (research contract)' },
    { title: 'Loop', detail: 'quality-gated research iterations' },
    { title: 'Finalize', detail: 'documentation + research_session entity' },
  ],
}

// ---- run params (embedded from args; defensive — Stage-0 lesson: args may arrive loosely typed) ----
const A = (typeof args === 'string' ? JSON.parse(args) : (args || {}))
const TOPIC = A.topic
const GRAPH = A.graph || null            // null → setup creates one
const LABEL = A.sessionLabel || 'deep'
const SUBGRAPH_TAG = A.subgraphTag || null   // set by hyper-research for provenance partitioning
// Loom access is CLI-only: the Loom ships a single JSON-in/JSON-out CLI and no MCP server. LOOM below is the
// instruction string injected into agent prompts; agents run it over Bash.
const LOOM = 'run the Loom CLI over Bash as `loom <command> \'<json>\'` — kebab-case commands (create-graph, create-entity, create-relation, read-entity, list-entities, hybrid-search, graph-stats, update-entity), JSON payload carrying the operation\'s camelCase fields plus a `graph` field. Two CLI-enforced invariants: create-relation REQUIRES polarity ("+"/"-" for causal types, null otherwise), strength (weak|moderate|strong|foundational), and evidence (string or null); and embedding is a separate step — run `loom embed-entities \'{"graph": "<GRAPH_NAME>"}\'` after each creation batch or semantic search cannot see the new entities. If `loom` is not on PATH, prefix each call with `uv run --directory "$LOOM_DIR"`, where LOOM_DIR is the path to your Loom checkout. There is no MCP server — do not look for the-loom MCP tools'
if (!TOPIC) throw new Error('deep-research workflow requires args.topic')

// ---- schemas (canonical defs in .claude/references/research-schemas.md) ----
const SETUP = { type: 'object', additionalProperties: true, required: ['sessionFolder', 'graphName', 'sessionId', 'classification'],
  properties: { sessionFolder: { type: 'string' }, graphName: { type: 'string' }, sessionId: { type: 'string' },
    classification: { type: 'object', required: ['type', 'maxIterations', 'enableRedTeam', 'enableCheckpoint'],
      properties: { type: { enum: ['A', 'B', 'C', 'D'] }, maxIterations: { type: 'integer', minimum: 2, maximum: 7 },
        enableRedTeam: { type: 'boolean' }, enableCheckpoint: { type: 'boolean' }, rationale: { type: 'string' } } } } }
const VBLOCK = { type: 'object', required: ['entitiesAttempted', 'entitiesVerified', 'failedCreations'],
  properties: { entitiesAttempted: { type: 'integer', minimum: 0 }, entitiesVerified: { type: 'integer', minimum: 0 },
    failedCreations: { type: 'array', items: { type: 'object' } } } }
const CONTRACT = { type: 'object', additionalProperties: true, required: ['coreQuestion', 'successCriteria', 'initialQuestions', 'seededEntityIds'],
  properties: { coreQuestion: { type: 'string' }, successCriteria: { type: 'array', items: { type: 'string' } },
    initialQuestions: { type: 'array', items: { type: 'string' } }, seededEntityIds: { type: 'array', items: { type: 'string' } } } }
const FINDINGS = { type: 'object', additionalProperties: true, required: ['iteration', 'newEntityIds', 'verification'],
  properties: { iteration: { type: 'integer' }, newEntityIds: { type: 'array', items: { type: 'string' } }, verification: VBLOCK } }
const SYNTH = { type: 'object', additionalProperties: true, required: ['patternIds', 'insightIds', 'tensionIds', 'verification'],
  properties: { patternIds: { type: 'array', items: { type: 'string' } }, insightIds: { type: 'array', items: { type: 'string' } },
    tensionIds: { type: 'array', items: { type: 'string' } }, verification: VBLOCK } }
const CONSOL = { type: 'object', additionalProperties: true, required: ['entityCount', 'relationCount'],
  properties: { entityCount: { type: 'integer', minimum: 0 }, relationCount: { type: 'integer', minimum: 0 },
    recommendations: { type: 'array', items: { type: 'string' } } } }
const EXPED = { type: 'object', additionalProperties: true, required: ['emergentTheory'],
  properties: { emergentTheory: { type: 'object', required: ['found'], properties: { found: { type: 'boolean' }, plainLanguageSummary: { type: 'string' } } } } }
const REDTEAM = { type: 'object', additionalProperties: true, required: ['counterEvidenceIds', 'survivedClaimIds', 'weakenedClaimIds', 'verification'],
  properties: { counterEvidenceIds: { type: 'array', items: { type: 'string' } }, survivedClaimIds: { type: 'array', items: { type: 'string' } },
    weakenedClaimIds: { type: 'array', items: { type: 'string' } }, verification: VBLOCK } }
const QUALITY = { type: 'object', additionalProperties: true, required: ['overallScore', 'continueResearch', 'stoppingReason'],
  properties: { overallScore: { type: 'number', minimum: 0, maximum: 10 }, continueResearch: { type: 'boolean' },
    stoppingReason: { enum: ['continue', 'quality_threshold', 'multi_criteria', 'max_iterations', 'saturation', 'error'] },
    feedback: { type: 'string' }, nextIterationQueries: { type: 'array', items: { type: 'string' } } } }
const DOCS = { type: 'object', additionalProperties: true, required: ['artifactsCreated'],
  properties: { artifactsCreated: { type: 'array', items: { type: 'object' } } } }
const FINAL = { type: 'object', additionalProperties: true, required: ['researchSessionEntityId', 'manifestPath'],
  properties: { researchSessionEntityId: { type: 'string' }, manifestPath: { type: 'string' } } }

const params = (extra) => `Loom access: ${LOOM}.
SESSION_FOLDER: {{SF}}
GRAPH_NAME: {{GN}}
TOPIC: ${TOPIC}${SUBGRAPH_TAG ? `\nPROVENANCE: stamp every entity/relation you create with observation "subgraph: ${SUBGRAPH_TAG}" (logical partition for cross-question dedup).` : ''}
${extra || ''}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`

// agent() resolves to null when a subagent dies on a terminal API error (e.g. "Connection closed
// mid-response") after its internal retries. Schema validation does NOT cover that case — it
// constrains the shape of a result that arrived, not whether one arrived at all. Dereferencing the
// null loses the whole run, so retry with a fresh subagent and only then give up. Attempt 0 reuses
// the exact (prompt, opts) so resumed runs still hit the result cache.
const resilient = async (prompt, opts, tries = 3) => {
  for (let a = 0; a < tries; a++) {
    const p = a === 0 ? prompt : `${prompt}\n\nRETRY ${a}: a previous attempt died mid-response. Resume from the CURRENT graph state — re-query the graph first and do not re-create entities that already exist.`
    const r = await agent(p, a === 0 ? opts : { ...opts, label: `${opts.label}-retry${a}` })
    if (r) return r
    log(`${opts.label}: attempt ${a + 1}/${tries} returned null (subagent died) — retrying`)
  }
  throw new Error(`${opts.label}: all ${tries} attempts died on terminal API errors. Halting.`)
}

// ===== Phase 0: Setup =====
phase('Setup')
const setup = await resilient(`Initialize a deep-research session. To touch the Loom graph, ${LOOM}.
1. Create the session folder RELATIVE to your current working directory (where Claude Code was launched): research/sessions/{sessionId}/ where sessionId = "{YYYY-MM-DD}-{slug of TOPIC}-{NNN}". Run \`mkdir -p research/sessions/{sessionId}/{findings,quality,artifacts}\`, then resolve it to an absolute path with \`pwd\` and return sessionFolder = "$(pwd)/research/sessions/{sessionId}".
2. Graph: ${GRAPH ? `use existing graph "${GRAPH}".` : `create a new graph named the sessionId (or "deep-{slug}") by running loom create-graph '{"name":"<sessionId>"}'; use that.`}
3. Classify the research question TOPIC="${TOPIC}" into Type A (lookup, maxIterations 2, no red-team/checkpoint) / B (comparative, 3, no/no) / C (open investigation, 5, red-team yes, checkpoint yes) / D (deep theoretical, 7, yes, yes).
4. Write research-state.json (inside sessionFolder) with phase, classification, paths.
Return: sessionFolder (abs path under the launch cwd), graphName, sessionId, classification {type,maxIterations,enableRedTeam,enableCheckpoint,rationale}.`,
  { label: `setup:${LABEL}`, phase: 'Setup', schema: SETUP })

const SF = setup.sessionFolder, GN = setup.graphName, cls = setup.classification
const P = (extra) => params(extra).replace('{{SF}}', SF).replace('{{GN}}', GN)
log(`deep-research "${TOPIC}" → graph ${GN}, Type ${cls.type} (maxIter ${cls.maxIterations}, redTeam ${cls.enableRedTeam})`)

// ===== Phase 1-2: Orient =====
phase('Orient')
const contract = await resilient(`${P('Clarify the research intention and seed initial Loom entities (concepts + questions) in the session graph.')}`,
  { label: `orient:${LABEL}`, agentType: 'research-orientation', phase: 'Orient', schema: CONTRACT })

// ===== Phase 3: quality-gated research loop =====
phase('Loop')
let iter = 0, verdict = { continueResearch: true, nextIterationQueries: contract.initialQuestions }
const trail = []
while (verdict.continueResearch && iter < cls.maxIterations) {
  const queries = (verdict.nextIterationQueries || []).join('; ')
  const findings = await resilient(`${P(`ITERATION: ${iter}\nFocus queries for this iteration: ${queries || '(use the research contract)'}`)}`,
    { label: `research:${LABEL}-${iter}`, agentType: 'research-agent', phase: 'Loop', schema: FINDINGS })
  const synth = await resilient(`${P(`ITERATION: ${iter}\nSynthesize patterns/insights/tensions over the new findings.`)}`,
    { label: `synth:${LABEL}-${iter}`, agentType: 'research-synthesis', phase: 'Loop', schema: SYNTH })

  // Verification — pure JS over structured output that resilient() guarantees is non-null.
  // Zero-verified-despite-attempts is fatal.
  const att = findings.verification.entitiesAttempted + synth.verification.entitiesAttempted
  const ver = findings.verification.entitiesVerified + synth.verification.entitiesVerified
  if (att > 0 && ver === 0) throw new Error(`Iter ${iter}: ${att} entities attempted, ZERO verified — Loom write path failing silently (check that the loom CLI is reachable, FalkorDB is up, and GRAPH_NAME "${GN}" is correct). Halting.`)

  const consol = await resilient(`${P(`ITERATION: ${iter}\nClean the graph: dedup, prune orphans, propagate credit.`)}`,
    { label: `consol:${LABEL}-${iter}`, agentType: 'research-consolidation', phase: 'Loop', schema: CONSOL })

  // Independent conditional work runs concurrently (both only feed quality).
  const wantExp = iter >= 1 && consol.entityCount >= 20
  const wantRT = cls.enableRedTeam && iter >= 2
  const side = []
  if (wantExp) side.push(() => agent(`${P(`ITERATION: ${iter}\nMine emergent theories from graph topology.`)}`,
    { label: `exped:${LABEL}-${iter}`, agentType: 'research-expedition', phase: 'Loop', schema: EXPED }))
  if (wantRT) side.push(() => agent(`${P(`ITERATION: ${iter}\nAdversarially seek counter-evidence to high-confidence claims; create contradicts relations.`)}`,
    { label: `redteam:${LABEL}-${iter}`, agentType: 'research-red-team', phase: 'Loop', schema: REDTEAM }))
  const sideResults = side.length ? await parallel(side) : []
  let k = 0
  const expedition = wantExp ? sideResults[k++] : null
  const redTeam = wantRT ? sideResults[k++] : null

  verdict = await resilient(`${P(`ITERATION: ${iter}\nMAX_ITERATIONS: ${cls.maxIterations}\nEvaluate Lakatos + flexibility; decide continue/terminate.\nThis iteration: consolidation entityCount=${consol.entityCount}; expedition=${expedition ? (expedition.emergentTheory.found ? 'theory found' : 'none') : 'n/a'}; redTeam=${redTeam ? `${redTeam.counterEvidenceIds.length} counter-evidence, ${redTeam.weakenedClaimIds.length} weakened` : 'n/a'}.`)}`,
    { label: `quality:${LABEL}-${iter}`, agentType: 'research-quality', phase: 'Loop', schema: QUALITY })
  trail.push({ iter, score: verdict.overallScore, entityCount: consol.entityCount, stoppingReason: verdict.stoppingReason })
  log(`iter ${iter}: score ${verdict.overallScore}, ${consol.entityCount} entities, ${verdict.continueResearch ? 'continue' : 'terminate (' + verdict.stoppingReason + ')'}`)
  iter++
}

// ===== Phase 4-5: Finalize =====
phase('Finalize')
await resilient(`${P('Create documentation artifacts (zettelkasten, research doc, journal reflection) from the graph entities.')}`,
  { label: `docs:${LABEL}`, agentType: 'research-documentation', phase: 'Finalize', schema: DOCS })
const final = await resilient(`Finalize the deep-research session. To touch the Loom graph, ${LOOM}.
SESSION_FOLDER: ${SF}\nGRAPH_NAME: ${GN}\nTOPIC: ${TOPIC}
1. Query final graph-stats.
2. Create a research_session entity in the graph capturing {topic, sessionId ${setup.sessionId}, iterations ${iter}, finalScore ${verdict.overallScore}} and link it to the session's key insight/pattern entities (sources/derived_from relations).
3. Write artifact-manifest.json listing all artifacts + the research_session entity id.
Return: researchSessionEntityId, manifestPath.`,
  { label: `finalize:${LABEL}`, phase: 'Finalize', schema: FINAL })

return {
  topic: TOPIC, sessionId: setup.sessionId, graphName: GN, classification: cls,
  iterations: iter, finalScore: verdict.overallScore, stoppingReason: verdict.stoppingReason,
  researchSessionEntityId: final.researchSessionEntityId, manifestPath: final.manifestPath, trail,
}
