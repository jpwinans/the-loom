export const meta = {
  name: 'hyper-research',
  description: 'Meta-research orchestrator: comprehend a context doc → explore the Loom graph → extract independent questions → run deep-research PER QUESTION IN PARALLEL (shared graph + provenance tags) → consolidate → expedition + cross-session discovery → synthesize a cross-cutting report. Reuses the deep-research workflow and research-* agents.',
  whenToUse: 'Invoked by the /hyper-research skill on a context document to investigate several extracted questions at once.',
  phases: [
    { title: 'Comprehend', detail: 'read context doc + explore graph' },
    { title: 'Extract', detail: 'derive independent research questions' },
    { title: 'DeepResearch', detail: 'parallel deep-research per question (shared graph)' },
    { title: 'Discover', detail: 'consolidate + expedition + cross-session discovery' },
    { title: 'Synthesize', detail: 'cross-cutting synthesis + ingest report' },
  ],
}

const A = (typeof args === 'string' ? JSON.parse(args) : (args || {}))
const CONTEXT_DOC = A.contextDoc
const TOPIC = A.topic || null
const GRAPH = A.graph || null
const INGEST_CATEGORY = A.category || 'research'   // Loom doc-store category for the final report
// Loom access is CLI-only (see deep-research.js). LOOM is the instruction string for agent prompts.
const LOOM = 'run the Loom CLI over Bash as `loom <command> \'<json>\'` — kebab-case commands (create-graph, create-entity, create-relation, read-entity, list-entities, hybrid-search, graph-stats), JSON payload carrying the operation\'s camelCase fields plus a `graph` field. Two CLI-enforced invariants: create-relation REQUIRES polarity ("+"/"-" for causal types, null otherwise), strength (weak|moderate|strong|foundational), and evidence (string or null); and embedding is a separate step — run `loom embed-entities \'{"graph": "<GRAPH_NAME>"}\'` after each creation batch or semantic search cannot see the new entities. If `loom` is not on PATH, prefix each call with `uv run --directory "$LOOM_DIR"`, where LOOM_DIR is the path to your Loom checkout. There is no MCP server — do not look for the-loom MCP tools'
if (!CONTEXT_DOC) throw new Error('hyper-research workflow requires args.contextDoc')

// NOTE: agent({schema}) requires a top-level OBJECT (the StructuredOutput tool rejects array/scalar roots) — wrap the list.
const QUESTIONS = { type: 'object', additionalProperties: true, required: ['questions'],
  properties: { questions: { type: 'array', minItems: 0, maxItems: 5,
    items: { type: 'object', additionalProperties: true, required: ['id', 'text', 'rationale', 'priority'],
      properties: { id: { type: 'string' }, text: { type: 'string' }, rationale: { type: 'string' }, priority: { type: 'integer', minimum: 1, maximum: 5 } } } } } }
const SETUP = { type: 'object', additionalProperties: true, required: ['sessionId', 'graphName', 'reportPath'],
  properties: { sessionId: { type: 'string' }, graphName: { type: 'string' }, reportPath: { type: 'string' }, topic: { type: 'string' } } }
const UNDERSTAND = { type: 'object', additionalProperties: true, required: ['claims', 'openQuestions'],
  properties: { claims: { type: 'array', items: { type: 'string' } }, openQuestions: { type: 'array', items: { type: 'string' } },
    tensions: { type: 'array', items: { type: 'string' } }, gaps: { type: 'array', items: { type: 'string' } } } }
const FINAL = { type: 'object', additionalProperties: true, required: ['crossCuttingThemes', 'whatRemainsOpen'],
  properties: { crossCuttingThemes: { type: 'array', items: { type: 'object' } }, whatRemainsOpen: { type: 'array', items: { type: 'string' } },
    suggestedNextResearch: { type: 'array', items: { type: 'string' } } } }

