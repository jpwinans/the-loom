---
description: Meta-research orchestrator — runs the hyper-research Workflow: comprehend a context document, extract independent questions, run deep-research PER QUESTION IN PARALLEL on one shared Loom graph, consolidate, then synthesize a cross-cutting report. Use for "/hyper-research DOC", investigating several questions from a document at once, or turning a research synthesis into the next round of parallel deep-research.
allowed-tools: Workflow, Bash, Read, Write, Glob, Grep
---

# Hyper Research

Runs the **hyper-research Workflow** at `.claude/workflows/hyper-research.js` — comprehend the context doc → parallel graph exploration → extract independent questions → **parallel per-question deep-research** (each via the deep-research workflow, onto ONE shared graph with per-question provenance tags) → post-barrier consolidation merge → expedition + cross-session discovery → cross-cutting synthesis → ingest the report.

## Invoke

1. Parse `$ARGUMENTS`: **contextDoc** = path to the context document (required). Optional: `--topic T`, `--graph NAME`, `--output PATH`, `--category NAME` (Loom doc-store category for the final report; default `research`).
2. Call the Workflow tool (valid opt-in via this skill):
   ```
   Workflow({ name: "hyper-research", args: { contextDoc: "<path>", topic: <T if --topic else omit>, graph: <NAME if --graph else omit>, output: <PATH if --output else omit>, category: <NAME if --category else omit> } })
   ```
3. Runs in the background; notifies on completion. Report: `sessionId`, `graphName`, `reportPath`, the extracted question ids, `completedQuestions`/`failedQuestions`, and the cross-cutting themes.

**Portable:** the session, report (`research/reports/{slug}-{date}.md`), and per-question sessions are written relative to the launch directory — no vault or identity coupling; the report is ingested into the Loom doc store under a generic `research` category (override with `--category`). **Loom-via-CLI:** every graph operation runs through `loom <command> '<json>'` over Bash — there is no MCP server. **Shared-graph** is safe for concurrent writes (duplicates coexist non-fatally and are merged by the consolidation barrier). **Concurrency note:** the per-question fan-out shares the Workflow cap with each run's internal agents, so wall-clock speedup is sub-linear in the number of questions — many parallel runs queue against the cap rather than all running at once.

> **Home:** this pipeline lives in the-loom repository (see the deep-research skill's Home note) — run `/hyper-research` from the repo root. `hyper-research` invokes `deep-research` **by name**, resolved from this repo's `.claude/workflows/`. Requires FalkorDB running. Do not keep copies in `~/.claude/` — duplicates shadow and drift.
