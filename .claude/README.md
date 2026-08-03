# .claude/ — Project Agent Assets

This directory is the **single source of truth** for the autonomous research pipeline
and the Loom skill. Run everything from the repo root so project-level skills,
workflows, and agents resolve together and `uv run loom` hits this checkout.

## Layout

```
.claude/
├── skills/
│   ├── the-loom/                  # the Loom's 156-command CLI: architecture, data model,
│   │                              #   tool catalog, workflow patterns
│   ├── deep-research/             # /deep-research TOPIC — single-question autonomous research
│   └── hyper-research/            # /hyper-research DOC — parallel multi-question meta-research
├── workflows/
│   ├── deep-research.js           # deterministic orchestration: orient → quality-gated loop → finalize
│   └── hyper-research.js          # comprehend → extract questions → parallel deep-research → synthesize
├── agents/
│   └── research-*.md              # the 8 pipeline agents (orientation, research, synthesis,
│                                  #   consolidation, expedition, red-team, quality, documentation)
├── references/
│   └── research-schemas.md        # canonical structured-output contracts for agent handoffs
└── commands/
    └── loom-expedition.md         # /loom-expedition GRAPH — standalone emergent-theory expedition
```

Everything drives the Loom exclusively through its JSON-in/JSON-out CLI
(`loom <command> '<json>'`). There is no MCP server in this repository.

## Prerequisites

```bash
uv sync                        # install the loom CLI into the venv
docker compose up -d falkordb  # the store — nothing works without it
uv run loom graph-stats        # verify: errors here mean FalkorDB is down
```

Without FalkorDB running, agents may appear to succeed — findings files still get
written — while nothing persists to the graph. `loom graph-stats` failing on
connection is the tell.

Connection settings resolve via `theloom/config.py` in the order *CLI flags >
environment > `~/.loom/config.json` > defaults*; the environment variables are
`GRAPH_HOST`, `GRAPH_PORT`, and `DEFAULT_GRAPH`.

## Usage

```bash
/deep-research "Systems thinking and organizational change"
/hyper-research "research/reports/some-synthesis.md" --graph my-graph
/loom-expedition my-graph --seed "Systems thinking"
```

Research outputs land under `research/sessions/{id}/` (deep) and
`research/hyper-sessions/{id}/` + `research/reports/` (hyper), relative to the
launch directory.

## Two invariants the CLI enforces

The pipeline's documentation repeats these because getting either wrong fails
silently or loudly at runtime:

1. `create-relation` **requires** `polarity` (`"+"`/`"-"` for causal types, `null`
   otherwise), `strength` (`weak|moderate|strong|foundational`), and `evidence`
   (string or `null`).
2. Embedding is **not** automatic on create — run
   `loom embed-entities '{"graph": "<name>"}'` after each creation batch or
   semantic search cannot see the new entities.

## Do not duplicate

Do not keep copies of these skills, workflows, or agents in `~/.claude/` —
project-level and user-level copies shadow each other and drift. Edit them here,
under version control.
