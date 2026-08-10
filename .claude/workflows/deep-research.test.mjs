// Regression tests for the deep-research loop gating. No deps, no CI wiring — run it directly:
//   node .claude/workflows/deep-research.test.mjs
//
// Workflow scripts are executed by the harness inside an async wrapper with `args`, `agent`, `log`,
// `phase` and `parallel` injected as globals, so they are neither importable nor `node --check`-able
// as-is. These tests load the real source, wrap it in an AsyncFunction taking those names as
// parameters, and drive it with stub agents and scripted quality verdicts. The point is the
// scheduling of the side agents (red team, expedition), so every stub returns the minimum object
// that satisfies its schema.
import { readFileSync } from 'node:fs'

const SRC = new URL('./deep-research.js', import.meta.url)
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const body = readFileSync(SRC, 'utf8').replace(/^export const meta/m, 'const meta')
const wf = new AsyncFunction('args', 'agent', 'log', 'phase', 'parallel', body)

const V = { entitiesAttempted: 3, entitiesVerified: 3, failedCreations: [] }

// entityCounts[i] is what consolidation reports on iteration i; verdicts are handed to the quality
// agent in order. A re-score consumes the next scripted verdict only if it is marked {rescore:true}.
async function run({ cls, entityCounts, verdicts }) {
  const calls = []
  let iterSeen = -1
  const q = [...verdicts]

  const agent = async (prompt, opts) => {
    const l = opts.label
    calls.push(l)
    if (l.startsWith('setup:')) return { sessionFolder: '/tmp/sf', graphName: 'g', sessionId: 'sid', classification: cls }
    if (l.startsWith('orient:')) return { coreQuestion: 'q', successCriteria: [], initialQuestions: ['q1'], seededEntityIds: ['e1'] }
    if (l.startsWith('research:')) { iterSeen++; return { iteration: iterSeen, newEntityIds: ['e2'], verification: V } }
    if (l.startsWith('synth:')) return { patternIds: [], insightIds: [], tensionIds: [], verification: V }
    if (l.startsWith('consol:')) return { entityCount: entityCounts[iterSeen] ?? 0, relationCount: 10 }
    if (l.startsWith('exped:')) return { emergentTheory: { found: true, plainLanguageSummary: 's' } }
    if (l.startsWith('redteam:')) return { counterEvidenceIds: ['ce1'], survivedClaimIds: ['c1'], weakenedClaimIds: ['c2'], verification: V }
    if (l.startsWith('quality:')) return l.endsWith('-rescore') && !(q.length && q[0].rescore) ? STOP : q.shift()
    if (l.startsWith('docs:')) return { artifactsCreated: [] }
    if (l.startsWith('finalize:')) return { researchSessionEntityId: 'rs1', manifestPath: '/tmp/m.json' }
    throw new Error(`unstubbed agent label ${l}`)
  }
  const out = await wf({ topic: 'T', sessionLabel: 'test' }, agent, () => {}, () => {}, (ts) => Promise.all(ts.map((f) => f())))
  const of = (p) => calls.filter((c) => c.startsWith(p))
  return { out, calls, rt: of('redteam:'), exp: of('exped:'), quality: of('quality:') }
}

const TYPE_D = { type: 'D', maxIterations: 7, enableRedTeam: true, enableCheckpoint: true }
const TYPE_C2 = { type: 'C', maxIterations: 2, enableRedTeam: true, enableCheckpoint: true }
const TYPE_A = { type: 'A', maxIterations: 2, enableRedTeam: false, enableCheckpoint: false }
const TYPE_B = { type: 'B', maxIterations: 3, enableRedTeam: false, enableCheckpoint: false }
const STOP = { overallScore: 9.22, continueResearch: false, stoppingReason: 'quality_threshold' }
const GO = { overallScore: 6.0, continueResearch: true, stoppingReason: 'continue', nextIterationQueries: ['n'] }