// ===== Phase 0: Setup =====
phase('Comprehend')
const setup = await agent(`Initialize a hyper-research session. To touch the Loom graph, ${LOOM}. Create all folders RELATIVE to your current working directory (where Claude Code was launched), then resolve absolute paths with \`pwd\`.
1. sessionId = "hyper-{YYYY-MM-DD}-{slug}-{NNN}". Run \`mkdir -p research/hyper-sessions/{sessionId}/\`.
2. Graph: ${GRAPH ? `use "${GRAPH}".` : `create graph "hyper-{slug}"; use it.`} This ONE graph is shared by all per-question deep-research runs.
3. reportPath = ${A.output ? `"${A.output}"` : '"$(pwd)/research/reports/{slug}-{date}.md"'}; run \`mkdir -p\` on its parent dir, then write an initial report skeleton (title, context-doc pointer ${CONTEXT_DOC}, date).
Return: sessionId, graphName, reportPath (absolute), topic (derive from context doc if not given${TOPIC ? `; topic="${TOPIC}"` : ''}).`,
  { label: 'setup', phase: 'Comprehend', schema: SETUP })
const SID = setup.sessionId, GN = setup.graphName, REPORT = setup.reportPath, topic = TOPIC || setup.topic
log(`hyper-research on ${CONTEXT_DOC} → shared graph ${GN}`)

// ===== Phase 1: Comprehension =====
const understanding = await agent(`Read the context document at ${CONTEXT_DOC}. Extract its claims, evidence, conclusions, open questions, and tensions. Append a "Comprehension" section to the report at ${REPORT}.`,
  { label: 'comprehension', phase: 'Comprehend', schema: UNDERSTAND })

// ===== Phase 2: Graph exploration (parallel read-only) =====
const [recon, landscape] = await parallel([
  () => agent(`Loom recon on graph "${GN}": ${LOOM}. Run loom graph-reconnaissance '{"graph":"${GN}"}' (or loom graph-stats / loom structural-survey). Report existing-knowledge summary + structural gaps as { summary, gaps:[] }.`,
    { label: 'recon', phase: 'Comprehend', schema: { type: 'object', additionalProperties: true, required: ['gaps'], properties: { summary: { type: 'string' }, gaps: { type: 'array', items: { type: 'string' } } } } }),
  () => agent(`Semantic landscape on graph "${GN}": ${LOOM}. Run loom semantic-landscape '{"graph":"${GN}"}' (or loom hybrid-search across the topic "${topic}"). Report dense vs sparse regions as { summary, sparseAreas:[] }.`,
    { label: 'landscape', phase: 'Comprehend', schema: { type: 'object', additionalProperties: true, required: ['sparseAreas'], properties: { summary: { type: 'string' }, sparseAreas: { type: 'array', items: { type: 'string' } } } } }),
])

// ===== Phase 3: Question extraction =====
phase('Extract')
const qOut = await agent(`From the comprehension + graph exploration below, extract 1-5 INDEPENDENT (complementary, non-overlapping) research questions ranked by impact. Each needs a stable slug id (e.g. q1-feedback-latency), text, rationale, priority 1-5.
Comprehension: ${JSON.stringify(understanding)}
Recon gaps: ${JSON.stringify(recon.gaps)} | Sparse areas: ${JSON.stringify(landscape.sparseAreas)}
Append a "Research Questions" section to the report at ${REPORT}.
Return an object: { "questions": [ {id, text, rationale, priority}, ... ] }.`,
  { label: 'extract-questions', phase: 'Extract', schema: QUESTIONS })
const questions = qOut.questions || []
log(`extracted ${questions.length} questions`)

// ===== Phase 4: deep-research PER QUESTION, IN PARALLEL (shared graph + provenance tag) =====
phase('DeepResearch')
let drResults = []
if (questions.length > 0) {
  drResults = await parallel(questions.map((q) => () =>
    workflow('deep-research', {
      topic: q.text,
      graph: GN,                              // shared graph
      subgraphTag: `${SID}-${q.id}`,          // provenance partition for post-barrier dedup
      sessionLabel: q.id,
    }).then((r) => ({ questionId: q.id, status: 'complete', ...r }))
      .catch((e) => ({ questionId: q.id, status: 'failed', error: String(e && e.message || e) }))
  ))
  drResults.forEach((r) => log(`q ${r.questionId}: ${r.status}${r.finalScore != null ? ' (score ' + r.finalScore + ')' : ''}`))
}

