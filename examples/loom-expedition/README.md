# Loom Expedition

A read-only discovery pass over a graph you already have. Research adds
knowledge; an expedition reads *between* it — hunting the implicit causal
chains, feedback dynamics, and surprising long-range connections that only
exist because many independent findings accumulated in one structure. No
individual research step wrote them; the structure implies them.

## Usage

```
/loom-expedition GRAPH_NAME
/loom-expedition GRAPH_NAME --seed "topic to start from"
/loom-expedition GRAPH_NAME --session-folder PATH --iteration N
```

Unlike the other examples, this is not a background multi-agent workflow: the
invoking session executes the expedition directly
([`.claude/skills/loom-expedition/SKILL.md`](../../.claude/skills/loom-expedition/SKILL.md)),
so it runs synchronously and finishes in minutes — it only reads. Findings
land as a machine-readable JSON plus a human-readable report under
`DeepResearch/expeditions/` (or the `--session-folder` you point it at, which
is how hyper-research embeds it as a phase).

Two constraints define it:

- **Read-only, and that's load-bearing.** An expedition that wrote its
  conclusions into the graph would let the next expedition discover its own
  output — manufactured insight in a feedback loop. It reports; it never
  writes to the graph.
- **Absence is a valid result.** A sparse graph exits early ("too sparse for
  meaningful expedition analysis"), and `emergentTheory: "not found"` is an
  honest outcome, not a failure.

## How it uses The Loom

This is the smallest worked example of the Loom's *reasoning* surface — no
LLM extraction, no writes, just reads that a plain vector store could not
answer:

- **Structure picks the thread.** `detect-loops`, `list-bridges`, and
  centrality choose where to dig: an entity that participates in feedback
  dynamics beats one that is merely well-connected.
- **Semirings characterize what recall can't.** `semiring-distances` with the
  Viterbi algebra gives confidence-weighted causal reach (how far belief
  propagates and how much survives the trip); `semiring-bottleneck` names the
  weakest link where a theory would break; `semiring-traverse` with the
  counting algebra says whether a connection is one fragile thread or a braid
  of independent paths.
- **Distance implies emergence.** The hunt targets the most *distant*
  reachable entities, because nearby connections were probably authored
  deliberately — the far ones nobody wrote are where emergent theories live.
- **The causal-only pass keeps theories honest.** `cross-type-query`
  restricted to the six causal relation types guards against a "theory"
  strung together from structural filler, and `semantic-neighbors` checks
  whether the graph already holds adjacent ideas the theory should
  acknowledge.

## After a run

The natural pairing is with the research pipeline: accumulate structure with
[deep-research](../deep-research/README.md) (or several runs onto one graph),
then send an expedition through it. Anything the expedition surfaces can be
verified the usual way — `find-shortest-path` to walk the chain,
`provenance-audit` on its endpoints, or a targeted deep-research run on the
theory as a new question.