let failures = 0
const check = (name, cond, detail) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? `  [${detail}]` : ''}`)
  if (!cond) failures++
}

// 1. The reported failure: a Type D question whose first iteration scores above threshold. The loop
//    exits at iteration 0, which `iter >= 2` could never survive.
{
  const r = await run({ cls: TYPE_D, entityCounts: [30], verdicts: [STOP] })
  check('Type D, exits at iter 0: red team runs exactly once', r.rt.length === 1, r.rt.join(',') || 'none')
  check('Type D, exits at iter 0: red team ran at iteration 0', r.rt[0] === 'redteam:test-0-final', r.rt[0])
  check('Type D, exits at iter 0: verdict is re-scored with the red team in hand', r.quality.join(',') === 'quality:test-0,quality:test-0-rescore', r.quality.join(','))
  check('Type D, exits at iter 0: red team precedes documentation', r.calls.indexOf(r.rt[0]) < r.calls.findIndex((c) => c.startsWith('docs:')), r.calls.join(' > '))
  check('Type D, exits at iter 0: no extra iteration is padded to reach the red team', r.out.iterations === 1, `iterations=${r.out.iterations}`)
}

// 2. Classification is respected: A and B never gain a red team.
for (const [n, cls] of [['Type A', TYPE_A], ['Type B', TYPE_B]]) {
  const r = await run({ cls, entityCounts: [30, 30, 30], verdicts: [STOP] })
  check(`${n} (enableRedTeam:false): red team does NOT run`, r.rt.length === 0, r.rt.join(',') || 'none')
  check(`${n} (enableRedTeam:false): no re-score`, r.quality.length === 1, r.quality.join(','))
}

// 3. A long run red-teams once, in-loop, on a matured graph — not once per iteration.
{
  const r = await run({ cls: TYPE_D, entityCounts: [30, 30, 30, 30, 30], verdicts: [GO, GO, GO, GO, STOP] })
  check('Long run: red team runs exactly once', r.rt.length === 1, r.rt.join(',') || 'none')
  check('Long run: red team runs in-loop at iter 2 (not as a last-chance pass)', r.rt[0] === 'redteam:test-2', r.rt[0])
  check('Long run: no redundant re-score', r.quality.filter((c) => c.endsWith('-rescore')).length === 0, r.quality.join(','))
  check('Long run: ran 5 iterations', r.out.iterations === 5, `iterations=${r.out.iterations}`)
}

// 4. The other unreachable path: maxIterations exhausted before iteration 2.
{
  const r = await run({ cls: TYPE_C2, entityCounts: [30, 30], verdicts: [GO, GO] })
  check('maxIterations=2, quality always continuing: red team runs exactly once', r.rt.length === 1, r.rt.join(',') || 'none')
  check('maxIterations=2: red team fires on the final allowed iteration', r.rt[0] === 'redteam:test-1-final', r.rt[0])
}

// 5. The expedition keeps its substantive guard and is now reachable at iteration 0.
{
  const r = await run({ cls: TYPE_B, entityCounts: [12], verdicts: [STOP] })
  check('Expedition: skipped when entityCount < 20', r.exp.length === 0, r.exp.join(',') || 'none')
}
{
  const r = await run({ cls: TYPE_B, entityCounts: [30], verdicts: [STOP] })
  check('Expedition: runs at iteration 0 when entityCount >= 20', r.exp.join(',') === 'exped:test-0', r.exp.join(',') || 'none')
}
{
  const r = await run({ cls: TYPE_B, entityCounts: [12, 25], verdicts: [GO, STOP] })
  check('Expedition: gated per-iteration on material (skip iter 0 at 12, run iter 1 at 25)', r.exp.join(',') === 'exped:test-1', r.exp.join(',') || 'none')
}

// 6. The red team must land before the verdict is final, not merely before finalize.
{
  const r = await run({ cls: TYPE_D, entityCounts: [30], verdicts: [STOP] })
  check('Red team informs the final verdict (re-score runs after the red team)', r.calls.indexOf('redteam:test-0-final') < r.calls.indexOf('quality:test-0-rescore'), r.calls.join(' > '))
}

console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILURE(S)`)
process.exit(failures ? 1 : 0)
