# Examples

Worked examples of building on The Loom. Each folder documents one of the
Claude Code agent skills this repository ships — what it does, how to invoke
it, and (the part that matters if you're building your own tooling) exactly
**how it drives the Loom's JSON-in/JSON-out CLI** to build, query, and maintain
a knowledge graph.

| Example | One line | Guide |
| --- | --- | --- |
| **deep-research** | Autonomous multi-iteration research on one question, built into a graph of sources, evidence, and claims with calibrated confidence | [deep-research/README.md](deep-research/README.md) |
| **hyper-research** | The meta-orchestrator: independent questions extracted from a document, deep-research run per question in parallel onto one shared graph, then a cross-cutting synthesis | [hyper-research/README.md](hyper-research/README.md) |
| **map-codebase** | An explained architecture map of any repository — structure from tree-sitter, meaning from an LLM enrichment pass, kept current by diff-scaled incremental updates | [map-codebase/README.md](map-codebase/README.md) |
| **loom-expedition** | A read-only discovery pass over an existing graph — surfacing the emergent theories and surprising long-range connections the accumulated structure implies | [loom-expedition/README.md](loom-expedition/README.md) |

## Where the implementation lives

These folders are **guides**, not the skills themselves. The runnable assets
live under [`.claude/`](../.claude/README.md) — skills (`.claude/skills/`),
deterministic workflow scripts (`.claude/workflows/`), subagent definitions
(`.claude/agents/`), and the structured-output contracts
(`.claude/references/`) — because that is where Claude Code resolves them.
Keep them there; these guides link into them.

## Shared prerequisites

Every example assumes you are at the repo root with the store running:

```bash
uv sync                          # install the loom CLI into the venv
docker compose up -d falkordb    # the single store — nothing works without it
uv run loom graph-stats '{}'     # verify: a connection error means FalkorDB is down
```

The research and mapping examples run as background multi-agent Workflows
launched by their slash command and notify on completion; the expedition runs
inline and synchronously, since it only reads. All of them drive the Loom
exclusively through `loom <command> '<json>'` over Bash (there is no MCP
server). Two CLI invariants every example respects — and yours should too:

1. `create-relation` **requires** `polarity` (`"+"`/`"-"` for causal types,
   `null` otherwise), `strength` (`weak|moderate|strong|foundational`), and
   `evidence` (a one-line justification, or `null`).
2. **Embedding is deliberate, not a side effect** — after each creation batch,
   run `loom embed-entities '{"graph": "<name>"}'` or semantic search cannot
   see the new entities.
