export const meta = {
  name: 'map-codebase',
  description: 'Architecture map of a codebase: tree-sitter extraction → parallel semantic enrichment per module group → analysis + written map + visualization. Incremental re-runs via update-codebase.',
  whenToUse: 'Invoked by the /map-codebase skill to map a repo into a Loom graph with an ARCHITECTURE-MAP.md and codebase-map.html.',
  phases: [
    { title: 'Setup', detail: 'extract or incrementally update the graph; build module groups' },
    { title: 'Enrich', detail: 'parallel semantic enrichment per module group' },
    { title: 'Cartograph', detail: 'analysis + map document + visualization + manifest' },
  ],
}

const A = (typeof args === 'string' ? JSON.parse(args) : (args || {}))
const PATH = A.path || '.'
const GRAPH = A.graph || null          // null → setup derives codebase-{slug}
const OUTPUT = A.output || null        // null → {PATH}/docs/architecture/
const FULL = !!A.full
const INCLUDE = A.include || null      // array of globs or null
const NO_TESTS = !!A.noTests
// Loom access is CLI-only. LOOM is the instruction string injected into agent prompts.
const LOOM = 'run the Loom CLI over Bash as `loom <command> \'<json>\'` — kebab-case commands, camelCase JSON fields plus a `graph` field on every call. Two CLI-enforced invariants: create-relation REQUIRES polarity ("+"/"-" for causal types, null otherwise), strength (weak|moderate|strong|foundational), and evidence (string or null); and embedding is a separate step — run `loom embed-entities \'{"graph": "<GRAPH_NAME>"}\'` after each creation batch. If `loom` is not on PATH, prefix each call with `uv run --directory "$LOOM_DIR"`, where LOOM_DIR is the Loom checkout (default ~/Dropbox/Development/the-loom). There is no MCP server — do not look for the-loom MCP tools'

