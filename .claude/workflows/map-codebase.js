export const meta = {
  name: 'map-codebase',
  description: 'Architecture map of a codebase: tree-sitter extraction → parallel semantic enrichment per module group → analysis + written map + visualization. Incremental re-runs via update-codebase.',
  whenToUse: 'Invoked by the /map-codebase skill to map a repo into a Loom graph with an ARCHITECTURE-MAP.md and codebase-map.html.',
  phases: [
    { title: 'Setup', detail: 'extract or incrementally update the graph; build module groups' },
    { title: 'Enrich', detail: 'parallel semantic enrichment per module group (skipped with --no-enrich)' },
    { title: 'Embed', detail: 'one embed-entities pass over everything created' },
    { title: 'Cartograph', detail: 'analysis + map document + visualization + manifest' },
  ],
}

const A = (typeof args === 'string' ? JSON.parse(args) : (args || {}))
const PATH = A.path || '.'
const GRAPH = A.graph || null          // null → setup derives codebase-{slug}
const OUTPUT = A.output || null        // null → {PATH}/docs/architecture/
const FULL = !!A.full
const NO_TESTS = !!A.noTests
const NO_ENRICH = !!A.noEnrich         // structural-only run: skip the Enrich phase entirely
const THOROUGH = !!A.thorough          // incremental: force whole-group re-enrichment + full cartograph (pre-fast-path behavior)
// Fast-update thresholds (incremental mode only). A changed group is classified:
//   skip    — tiny diff, no files added/removed: structural update already landed, the
//             semantic layer is almost surely still true; not re-enriched, reported as carried.
//   delta   — a minority of the group's files changed: the enricher re-reads only the
//             changed files and supersedes only the notes citing them (cheaper model).
//   rewrite — the group substantially changed: today's whole-group re-enrichment.
const SKIP_MAX_LINES = 15              // total added+deleted lines across the group
const DELTA_MAX_FRACTION = 0.5         // changed files / group files above this → rewrite
const REFRESH_MAX_GROUPS = 8           // more enriched groups than this → full cartograph rewrite
// Loom access is CLI-only. LOOM is the instruction string injected into agent prompts.
const LOOM = 'run the Loom CLI over Bash as `loom <command> \'<json>\'` — kebab-case commands, camelCase JSON fields plus a `graph` field on every call. Two CLI-enforced invariants: create-relation REQUIRES polarity ("+"/"-" for causal types, null otherwise), strength (weak|moderate|strong|foundational), and evidence (string or null); and embedding is a separate step — run `loom embed-entities \'{"graph": "<GRAPH_NAME>"}\'` after each creation batch. If `loom` is not on PATH, prefix each call with `uv run --directory "$LOOM_DIR"`, where LOOM_DIR is the path to your Loom checkout. There is no MCP server — do not look for the-loom MCP tools'
// Cheap-path commands worth knowing before falling back to full listings/scans.
const CONSUMPTION_HINT = 'Prefer the Consumption commands for one-call answers instead of manual multi-step lookups: `loom explore \'{"name": "<symbol>", "graph": "<GRAPH_NAME>"}\'` (definition, callers, callees, imports, containment, inheritance, semantic layer, budgeted), `loom find-callers` / `loom find-callees` (`{"name": "<symbol>", "graph": "<GRAPH_NAME>"}`, ranked and anchored), and `loom blast-radius \'{"name": "<symbol>", "graph": "<GRAPH_NAME>"}\'` (reverse dependency reach, grouped by module). All entity-addressed reads take `name` instead of an id (exactly one of `id`/`name`), and `list-entities` / `read-entity` / `get-neighbors` / `get-relations` / `entity-deep-dive` accept `"compact": true` and `"limit": N` to keep responses small.'
// Repo-hygiene guard, injected into every sub-agent prompt: nothing but the pipeline's
// designated deliverables (and loom's own graph writes) may land in the repo tree.
const SCRATCH_GUARD = 'Never write scratch, intermediate, or bookkeeping files (batch results, retry state, dedupe logs, working notes) into the project tree or the repo root, even transiently — use /tmp for anything temporary. The only files this pipeline commits to the target repo are the designated OUTPUT_DIR deliverables (ARCHITECTURE-MAP.md, codebase-map.html, QUERYING.md, map-manifest.json); everything else is a loom CLI graph write, never a local file.'

