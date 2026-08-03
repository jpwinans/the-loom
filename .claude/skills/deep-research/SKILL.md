---
description: Autonomous deep research on one question — runs the deep-research Workflow (orientation → quality-gated research loop with parallel red-team/expedition → documentation → finalize) into a Loom graph. Use for "/deep-research TOPIC", "research X deeply", autonomous multi-iteration investigation that builds a knowledge graph.
allowed-tools: Workflow, Bash, Read, Write, Glob, Grep
---

# Deep Research

Runs the **deep-research Workflow** at `.claude/workflows/deep-research.js` — deterministic JS orchestration that reuses the 8 `research-*` subagents via `agentType` (resolved from this repo's `.claude/agents/`), with schema-validated handoffs (`.claude/references/research-schemas.md`), a quality-gated iteration loop (research → synthesis → verify → consolidation, with red-team + expedition concurrent before the quality gate), and a `research_session` finalize.

## Invoke

1. Parse `$ARGUMENTS`: the **topic** is everything before any `--flag`. Optional: `--graph NAME` (accumulate into an existing Loom graph instead of a fresh one), `--label NAME`.
2. Call the Workflow tool — this is the valid opt-in (a skill instructing a Workflow run):
   ```
   Workflow({ name: "deep-research", args: { topic: "<topic>", graph: <NAME if --graph else omit>, sessionLabel: <label if --label else "deep"> } })
   ```
3. It runs in the background and notifies on completion. Report to the user: `sessionId`, `graphName`, `classification.type`, `iterations`, `finalScore`, `researchSessionEntityId`.

The workflow owns everything downstream — session folder, classification (Type A/B/C/D → iteration limits + red-team/checkpoint flags), the loop, documentation, finalize. **Portable:** outputs are written under `research/sessions/{id}/` relative to the directory Claude Code was launched from — no vault or identity coupling. **Loom-via-CLI:** the graph is built entirely through the Loom's JSON-in/JSON-out CLI (`loom <command> '<json>'`, kebab-case commands) run over Bash; there is no MCP server. If `loom` is not on `PATH`, agents prefix each call with `uv run --directory "$LOOM_DIR"` (`LOOM_DIR` = the Loom checkout, default `~/Dropbox/Development/the-loom`). **Verification-hard:** an iteration where entities were attempted but zero verified halts (Loom write path failing silently), rather than continuing silently.

> **Home:** this pipeline lives in the-loom repository — the skill (`.claude/skills/deep-research/`), the workflow (`.claude/workflows/deep-research.js`), the 8 agents (`.claude/agents/research-*.md`), and the schemas (`.claude/references/research-schemas.md`) are all repo-relative and version-controlled here; this checkout is the single source of truth. Run `/deep-research` from the repo root so the project-level skill, workflow, and agents resolve together and `uv run loom` hits this checkout. Requires FalkorDB running (`docker compose up -d falkordb`). Do not keep copies in `~/.claude/` — user-level and project-level copies shadow each other and drift.
