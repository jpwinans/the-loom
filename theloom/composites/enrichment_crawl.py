"""Enrichment Crawl composite.

Crawls the under-described frontier and proposes enrichment relations for it.
Four sections, each inside :func:`time_section`:

1. ``prioritize`` — rank entities by how thinly described they are (few
   observations, few relations) and keep the top ``maxNodes``. Deterministic,
   no embeddings required.
2. ``crawl`` — gather each frontier node's context through the existing read
   ops (``get-relations`` for its edges, ``semantic-neighbors`` for its
   embedding neighbourhood) and turn that context into candidate relations:
   *structural closure* (a node two hops away sharing neighbours, scored by
   neighbour-set Jaccard) and, when entity vectors exist, *semantic
   neighbours* (scored by similarity). Candidates are merged per unordered
   pair, gated by ``minConfidence`` and capped at ``maxCandidates`` per node.
3. ``enrich`` — ``dryRun`` (the default) reports what would be written;
   ``dryRun: false`` creates each surviving candidate through
   ``create-relation``, so every write is event-logged like any other
   mutation. ``enrichedCount`` counts relations actually created — never
   proposals.
4. ``summary`` — the counts plus a human-readable report.

**Boundary — CISC N-sample voting.** The original contract scored candidates
by ``numSamples``-way LLM self-consistency voting. That needs a provider, and
this build has no enrichment LLM path, so voting is *not* applied: candidates
are ranked deterministically by their structural/semantic confidence and the
``summary`` section reports ``voting.applied = false`` with ``samplesUsed:
0``. ``numSamples`` therefore multiplies no spend — it is echoed back as
``requestedSamples`` so a caller can see the request was understood and
declined, rather than silently honoured.

The relation type of a candidate is inferred from the graph's own habits: the
most frequent existing relation type between that ordered pair of entity types
(``related_to`` when the graph has no precedent). Nothing is invented that the
graph does not already say — and because the evidence behind a candidate
(neighbour-set Jaccard, embedding similarity) is *symmetric and directionless*,
inference is restricted to what such evidence can carry:

* **No causal types.** ``causes``/``enables``/``requires``/``inhibits``/
  ``amplifies``/``dampens`` carry a polarity the evidence does not supply, so
  they are never inferred even on a graph made entirely of them.
* **Same-type pairs stay symmetric.** When both endpoints share an entity type
  the crawl direction is an artefact of which node it reached first, so only
  the symmetric fallback ``related_to`` is allowed. A cross-type precedent is
  still honoured, since the frequency table is keyed by the *ordered* type pair
  and therefore already encodes the graph's own direction.

Sections degrade rather than lie: a failed section never lets its dependants
report clean zeros, and a semantic-neighbour failure loses only the semantic
half of the context — structural closure still applies.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.composites.framework import failed_section, run_composite, time_section
from theloom.exploration import embeddings_available
from theloom.graph.hydrate import LoomGraph, hydrate_graph
from theloom.model import CAUSAL_RELATION_TYPES
from theloom.operations.common import CommandInput
from theloom.operations.relations import (
    CreateRelationInput,
    GetRelationsInput,
    create_relation,
    get_relations,
)
from theloom.operations.semantic import SemanticNeighborsInput, semantic_neighbors
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

DEFAULT_MAX_NODES = 10
DEFAULT_MAX_CANDIDATES = 5
DEFAULT_NUM_SAMPLES = 1
DEFAULT_MIN_CONFIDENCE = 0.5
FALLBACK_RELATION_TYPE = "related_to"

# Types a symmetric, directionless candidate can never justify: every causal
# type carries a polarity the evidence does not supply.
_UNINFERABLE_RELATION_TYPES = frozenset(str(t) for t in CAUSAL_RELATION_TYPES)

# Weight split between the two under-description signals (observations, edges).
_OBSERVATION_WEIGHT = 0.5
_RELATION_WEIGHT = 0.5

_NO_EMBEDDINGS_REASON = (
    "no entity vectors in this graph — the embedding pipeline has not run, so "
    "semantic-neighbor context was skipped; structural closure still applies"
)
_UPSTREAM_FAILED = "Skipped: the {section} section failed, so this section has nothing to report"
_NO_VOTING_REASON = (
    "CISC N-sample voting needs an LLM provider and no enrichment LLM path exists in "
    "this build; candidates are ranked deterministically instead and no samples were spent"
)


class EnrichmentCrawlInput(CommandInput):
    max_nodes: int | None = Field(default=None, gt=0, alias="maxNodes")
    max_candidates: int | None = Field(default=None, gt=0, alias="maxCandidates")
    num_samples: int | None = Field(default=None, gt=0, alias="numSamples")
    min_confidence: float | None = Field(default=None, ge=0, le=1, alias="minConfidence")
    dry_run: bool | None = Field(default=None, alias="dryRun")
    graph: str | None = None


def _semantic_failure_reason(err: Exception) -> str:
    """Why semantic context stopped mid-crawl (structural closure still applies)."""
    detail = str(err) or err.__class__.__name__
    return (
        f"semantic-neighbor context failed and was skipped for the rest of the crawl "
        f"({detail}); structural closure still applies"
    )


def _priority_score(observation_count: int, relation_count: int) -> float:
    """How under-described a node is: 1 at zero observations and zero edges,
    decaying as either grows."""
    return _OBSERVATION_WEIGHT / (1 + observation_count) + _RELATION_WEIGHT / (1 + relation_count)


def _relation_type_frequencies(
    relations: list[Doc], type_by_id: dict[str, str]
) -> dict[str, dict[str, int]]:
    """``"fromType→toType" -> {relationType: count}`` over the live graph."""
    frequencies: dict[str, dict[str, int]] = {}
    for relation in relations:
        from_type = type_by_id.get(relation["from"])
        to_type = type_by_id.get(relation["to"])
        if from_type is None or to_type is None:
            continue
        bucket = frequencies.setdefault(f"{from_type}→{to_type}", {})
        relation_type = str(relation["relationType"])
        bucket[relation_type] = bucket.get(relation_type, 0) + 1
    return frequencies


def _infer_relation_type(
    frequencies: dict[str, dict[str, int]], from_type: str, to_type: str
) -> str:
    """The graph's most frequent relation type for this ordered type pair
    (ties broken by name for determinism), else ``related_to``.

    The evidence behind a candidate is symmetric, so causal types (which would
    need an invented polarity) are never inferred, and a same-type pair — whose
    direction is decided only by which endpoint the crawl reached first — falls
    back to the symmetric ``related_to``.
    """
    if from_type == to_type:
        return FALLBACK_RELATION_TYPE
    bucket = {
        relation_type: count
        for relation_type, count in (frequencies.get(f"{from_type}→{to_type}") or {}).items()
        if relation_type not in _UNINFERABLE_RELATION_TYPES
    }
    if not bucket:
        return FALLBACK_RELATION_TYPE
    return max(sorted(bucket), key=lambda relation_type: bucket[relation_type])


def _closure_candidates(graph: LoomGraph, entity_id: str) -> list[tuple[str, float]]:
    """Two-hop nodes sharing neighbours with ``entity_id`` and not already
    linked to it, scored by neighbour-set Jaccard. Deterministic order."""
    own = set(graph.neighbors(entity_id))
    if not own:
        return []
    scored: list[tuple[str, float]] = []
    for other_id in graph.nodes():
        if other_id == entity_id or graph.has_any_edge(entity_id, other_id):
            continue
        other = set(graph.neighbors(other_id))
        shared = own & other
        if not shared:
            continue
        union = (own | other) - {entity_id, other_id}
        if not union:
            continue
        scored.append((other_id, len(shared - {entity_id, other_id}) / len(union)))
    scored.sort(key=lambda pair: (-pair[1], graph.node_docs[pair[0]]["name"]))
    return scored


def enrichment_crawl(params: EnrichmentCrawlInput, multi: MultiGraph) -> dict[str, Any]:
    max_nodes = params.max_nodes if params.max_nodes is not None else DEFAULT_MAX_NODES
    max_candidates = (
        params.max_candidates if params.max_candidates is not None else DEFAULT_MAX_CANDIDATES
    )
    num_samples = params.num_samples if params.num_samples is not None else DEFAULT_NUM_SAMPLES
    min_confidence = (
        params.min_confidence if params.min_confidence is not None else DEFAULT_MIN_CONFIDENCE
    )
    dry_run = params.dry_run is not False

    # Resolve the store outside any section (a bad graph propagates before the
    # try, rather than being caught as a section failure).
    store = multi.get_store(params.graph)

    state: dict[str, Any] = {
        "graph": LoomGraph(),
        "frontier": [],
        "candidates": [],
        "semanticAvailable": False,
    }

    # -- Section 1: prioritize ------------------------------------------------
    def _prioritize() -> Doc:
        entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
        relations = [
            r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()
        ]
        graph = hydrate_graph(entities, relations)
        state["graph"] = graph
        state["relationTypeFrequencies"] = _relation_type_frequencies(
            relations, {e["id"]: e["entityType"] for e in entities}
        )

        ranked: list[Doc] = []
        for entity in entities:
            observation_count = len(entity.get("observations") or [])
            relation_count = len(graph.node_edges(entity["id"]))
            ranked.append(
                {
                    "id": entity["id"],
                    "name": entity["name"],
                    "entityType": entity["entityType"],
                    "observationCount": observation_count,
                    "relationCount": relation_count,
                    "priorityScore": _priority_score(observation_count, relation_count),
                }
            )
        # Most under-described first; name breaks ties so the crawl is stable.
        ranked.sort(key=lambda node: (-float(node["priorityScore"]), str(node["name"])))
        frontier = ranked[:max_nodes]
        for rank, node in enumerate(frontier, start=1):
            node["rank"] = rank
        state["frontier"] = frontier
        return {
            "scanned": len(entities),
            "frontierSize": len(frontier),
            "maxNodes": max_nodes,
            "nodes": frontier,
        }

    prioritize_section = time_section(_prioritize)

    # -- Section 2: crawl -----------------------------------------------------
    def _semantic_candidates(entity_id: str) -> list[tuple[str, float]]:
        neighbors = semantic_neighbors(
            SemanticNeighborsInput.model_validate(
                {
                    "entityId": entity_id,
                    "limit": max_candidates,
                    "minSimilarity": min_confidence,
                    **({"graph": params.graph} if params.graph else {}),
                }
            ),
            multi,
        )
        return [(n["entity"]["id"], float(n["similarity"])) for n in neighbors]

    def _crawl() -> Doc:
        graph: LoomGraph = state["graph"]
        frequencies: dict[str, dict[str, int]] = state.get("relationTypeFrequencies", {})
        semantic_ok = embeddings_available(store)
        semantic_reason = None if semantic_ok else _NO_EMBEDDINGS_REASON

        merged: dict[tuple[str, str], Doc] = {}
        context: list[Doc] = []
        below_threshold = 0

        for node in state["frontier"]:
            entity_id = str(node["id"])
            existing = get_relations(
                GetRelationsInput.model_validate(
                    {"entityId": entity_id, **({"graph": params.graph} if params.graph else {})}
                ),
                multi,
            )
            scored: dict[str, Doc] = {}
            for other_id, score in _closure_candidates(graph, entity_id):
                scored[other_id] = {"confidence": score, "sources": ["common-neighbors"]}
            if semantic_ok:
                try:
                    semantic = _semantic_candidates(entity_id)
                except Exception as err:  # noqa: BLE001 — semantic context is optional.
                    # Losing the embedder must cost only the semantic half of
                    # the context, not the structural closure of every node.
                    semantic_ok = False
                    semantic_reason = _semantic_failure_reason(err)
                    semantic = []
                for other_id, score in semantic:
                    entry = scored.get(other_id)
                    if entry is None:
                        scored[other_id] = {"confidence": score, "sources": ["semantic-neighbors"]}
                    else:
                        entry["confidence"] = max(float(entry["confidence"]), score)
                        entry["sources"] = [*entry["sources"], "semantic-neighbors"]

            accepted = 0
            for other_id, entry in sorted(
                scored.items(), key=lambda item: -float(item[1]["confidence"])
            ):
                if accepted >= max_candidates:
                    break
                if float(entry["confidence"]) < min_confidence:
                    below_threshold += 1
                    continue
                other = graph.node_docs.get(other_id)
                if other is None:
                    continue
                key = (entity_id, other_id) if entity_id < other_id else (other_id, entity_id)
                if key in merged:
                    # Already proposed from the other endpoint — it is not a new
                    # candidate, so it must not consume this node's budget.
                    continue
                accepted += 1
                merged[key] = {
                    "from": {
                        "id": entity_id,
                        "name": node["name"],
                        "entityType": node["entityType"],
                    },
                    "to": {
                        "id": other_id,
                        "name": other["name"],
                        "entityType": other["entityType"],
                    },
                    "relationType": _infer_relation_type(
                        frequencies, str(node["entityType"]), str(other["entityType"])
                    ),
                    "confidence": float(entry["confidence"]),
                    "sources": list(entry["sources"]),
                    "rationale": (
                        f"{node['name']} and {other['name']} share graph context but no "
                        f"relation (evidence: {', '.join(entry['sources'])})"
                    ),
                }

            context.append(
                {
                    "entityId": entity_id,
                    "name": node["name"],
                    "existingRelations": len(existing),
                    "candidatesProposed": accepted,
                }
            )

        candidates = sorted(
            merged.values(), key=lambda c: (-float(c["confidence"]), str(c["from"]["name"]))
        )
        state["candidates"] = candidates
        state["semanticAvailable"] = semantic_ok
        return {
            "nodesCrawled": len(context),
            "candidatesProposed": len(candidates),
            "candidatesBelowThreshold": below_threshold,
            "minConfidence": min_confidence,
            "maxCandidatesPerNode": max_candidates,
            "semanticContextAvailable": semantic_ok,
            "semanticContextReason": semantic_reason,
            "context": context,
            "candidates": candidates,
        }

    # A failed upstream section must not let its dependants report clean zeros:
    # an empty crawl and a *skipped* crawl are different facts.
    crawl_section = (
        failed_section(_UPSTREAM_FAILED.format(section="prioritize"))
        if prioritize_section["error"] is not None
        else time_section(_crawl)
    )

    # -- Section 3: enrich ----------------------------------------------------
    def _enrich() -> Doc:
        candidates: list[Doc] = state["candidates"]
        if dry_run:
            return {
                "dryRun": True,
                "created": 0,
                "wouldCreate": len(candidates),
                "failures": [],
            }

        created = 0
        failures: list[Doc] = []
        for candidate in candidates:
            try:
                create_relation(
                    CreateRelationInput.model_validate(
                        {
                            "from": candidate["from"]["id"],
                            "to": candidate["to"]["id"],
                            "relationType": candidate["relationType"],
                            "polarity": None,
                            "strength": "weak",
                            "evidence": candidate["rationale"],
                            "graph": params.graph,
                        }
                    ),
                    multi,
                )
                created += 1
            except Exception as err:  # noqa: BLE001 — one bad write must not lose the rest.
                failures.append(
                    {
                        "from": candidate["from"]["id"],
                        "to": candidate["to"]["id"],
                        "relationType": candidate["relationType"],
                        "reason": str(err) or err.__class__.__name__,
                    }
                )
        return {
            "dryRun": False,
            "created": created,
            "wouldCreate": len(candidates),
            "failures": failures,
        }

    enrich_section = (
        failed_section(_UPSTREAM_FAILED.format(section="crawl"))
        if crawl_section["error"] is not None
        else time_section(_enrich)
    )

    # -- Section 4: summary ---------------------------------------------------
    def _summary() -> Doc:
        prioritize_data: Doc = prioritize_section["data"]
        crawl_data: Doc = crawl_section["data"]
        enrich_data: Doc = enrich_section["data"]
        counts = {
            "frontierSize": prioritize_data.get("frontierSize", 0),
            "nodesCrawled": crawl_data.get("nodesCrawled", 0),
            "candidatesProposed": crawl_data.get("candidatesProposed", 0),
            "candidatesBelowThreshold": crawl_data.get("candidatesBelowThreshold", 0),
            "enrichedCount": enrich_data.get("created", 0),
        }
        voting = {
            "mode": "deterministic",
            "requestedSamples": num_samples,
            "samplesUsed": 0,
            "applied": False,
            "reason": _NO_VOTING_REASON,
        }
        return {
            **counts,
            "dryRun": dry_run,
            "voting": voting,
            "semanticContextAvailable": state["semanticAvailable"],
            "text": _build_summary(counts, state["candidates"], dry_run, voting),
        }

    summary_section = (
        failed_section(_UPSTREAM_FAILED.format(section="enrich"))
        if enrich_section["error"] is not None
        else time_section(_summary)
    )

    result = run_composite(
        [
            ("prioritize", prioritize_section),
            ("crawl", crawl_section),
            ("enrich", enrich_section),
            ("summary", summary_section),
        ]
    )
    enrich_result: Doc | None = enrich_section["data"]
    # null, not 0: nothing was written *and* nothing was attempted.
    result["metadata"]["enrichedCount"] = (
        None if enrich_result is None else enrich_result.get("created", 0)
    )
    result["metadata"]["dryRun"] = dry_run
    return result


def _build_summary(
    counts: dict[str, Any], candidates: list[Doc], dry_run: bool, voting: dict[str, Any]
) -> str:
    lines = [
        "Enrichment Crawl",
        "",
        f"Frontier: {counts['frontierSize']} under-described node(s), "
        f"{counts['nodesCrawled']} crawled",
        f"Candidates: {counts['candidatesProposed']} proposed, "
        f"{counts['candidatesBelowThreshold']} below the confidence floor",
        f"Enriched: {counts['enrichedCount']} relation(s) created"
        + (" (dry run — nothing written)" if dry_run else ""),
        f"Voting: {voting['mode']} — {voting['reason']}",
        "",
    ]
    if not candidates:
        lines.append("No enrichment candidates found.")
        return "\n".join(lines)
    lines.append(f"Top {min(len(candidates), 5)} candidate(s):")
    for candidate in candidates[:5]:
        lines.append(
            f"  {candidate['from']['name']} -[{candidate['relationType']}]- "
            f"{candidate['to']['name']} (confidence: {candidate['confidence']:.3f}, "
            f"via {', '.join(candidate['sources'])})"
        )
    if len(candidates) > 5:
        lines.append(f"  ... and {len(candidates) - 5} more")
    return "\n".join(lines)