// ---- schemas (canonical defs in .claude/references/map-codebase-schemas.md) ----
const SETUP = { type: 'object', additionalProperties: true,
  required: ['graphName', 'projectPath', 'mode', 'headCommit', 'moduleGroups'],
  properties: { graphName: { type: 'string' }, projectPath: { type: 'string' },
    mode: { enum: ['full', 'incremental'] }, headCommit: { type: 'string' },
    moduleGroups: { type: 'array', items: { type: 'object', required: ['id', 'label', 'paths', 'fileCount'],
      properties: { id: { type: 'string' }, label: { type: 'string' },
        paths: { type: 'array', items: { type: 'string' } }, fileCount: { type: 'integer', minimum: 0 },
        changedFiles: { type: 'array', items: { type: 'string' } },
        changedLines: { type: 'integer', minimum: 0 },
        addedOrDeleted: { type: 'integer', minimum: 0 } } } },
    skippedFiles: { type: 'integer', minimum: 0 }, dirtyTree: { type: 'boolean' } } }
const VBLOCK = { type: 'object', required: ['entitiesAttempted', 'entitiesVerified', 'failedCreations'],
  properties: { entitiesAttempted: { type: 'integer', minimum: 0 }, entitiesVerified: { type: 'integer', minimum: 0 },
    failedCreations: { type: 'array', items: { type: 'object' } } } }
const ENRICH = { type: 'object', additionalProperties: true,
  required: ['groupId', 'conceptIds', 'patternIds', 'claimIds', 'tensionIds', 'verification'],
  properties: { groupId: { type: 'string' },
    conceptIds: { type: 'array', items: { type: 'string' } }, patternIds: { type: 'array', items: { type: 'string' } },
    claimIds: { type: 'array', items: { type: 'string' } }, tensionIds: { type: 'array', items: { type: 'string' } },
    supersededCount: { type: 'integer', minimum: 0 }, verification: VBLOCK } }
const MAP = { type: 'object', additionalProperties: true, required: ['mapPath', 'vizPath', 'queryingDoc', 'stats', 'keyFindings'],
  properties: { mapPath: { type: 'string' }, vizPath: { type: 'string' }, queryingDoc: { type: 'string' },
    stats: { type: 'object', required: ['entities', 'relations', 'cycles', 'hubs'],
      properties: { entities: { type: 'integer' }, relations: { type: 'integer' },
        cycles: { type: 'integer' }, hubs: { type: 'integer' } } },
    keyFindings: { type: 'array', items: { type: 'string' }, maxItems: 10 } } }

// ===== Phase 0: Setup =====
phase('Setup')
const setup = await agent(`Initialize a map-codebase run. Loom access: ${LOOM}. ${SCRATCH_GUARD}
1. Resolve TARGET PATH "${PATH}" to an absolute path (pwd-relative if not absolute); derive slug from its dirname; GRAPH_NAME = ${GRAPH ? `"${GRAPH}"` : '"codebase-{slug}"'}; OUTPUT_DIR = ${OUTPUT ? `"${OUTPUT}"` : '"{abs path}/docs/architecture/"'} (mkdir -p it). Record \`git -C <path> rev-parse HEAD\` and whether \`git -C <path> status --porcelain\` is non-empty (dirtyTree).
2. Fail fast: \`loom graph-stats '{}'\` must succeed — if it errors on connection, throw with the remediation line "docker compose up -d falkordb".
3. Mode: if OUTPUT_DIR/map-manifest.json exists AND its graphName equals GRAPH_NAME AND that graph exists AND ${FULL} is false → mode "incremental" (on a graphName mismatch, fall back to mode "full"): run loom update-codebase '{"projectPath": "<abs>", "graphName": "<GRAPH_NAME>", "gitRef": "<manifest.commit>"${NO_TESTS ? ', "includeTests": false' : ''}}'. Otherwise mode "full": loom create-graph '{"name": "<GRAPH_NAME>"}' (ignore already-exists), then loom extract-codebase '{"projectPath": "<abs>", "graph": "<GRAPH_NAME>"${NO_TESTS ? ', "includeTests": false' : ''}}'. After extraction, compute skippedFiles = (\`git -C <path> ls-files | wc -l\`) minus stats.totalFiles — the repo files not parsed (unsupported types, config, docs). Do NOT embed here — embedding happens once, later in this run.
4. Module groups: loom list-entities '{"entityType": "system", "compact": true, "limit": 5000, "graph": "<GRAPH_NAME>"}' → group file paths by top-level directory, then balance each directory's files into groups by cumulative file size (\`wc -c\` or stat), capping each group at ~150KB AND 25 files, whichever binds first (split an oversized directory into multiple size-capped groups, in path order, rather than one lopsided group — a single group running 10x the median wall-clock time defeats the point of parallel enrichment). Fold directories with <3 files into their parent, EXCEPT give \`theloom/reification\` and \`theloom/symbolic\` (or their nearest equivalents in this repo) their own group whenever they have >=2 files, even under the fold-in threshold — they carry enough conceptual weight to deserve a dedicated read. Group id = kebab slug of the dir path (with a numeric suffix when a directory splits into more than one group, e.g. \`theloom-store-1\`, \`theloom-store-2\`). Each group's \`paths\` are FILE paths (relative to the project root), not directories. In incremental mode, keep ONLY groups containing files changed in the update-codebase diff.
5. Incremental mode only — per kept group, also record the change magnitude: \`changedFiles\` = the group's paths that appear in the update-codebase diff (relative paths); \`changedLines\` = the sum of added+deleted lines for those files from \`git -C <path> diff --numstat <manifest.commit> -- <the changed files>\` (a binary file's "-" counts as 100); \`addedOrDeleted\` = how many of the group's changedFiles were added, deleted, or renamed rather than modified (from the diff's status letters). In full mode omit these three fields.
Return the Setup contract object.`,
  { label: 'setup', phase: 'Setup', schema: SETUP })