// ---- schemas (canonical defs in .claude/references/map-codebase-schemas.md) ----
const SETUP = { type: 'object', additionalProperties: true,
  required: ['graphName', 'projectPath', 'mode', 'headCommit', 'moduleGroups'],
  properties: { graphName: { type: 'string' }, projectPath: { type: 'string' },
    mode: { enum: ['full', 'incremental'] }, headCommit: { type: 'string' },
    moduleGroups: { type: 'array', items: { type: 'object', required: ['id', 'label', 'paths', 'fileCount'],
      properties: { id: { type: 'string' }, label: { type: 'string' },
        paths: { type: 'array', items: { type: 'string' } }, fileCount: { type: 'integer', minimum: 0 } } } },
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
const MAP = { type: 'object', additionalProperties: true, required: ['mapPath', 'vizPath', 'stats', 'keyFindings'],
  properties: { mapPath: { type: 'string' }, vizPath: { type: 'string' },
    stats: { type: 'object', required: ['entities', 'relations', 'cycles', 'hubs'],
      properties: { entities: { type: 'integer' }, relations: { type: 'integer' },
        cycles: { type: 'integer' }, hubs: { type: 'integer' } } },
    keyFindings: { type: 'array', items: { type: 'string' }, maxItems: 10 } } }

// ===== Phase 0: Setup =====
phase('Setup')
const setup = await agent(`Initialize a map-codebase run. Loom access: ${LOOM}.
1. Resolve TARGET PATH "${PATH}" to an absolute path (pwd-relative if not absolute); derive slug from its dirname; GRAPH_NAME = ${GRAPH ? `"${GRAPH}"` : '"codebase-{slug}"'}; OUTPUT_DIR = ${OUTPUT ? `"${OUTPUT}"` : '"{abs path}/docs/architecture/"'} (mkdir -p it). Record \`git -C <path> rev-parse HEAD\` and whether \`git -C <path> status --porcelain\` is non-empty (dirtyTree).
2. Fail fast: \`loom graph-stats '{}'\` must succeed — if it errors on connection, throw with the remediation line "docker compose up -d falkordb".
3. Mode: if OUTPUT_DIR/map-manifest.json exists AND its graphName's graph exists AND ${FULL} is false → mode "incremental": run loom update-codebase '{"projectPath": "<abs>", "graphName": "<GRAPH_NAME>", "gitRef": "<manifest.commit>"${NO_TESTS ? ', "includeTests": false' : ''}}'. Otherwise mode "full": loom create-graph '{"name": "<GRAPH_NAME>"}' (ignore already-exists), then loom extract-codebase '{"projectPath": "<abs>", "graph": "<GRAPH_NAME>"${NO_TESTS ? ', "includeTests": false' : ''}${INCLUDE ? `, "include": ${JSON.stringify(INCLUDE)}` : ''}}'. Note skipped-file count from the output.
4. loom embed-entities '{"graph": "<GRAPH_NAME>"}'.
5. Module groups: loom list-entities '{"entityType": "system", "graph": "<GRAPH_NAME>"}' → group file paths by top-level directory; cap 25 files per group (split oversized dirs by subdirectory, then alphabetical chunks); fold dirs with <3 files into their parent. Group id = kebab slug of the dir path. In incremental mode, keep ONLY groups containing files changed in the update-codebase diff.
Return the Setup contract object.`,
  { label: 'setup', phase: 'Setup', schema: SETUP })
log(`map-codebase ${setup.mode} → graph ${setup.graphName}, ${setup.moduleGroups.length} groups @ ${setup.headCommit.slice(0, 8)}`)

// ===== Phase 1: Enrich (parallel per module group) =====
phase('Enrich')
const enrichResults = await pipeline(setup.moduleGroups, (g) =>
  agent(`Enrich one module group of the codebase graph. Loom access: ${LOOM}.
GRAPH_NAME: ${setup.graphName}
PROJECT_PATH: ${setup.projectPath}
GROUP: ${JSON.stringify(g)}
MODE: ${setup.mode}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`,
    { label: `enrich:${g.id}`, agentType: 'codebase-enricher', phase: 'Enrich', schema: ENRICH }))
const enriched = enrichResults.filter(Boolean)
for (const r of enriched) {
  if (r.verification.entitiesAttempted > 0 && r.verification.entitiesVerified === 0)
    throw new Error(`Group ${r.groupId}: ${r.verification.entitiesAttempted} entities attempted, ZERO verified — Loom write path failing silently (check the loom CLI, FalkorDB, and graph "${setup.graphName}"). Halting.`)
}
const unenriched = setup.moduleGroups.filter((g) => !enriched.some((r) => r.groupId === g.id))
log(`enriched ${enriched.length}/${setup.moduleGroups.length} groups${unenriched.length ? ` (unenriched: ${unenriched.map((g) => g.label).join(', ')})` : ''}`)

// ===== Phase 2: Cartograph =====
phase('Cartograph')
const map = await agent(`Write the architecture map deliverables. Loom access: ${LOOM}.
GRAPH_NAME: ${setup.graphName}
PROJECT_PATH: ${setup.projectPath}
OUTPUT_DIR: ${OUTPUT || `${setup.projectPath}/docs/architecture/`}
HEAD_COMMIT: ${setup.headCommit}
MODE: ${setup.mode}
GROUPS_ENRICHED: ${JSON.stringify(enriched.map((r) => r.groupId))}
GROUPS_UNENRICHED: ${JSON.stringify(unenriched.map((g) => g.label))}
DIRTY_TREE: ${!!setup.dirtyTree}
SKIPPED_FILES: ${setup.skippedFiles || 0}
Execute exactly per your agent definition. Emit ONLY your Structured Output Contract object as your final message.`,
  { label: 'cartograph', agentType: 'codebase-cartographer', phase: 'Cartograph', schema: MAP })

return {
  graphName: setup.graphName, mapPath: map.mapPath, vizPath: map.vizPath,
  mode: setup.mode, groupsTotal: setup.moduleGroups.length, groupsEnriched: enriched.length,
  keyFindings: map.keyFindings,
}
