---
description: Run a standalone, read-only Loom expedition over an existing knowledge graph to surface emergent theories — implicit causal chains, feedback dynamics, and surprising long-range connections that no individual research step explicitly created. Use for "/loom-expedition GRAPH", "what does this graph imply?", "find emergent theories / surprising connections in <graph>", "run an expedition", or as the expedition phase invoked from hyper-research.
allowed-tools: Read, Write, Bash, Grep, Glob
---

# Loom Expedition

Excavate what an accumulated graph *implies* rather than what any run explicitly
wrote: implicit causal chains, emergent feedback dynamics, and long-range
connections that only exist because many independent findings landed in one
structure. Research adds knowledge; an expedition reads between it.

Two hard constraints, and why:

1. **Read-only on the graph.** An expedition reports what the structure implies.
   Writing conclusions back would let the next expedition discover its own
   output — a feedback loop of manufactured insight. Every `loom` call here is
   a read; create nothing, modify nothing, delete nothing.
2. **Autonomous.** This runs unattended (often as a phase inside a larger
   pipeline). Never ask the user questions; make reasonable assumptions,
   record them in the findings, and continue. A partial report with honest
   gaps beats a stalled run.

## Invoke

1. Parse `$ARGUMENTS`: **GRAPH_NAME** = first non-flag token (required).
   Optional flags: `--seed TOPIC` (initial search seed; otherwise centrality
   picks the starting point), `--session-folder PATH` (findings destination;
   default `DeepResearch/expeditions/expedition-{GRAPH_NAME}-{date}`),
   `--iteration N` (findings filename suffix; default 1).
2. Ensure `{SESSION_FOLDER}/findings/` exists.
3. Execute the expedition below, then report the completion summary.

```
/loom-expedition computational-invention
/loom-expedition externalized-rl --seed "Externalized RL"
/loom-expedition hyper-2026-02-12-rl-001 --seed "Reinforcement Learning" --session-folder "DeepResearch/hyper-sessions/hyper-2026-02-12-rl-001"
```

## The expedition

Every graph operation is `loom <command> '<json>'` over Bash with a `"graph"`
field — kebab-case commands, camelCase payloads, no MCP server.

### 1. Reconnaissance

```bash
loom graph-stats '{"graph": "GRAPH_NAME"}'
loom detect-loops '{"maxSize": 6, "graph": "GRAPH_NAME"}'
loom list-bridges '{"from_graph": "GRAPH_NAME"}'
```

**Early exit:** a graph under ~20 entities is too sparse for structure to have
accumulated anything implicit. Write minimal findings (`discoveries: []`,
status `complete`), report "too sparse for meaningful expedition analysis",
and exit successfully — this is the normal outcome for early iterations, not
a failure.

### 2. Thread selection

Pick the most interesting thread to pull. With `--seed`, first search
`loom list-entities '{"query": "SEED_TOPIC", "graph": "GRAPH_NAME"}'`.
Priority order, and the reasoning: structure that *participates* in dynamics
beats structure that is merely large —

1. a topic-relevant entity that also sits in a loop or bridge,
2. the entity appearing in the most feedback loops,
3. a bridge entity connecting separate knowledge domains,
4. fallback: highest degree via
   `loom analyze-centrality '{"algorithm": "degree", "limit": 5, "graph": "GRAPH_NAME"}'`.

Record THREAD_SEED (entity id) and the selection reason — the reason is part
of the findings, not private deliberation.

### 3. Influence mapping

```bash
loom semiring-distances '{"source": "THREAD_SEED", "semiring": "viterbi", "graph": "GRAPH_NAME"}'
loom get-neighbors '{"entityId": "THREAD_SEED", "graph": "GRAPH_NAME"}'
loom read-entity '{"id": "THREAD_SEED", "graph": "GRAPH_NAME"}'
```

The Viterbi semiring gives confidence-weighted causal reach: how far belief
propagates from the seed and how much survives the trip. From the output,
read the 3–5 most *distant* reachable entities and hunt for the surprising
long-range connection — two entities causally linked whose relationship is
not obvious from names and observations alone. Distance is the point: nearby
connections were probably authored deliberately; distant ones emerged.
Record THEORY_SOURCE and THEORY_TARGET.

### 4. Path analysis

For the surprising connection, characterize the chain three ways:

```bash
loom semiring-bottleneck '{"source": "THEORY_SOURCE", "target": "THEORY_TARGET", "semiring": "capacity", "graph": "GRAPH_NAME"}'
loom semiring-traverse '{"source": "THEORY_SOURCE", "target": "THEORY_TARGET", "semiring": "counting", "graph": "GRAPH_NAME"}'
loom find-shortest-path '{"source": "THEORY_SOURCE", "target": "THEORY_TARGET", "graph": "GRAPH_NAME"}'
```

Bottleneck names the weakest link (where the theory would break), counting
says whether the connection is one fragile thread or a braid of independent
paths, and the shortest path is the chain to actually read. Read every entity
along it — the theory's substance lives in those observations, not in the
topology.

### 5. Context check

```bash
loom cross-type-query '{"source": "THREAD_SEED", "relationTypes": ["causes", "enables", "inhibits", "amplifies", "dampens", "requires"], "graph": "GRAPH_NAME"}'
loom semantic-neighbors '{"entityId": "THEORY_TARGET", "limit": 5, "graph": "GRAPH_NAME"}'
```

The pure causal network guards against a theory built on structural filler
(`related_to` chains), and the conclusion's semantic neighbors reveal whether
the graph already holds adjacent ideas the theory should acknowledge.

### 6. Write findings

Two artifacts in `{SESSION_FOLDER}/findings/`:

- `expedition-iteration-{ITERATION}.json` — machine-readable:
  `status`, `iteration`, `graphStats`, `loopsFound` (with size and
  reinforcing/balancing type), `bridgesFound`, `threadSeed` (with rationale),
  `emergentTheory` (the analysis, or `"not found"` — absence is a valid
  result), and `discoveries` (every notable finding with a plain-language
  summary).
- `expedition-report-{ITERATION}.md` — the human-readable narrative: graph
  overview, thread rationale, the emergent theory told plainly, all
  discoveries, structural observations.

### 7. Report completion

```
Loom Expedition Complete
========================
Graph:       {GRAPH_NAME}
Seed:        {SEED_TOPIC or "centrality-based"}
Entities:    {count}    Loops: {count}    Bridges: {count}
Discoveries: {count}    Theory: {found / not found}
Report:      {report path}
Findings:    {findings JSON path}
```

## Degradation

Every failure is non-blocking — log what was attempted, keep whatever data
exists, and say so in the findings. A partial expedition report is better
than no report. Specifically: semiring failures skip steps 3–4 and report
topology only; zero loops just means centrality-based selection (bridges and
long-range connections can still surface a theory); a sparse graph exits
early as described above.

> **Home:** this skill lives in the-loom repository beside the research
> pipeline it complements (`.claude/agents/research-expedition.md` is the
> in-pipeline variant; this is the standalone one). Run from the repo root
> with FalkorDB up (`docker compose up -d falkordb`); prefix `loom` calls
> with `uv run` if it is not on PATH. Do not keep copies in `~/.claude/` —
> duplicates shadow and drift.