log(`map-codebase ${setup.mode} → graph ${setup.graphName}, ${setup.moduleGroups.length} groups @ ${setup.headCommit.slice(0, 8)}`)

// ===== Phase 1: Enrich (parallel per module group; skipped entirely with --no-enrich) =====
// Incremental classification: skip (carried) / delta (changed files only, cheaper model) /
// rewrite (whole group, today's behavior). Full runs and --thorough classify everything rewrite.
const classify = (g) => {
  if (setup.mode !== 'incremental' || THOROUGH) return 'rewrite'
  const files = Array.isArray(g.changedFiles) ? g.changedFiles : g.paths
  const lines = typeof g.changedLines === 'number' ? g.changedLines : Infinity
  const churn = typeof g.addedOrDeleted === 'number' ? g.addedOrDeleted : 1
  if (lines <= SKIP_MAX_LINES && churn === 0) return 'skip'
  // Small churn stays delta: a new file gets fresh notes, a deleted one gets its
  // notes superseded — neither needs the whole group re-read.
  if (churn <= 2 && g.fileCount > 0 && files.length / g.fileCount <= DELTA_MAX_FRACTION) return 'delta'
  return 'rewrite'
}
const plan = setup.moduleGroups.map((g) => ({ g, tier: classify(g) }))
const carried = plan.filter((p) => p.tier === 'skip').map((p) => p.g)
const toEnrich = plan.filter((p) => p.tier !== 'skip')
if (setup.mode === 'incremental')
  log(`update plan: ${carried.length} carried, ${toEnrich.filter((p) => p.tier === 'delta').length} delta, ${toEnrich.filter((p) => p.tier === 'rewrite').length} rewrite`)

let enriched = []
let unenriched = setup.moduleGroups
if (!NO_ENRICH) {
  phase('Enrich')
  const enrichResults = await pipeline(toEnrich, ({ g, tier }) =>
    agent(`Enrich one module group of the codebase graph. Loom access: ${LOOM}. ${CONSUMPTION_HINT} ${SCRATCH_GUARD}
GRAPH_NAME: ${setup.graphName}
PROJECT_PATH: ${setup.projectPath}
GROUP: ${JSON.stringify(g)}
MODE: ${setup.mode}
ENRICH_MODE: ${tier}${tier === 'delta' ? `
CHANGED_FILES: ${JSON.stringify(g.changedFiles || [])}
Delta mode: follow your agent definition's "Delta mode" section — supersede and rewrite ONLY the semantic notes citing CHANGED_FILES; notes grounded solely in unchanged files stand.` : ''}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`,
      tier === 'delta'
        ? { label: `enrich:${g.id} (delta)`, agentType: 'codebase-enricher', phase: 'Enrich', schema: ENRICH, model: 'sonnet', effort: 'medium' }
        : { label: `enrich:${g.id}`, agentType: 'codebase-enricher', phase: 'Enrich', schema: ENRICH }))
  enriched = enrichResults.filter(Boolean)
  for (const r of enriched) {
    if (r.verification.entitiesAttempted > 0 && r.verification.entitiesVerified === 0)
      throw new Error(`Group ${r.groupId}: ${r.verification.entitiesAttempted} entities attempted, ZERO verified — Loom write path failing silently (check the loom CLI, FalkorDB, and graph "${setup.graphName}"). Halting.`)
  }
  unenriched = toEnrich.map((p) => p.g).filter((g) => !enriched.some((r) => r.groupId === g.id))
  log(`enriched ${enriched.length}/${toEnrich.length} groups${unenriched.length ? ` (unenriched: ${unenriched.map((g) => g.label).join(', ')})` : ''}${carried.length ? `; carried unchanged: ${carried.length}` : ''}`)
} else {
  log(`--no-enrich: skipping semantic enrichment, ${setup.moduleGroups.length} groups left structural-only`)
}

