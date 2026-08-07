# Deep Research

Autonomous, multi-iteration research on a single question that builds its
findings into a Loom graph instead of a pile of prose: sources, evidence, and
claims as typed entities; agreement and conflict as `supports` / `contradicts`
relations; every record carrying calibrated confidence and provenance.

## Usage

```
/deep-research "How do organizations successfully unwind technical debt?"
/deep-research "TOPIC" --graph existing-graph    # accumulate into an existing graph
/deep-research "TOPIC" --label my-label          # name the session folder
```

The skill launches the deterministic workflow at
[`.claude/workflows/deep-research.js`](../../.claude/workflows/deep-research.js)
in the background and reports on completion: the session id, graph name,
question classification, iterations run, final quality score, and the
`research_session` entity id.

**Pipeline shape:** orientation (clarify the question, seed the graph) → a
quality-gated loop of research → synthesis → verification → consolidation,
with adversarial red-team and expedition passes running concurrently → 
documentation → finalize. Eight specialized subagents
(`.claude/agents/research-*.md`) hand off through schema-validated structured
outputs (`.claude/references/research-schemas.md`). Session artifacts land
under `research/sessions/{id}/` relative to the launch directory.

> **Set expectations before you launch.** This is not a chat feature. A run
> executes a dozen-plus subagent turns per quality-gated iteration, and cost
> scales with the iteration budget the question's classification sets. For
> example, one session may take a couple of hours and a few hundred thousand
> tokens to build a hundred-entity graph with full provenance from a single
> iteration, while a narrow factual question finishes far faster. It runs in
> the background and notifies on completion: launch it and go do something
> else.

## Why not just an agent with web search?

Most "deep research" agents search, read, and hand you a report. The report
is the end of the line: the reasoning lives in prose, the sources live in
footnotes, and the next question starts from zero. Here the report is a
byproduct and the graph is the asset:

- **Every claim is auditable after the run.** "Which claims are contested?"
  and "what rests on a single source?" are queries, not a re-read of the
  report.
- **Beliefs update; they don't just accumulate.** The red-team pass attacks
  exactly what the graph exposes as weak, and when a source fails scrutiny,
  credit propagation weakens every conclusion built on it.
- **Sessions compound.** Re-run with `--graph` onto the same graph and new
  research lands beside the old with session provenance;
  `cross-session-contradictions` catches the disagreements nobody noticed.
- **Follow-ups don't re-research.** `synthesize`, `hybrid-search`, and
  `provenance-audit` answer from the graph in seconds, long after the run.

## How it uses The Loom

This pipeline is a worked example of the Loom's **epistemic layer** doing real
work:

- **Typed knowledge, not notes.** Findings enter as `source`, `evidence`,
  `claim`, `question`, and `hypothesis` entities; synthesis products as
  `pattern`, `insight`, `tension`, and `convergence`. The graph *is* the
  research state — every later stage reads it back through `list-entities`,
  `hybrid-search`, and `graph-stats` rather than passing documents around.
- **Epistemic edges carry the argument.** Evidence `supports` or `contradicts`
  claims; contested claims surface via `loom contested-claims`, thin ones via
  `loom needs-evidence`, fragile ones via `loom single-source-claims`. The
  red-team agent attacks exactly what those queries expose.
- **Confidence is calibrated and propagates.** Every entity carries a
  confidence score plus a basis (`multiple_sources` down to `speculation`).
  When verification changes a belief, `loom propagate-credit` flows the
  adjustment along epistemic chains — damped, fan-in-diluted, sign-flipped
  through contradictions.
- **Provenance is first-class.** Records carry session provenance, so
  `loom provenance-chain`, `loom claims-from-source`, and
  `loom session-changelog` can answer "where did this belief come from and
  what changed this session" after the run.
- **Verification is hard.** An iteration that attempts entity writes and
  verifies zero of them halts the workflow — a silently failing write path is
  treated as a defect, never continued past.
- **Embedding is explicit.** Each creation batch ends with
  `loom embed-entities` so the next iteration's semantic search and duplicate
  detection can see this iteration's work.

## After a run

The graph persists — the report is the byproduct, the graph is the asset:

```bash
uv run loom graph-stats '{"graph": "<graphName>"}'
uv run loom contested-claims '{"graph": "<graphName>"}'
uv run loom most-certain '{"limit": 10, "graph": "<graphName>"}'
uv run loom synthesize '{"query": "what did we learn?", "graph": "<graphName>"}'
uv run loom visualize '{"graph": "<graphName>", "theme": "dark"}'
```

Run `/deep-research` again with `--graph <graphName>` to deepen the same
graph, or hand it to [`/loom-expedition`](../loom-expedition/README.md) to
hunt for emergent theories in the accumulated structure.
