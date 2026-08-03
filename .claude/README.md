# .claude/ — Project Agent Assets

What lives here, and what deliberately does not.

## In this repository

- **`skills/the-loom/`** — the skill that teaches Claude the Loom's 156-command
  CLI: architecture, data model, tool catalog, and multi-step workflows. This is
  the canonical guide for driving the Loom from an agent.
- **`commands/loom-expedition.md`** — `/loom-expedition GRAPH [--seed TOPIC]`:
  a standalone expedition over an accumulated graph (reconnaissance → thread
  selection → influence mapping → path analysis → emergent-theory report).

Everything here drives the Loom exclusively through its JSON-in/JSON-out CLI
(`loom <command> '<json>'`). There is no MCP server in this repository.

## Prerequisites

```bash
uv sync                        # install the loom CLI into the venv
docker compose up -d falkordb  # the store — nothing works without it
uv run loom graph-stats        # verify: errors here mean FalkorDB is down
```

Without FalkorDB running, agents may appear to succeed — findings files still
get written — while nothing persists to the graph. `loom graph-stats` failing
on connection is the tell.

Connection settings resolve via `theloom/config.py` in the order *CLI flags >
environment > `~/.loom/config.json` > defaults*; the environment variables are
`GRAPH_HOST`, `GRAPH_PORT`, and `DEFAULT_GRAPH`.

## The research pipeline lives at user level

The autonomous research harness (`/deep-research`, `/hyper-research`) is **not**
part of this repository. Its portable unit is `~/.claude/` as a whole:

```
~/.claude/skills/deep-research/     ~/.claude/skills/hyper-research/
~/.claude/workflows/deep-research.js  ~/.claude/workflows/hyper-research.js
~/.claude/agents/research-*.md      (8 agents)
~/.claude/references/research-schemas.md
```

Keeping a second copy of those agents in this repo caused silent shadowing —
project-level agents take precedence, so in-repo runs used stale copies. The
copies were removed; the user-level set is the single source of truth. This
repo only needs to provide what the pipeline consumes: a working `loom` CLI
and a running FalkorDB.