// ===== Phase 1.5: Embed (one pass over everything created — extraction, and enrichment if it ran) =====
phase('Embed')
await agent(`Embed every unembedded entity in the codebase graph. Loom access: ${LOOM}. ${SCRATCH_GUARD}
Run \`loom embed-entities '{"graph": "${setup.graphName}"}'\` — this can take several minutes on a first run
(one-time embedder model download plus one embedding per entity); run it with a long Bash timeout (600000 ms),
never the default. Report nothing else; return an empty object.`,
  { label: 'embed', phase: 'Embed', schema: { type: 'object', additionalProperties: true }, model: 'haiku', effort: 'low' })

// ===== Phase 2: Cartograph =====
// Refresh mode (incremental, few groups touched): patch the existing map in place
// on a cheaper model instead of re-deriving every section from a whole-graph read.
phase('Cartograph')
const REFRESH = setup.mode === 'incremental' && !THOROUGH && !NO_ENRICH && enriched.length <= REFRESH_MAX_GROUPS
const map = await agent(`Write the architecture map deliverables. Loom access: ${LOOM}. ${CONSUMPTION_HINT} ${SCRATCH_GUARD}
GRAPH_NAME: ${setup.graphName}
PROJECT_PATH: ${setup.projectPath}
PORTABILITY (hard requirement): PROJECT_PATH and OUTPUT_DIR are for your own file access ONLY — the deliverables are committed to the repo and must contain no machine-specific absolute path (no /Users/..., no ~/...). In map-manifest.json write "projectPath": "." exactly; in ARCHITECTURE-MAP.md write the re-run instruction as \`/map-codebase <repo-root>\`; in QUERYING.md write the loom fallback prefix with a <path-to-your-loom-checkout> placeholder. Before returning, grep your four written outputs for the PROJECT_PATH string and rewrite any hit.
OUTPUT_DIR: ${OUTPUT || `${setup.projectPath}/docs/architecture/`}
HEAD_COMMIT: ${setup.headCommit}
MODE: ${setup.mode}${NO_ENRICH ? ' (structural-only: --no-enrich skipped the Enrich phase, so there is no semantic layer this run)' : ''}
CARTOGRAPH_MODE: ${REFRESH ? 'refresh — follow your agent definition\'s "Refresh mode" section: patch the existing OUTPUT_DIR deliverables in place for the groups below instead of re-deriving every section' : 'full'}
GROUPS_ENRICHED: ${JSON.stringify(enriched.map((r) => (setup.moduleGroups.find((g) => g.id === r.groupId) || {}).label || r.groupId))}
GROUPS_UNENRICHED: ${JSON.stringify(unenriched.map((g) => g.label))}
GROUPS_CARRIED: ${JSON.stringify(carried.map((g) => g.label))}
DIRTY_TREE: ${!!setup.dirtyTree}
SKIPPED_FILES: ${setup.skippedFiles || 0}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`,
  REFRESH
    ? { label: 'cartograph (refresh)', agentType: 'codebase-cartographer', phase: 'Cartograph', schema: MAP, model: 'sonnet' }
    : { label: 'cartograph', agentType: 'codebase-cartographer', phase: 'Cartograph', schema: MAP })

return {
  graphName: setup.graphName, mapPath: map.mapPath, vizPath: map.vizPath, queryingDoc: map.queryingDoc,
  mode: setup.mode, groupsTotal: setup.moduleGroups.length, groupsEnriched: enriched.length,
  groupsCarried: carried.length, cartographMode: REFRESH ? 'refresh' : 'full',
  keyFindings: map.keyFindings,
}