// ===== Phase 4.5: consolidation barrier (dedup the shared graph after all questions land) =====
phase('Discover')
await agent(`Loom access: ${LOOM}. The shared graph "${GN}" now holds entities from ${questions.length} parallel deep-research runs (each tagged "subgraph: ${SID}-<questionId>"). Run the consolidation pass to merge cross-question duplicates and propagate credit, exactly per your agent definition. Emit your Consolidation contract object.`,
  { label: 'consolidate-merge', agentType: 'research-consolidation', phase: 'Discover',
    schema: { type: 'object', additionalProperties: true, required: ['entityCount', 'relationCount'], properties: { entityCount: { type: 'integer' }, relationCount: { type: 'integer' } } } })

// ===== Phase 5: expedition (full graph → after barrier) =====
const expedition = await agent(`Run a mini Loom expedition over the fully-merged shared graph "${GN}" (TOPIC "${topic}") per your agent definition. Emit your Expedition contract object. Append findings to the report at ${REPORT}.`,
  { label: 'expedition', agentType: 'research-expedition', phase: 'Discover',
    schema: { type: 'object', additionalProperties: true, required: ['emergentTheory'], properties: { emergentTheory: { type: 'object', required: ['found'], properties: { found: { type: 'boolean' }, plainLanguageSummary: { type: 'string' } } } } } })

// ===== Phase 5.5: cross-session discovery (parallel, additive/non-blocking) =====
const [creativity, farAnalogy] = await parallel([
  () => agent(`${LOOM}. Run loom creativity-loop '{"graph":"${GN}","maxCycles":5,"purpose":"${topic}"}'. Report discovered insights as { insights:[] }. Non-blocking — if it errors, return {insights:[]}.`,
    { label: 'creativity', phase: 'Discover', schema: { type: 'object', additionalProperties: true, required: ['insights'], properties: { insights: { type: 'array', items: { type: 'string' } } } } })
    .catch(() => ({ insights: [] })),
  () => agent(`${LOOM}. Run loom far-analogy-retrieval '{"graph":"${GN}","maxCandidates":5,"maxProposals":10}'. Report analogies as { analogies:[] }. Non-blocking — if it errors, return {analogies:[]}.`,
    { label: 'far-analogy', phase: 'Discover', schema: { type: 'object', additionalProperties: true, required: ['analogies'], properties: { analogies: { type: 'array', items: { type: 'string' } } } } })
    .catch(() => ({ analogies: [] })),
])

// ===== Phase 6: synthesis =====
phase('Synthesize')
const synthesis = await agent(`Synthesize across all per-question deep-research results + expedition + cross-session discovery. Identify cross-cutting themes (each with supportingQuestionIds), what remains open, suggested next research. Append a "Cross-Cutting Synthesis" section to the report at ${REPORT}.
Per-question results: ${JSON.stringify(drResults.map((r) => ({ id: r.questionId, status: r.status, score: r.finalScore, sessionId: r.sessionId })))}
Expedition: ${JSON.stringify(expedition.emergentTheory)} | Creativity: ${JSON.stringify(creativity.insights)} | Far-analogy: ${JSON.stringify(farAnalogy.analogies)}`,
  { label: 'synthesis', phase: 'Synthesize', schema: FINAL })

// ===== Phase 7: ingestion =====
await agent(`${LOOM}. Ingest the final hyper-research report into the Loom document store: loom ingest-document '{"file_path":"${REPORT}","category":"${INGEST_CATEGORY}"}' (note: this command takes snake_case file_path). Report { ingested:boolean }.`,
  { label: 'ingest', phase: 'Synthesize', schema: { type: 'object', additionalProperties: true, required: ['ingested'], properties: { ingested: { type: 'boolean' } } } })

return {
  sessionId: SID, graphName: GN, reportPath: REPORT,
  questions: questions.map((q) => q.id),
  perQuestion: drResults.map((r) => ({ id: r.questionId, status: r.status, score: r.finalScore })),
  completedQuestions: drResults.filter((r) => r.status === 'complete').length,
  failedQuestions: drResults.filter((r) => r.status !== 'complete').length,
  crossCuttingThemes: synthesis.crossCuttingThemes, whatRemainsOpen: synthesis.whatRemainsOpen,
}
