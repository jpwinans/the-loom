"""Hybrid-search ranking stages.

Retrieval (:mod:`theloom.semantic.search`) answers "which entities are near
this query"; this module answers "in what order, and grouped how". Every stage
here is a pure function of plain rows — no store, no embedder, no clock — so
the ranking behaviour can be pinned by worked examples instead of only through
a live end-to-end search. ``hybrid_search`` is fetch-then-rank over these.

Row shape: ``entityId``, ``name``, ``entityType``, ``score`` and a ``scores``
sub-dict of the per-signal contributions, which is what the command emits.
"""

from __future__ import annotations

import datetime
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Row = dict[str, Any]
Weights = Mapping[str, float]
Neighbor = tuple[str, str, str]
"""``(entityId, name, entityType)`` — the store detail a ranking stage needs."""
NeighborsOf = Callable[[str], Sequence[Neighbor]]

MAX_SEEDS = 5
MAX_NEIGHBORS_PER_NODE = 10
DEFAULT_RECENCY_MAX_BOOST = 0.15
DEFAULT_RECENCY_HALF_LIFE_DAYS = 7.0
MIN_QUALITY_GAP = 0.05


def expand_by_graph(
    seed_ids: Sequence[str],
    neighbors_of: NeighborsOf,
    hops: int,
    max_neighbors: int = MAX_NEIGHBORS_PER_NODE,
) -> dict[str, Row]:
    """Breadth-first expansion from ``seed_ids``, scoring a result ``1/hop``.

    Seeds are never results (they already scored on their own signals) and a
    node keeps the shortest hop distance it was first reached at — reaching it
    again from a later seed does not re-expand it, which bounds the walk on
    dense graphs.
    """
    results: dict[str, Row] = {}
    if hops <= 0:
        return results
    seed_set = set(seed_ids)
    for seed_id in seed_ids:
        frontier = [seed_id]
        for hop in range(1, hops + 1):
            next_frontier: list[str] = []
            for node_id in frontier:
                for neighbor_id, name, entity_type in neighbors_of(node_id)[:max_neighbors]:
                    if neighbor_id in seed_set:
                        continue
                    existing = results.get(neighbor_id)
                    if not existing or existing["hopDistance"] > hop:
                        results[neighbor_id] = {
                            "entityId": neighbor_id,
                            "name": name,
                            "entityType": entity_type,
                            "hopDistance": hop,
                            "expandedFrom": seed_id,
                            "score": 1.0 / hop,
                        }
                    if not existing:
                        next_frontier.append(neighbor_id)
            frontier = next_frontier
    return results


def select_seeds(
    vector_rows: Sequence[Row],
    keyword_matches: Mapping[str, Row],
    weights: Weights,
    max_seeds: int = MAX_SEEDS,
) -> list[str]:
    """Which hits are worth expanding from: the best half of the vector hits
    (capped at ``max_seeds``) ranked by the vector+keyword blend, so a hit that
    is merely *near* the query does not drag the graph walk off topic."""
    ranked = sorted(
        vector_rows,
        key=lambda row: (
            -(
                weights["vector"] * float(row["score"])
                + weights["keyword"]
                * float(keyword_matches.get(str(row["entityId"]), {}).get("score", 0))
            )
        ),
    )
    count = min(max_seeds, max(1, math.ceil(len(vector_rows) * 0.5)))
    return [str(row["entityId"]) for row in ranked[:count]]


def match_source(scores: Mapping[str, float]) -> str:
    """The label naming which signals actually fired for a row."""
    v, k, g = scores["vector"] > 0, scores["keyword"] > 0, scores["graph"] > 0
    if v and k and g:
        return "semantic+keyword+graph"
    if v and k:
        return "semantic+keyword"
    if v and g:
        return "semantic+graph"
    if k:
        return "keyword"
    if g:
        return "graph"
    return "semantic"


def fuse_scores(
    vector_rows: Sequence[Row],
    keyword_matches: Mapping[str, Row],
    graph_rows: Mapping[str, Row],
    weights: Weights,
) -> list[Row]:
    """Blend the three signals into one ranked list.

    Weights are applied raw (they are not normalized to sum to 1), so a row
    reached by only one signal scores at most that signal's weight — which is
    what keeps graph-only expansions below genuine hits. A row carrying both a
    vector and a graph signal keeps the *strongest* graph score it was reached
    with, not the last one.
    """
    fused: dict[str, Row] = {}
    for vector_row in vector_rows:
        entity_id = str(vector_row["entityId"])
        keyword = keyword_matches.get(entity_id)
        scores = {
            "vector": float(vector_row["score"]),
            "keyword": float(keyword["score"]) if keyword else 0.0,
            "graph": 0.0,
        }
        row: Row = {
            "entityId": entity_id,
            "name": vector_row["name"],
            "entityType": vector_row["entityType"],
            "score": sum(weights[key] * scores[key] for key in weights),
            "scores": scores,
            "matchSource": match_source(scores),
            "entryType": vector_row["entryType"],
        }
        if keyword:
            row["matchedTerms"] = keyword["matchedTerms"]
        fused[entity_id] = row
    for graph_row in graph_rows.values():
        entity_id = str(graph_row["entityId"])
        existing = fused.get(entity_id)
        if existing is None:
            scores = {"vector": 0.0, "keyword": 0.0, "graph": float(graph_row["score"])}
            fused[entity_id] = {
                "entityId": entity_id,
                "name": graph_row["name"],
                "entityType": graph_row["entityType"],
                "score": sum(weights[key] * scores[key] for key in weights),
                "scores": scores,
                "matchSource": "graph",
                "hopDistance": graph_row["hopDistance"],
                "expandedFrom": graph_row["expandedFrom"],
            }
        else:
            existing["scores"]["graph"] = max(existing["scores"]["graph"], graph_row["score"])
            existing["score"] = sum(weights[key] * existing["scores"][key] for key in weights)
            existing["matchSource"] = match_source(existing["scores"])
            existing["hopDistance"] = graph_row["hopDistance"]
            existing["expandedFrom"] = graph_row["expandedFrom"]
    return sorted(fused.values(), key=lambda row: -float(row["score"]))


def apply_recency_boost(
    rows: Sequence[Row],
    timestamps: Mapping[str, str | None],
    *,
    now_ms: float,
    max_boost: float = DEFAULT_RECENCY_MAX_BOOST,
    half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
) -> list[Row]:
    """Multiply each row's score by ``1 + max_boost * 2**(-age/half_life)``.

    A *decay*, not a cutoff: recency tilts an already-relevant ranking rather
    than replacing it, and the boost is bounded by ``max_boost`` so a fresh
    irrelevant row cannot outrank a strong stale one. Rows with no usable
    timestamp keep their score. ``now_ms`` is injected — the stage has no clock.
    """
    half_life_ms = max(1.0, half_life_days * 86_400_000)
    boosted: list[Row] = []
    for row in rows:
        timestamp = timestamps.get(str(row["entityId"]))
        if not timestamp:
            boosted.append(dict(row))
            continue
        parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        age_ms = max(0.0, now_ms - parsed.timestamp() * 1000)
        decay = math.exp(-age_ms * math.log(2) / half_life_ms)
        boosted.append({**row, "score": float(row["score"]) * (1 + max_boost * decay)})
    boosted.sort(key=lambda row: -float(row["score"]))
    return boosted


def apply_mmr(rows: Sequence[Row], mmr_lambda: float, limit: int) -> list[Row]:
    """Maximal Marginal Relevance re-selection: greedily take the row maximising
    ``lambda*relevance - (1-lambda)*max_similarity_to_already_selected``.

    Relevance is the score normalized against the leader, similarity is Jaccard
    overlap of ``name`` + ``entityType`` tokens — deliberately lexical, so the
    diversification is explainable and costs no extra vectors. The top row is
    always kept; ``lambda=1`` degenerates to plain score order. Returns rows in
    *selection* order, at most ``limit`` of them.
    """
    if len(rows) <= 1:
        return list(rows)
    token_sets = [
        {t for t in re.split(r"\W+", f"{row['name']} {row['entityType']}".lower()) if t}
        for row in rows
    ]
    max_score = float(rows[0]["score"])
    normalized = [(float(row["score"]) / max_score if max_score > 0 else 0.0) for row in rows]
    selected = [0]
    remaining = list(range(1, len(rows)))
    count = min(limit, len(rows))
    while len(selected) < count and remaining:
        best_index, best_mmr = remaining[0], -math.inf
        for index in remaining:
            max_sim = 0.0
            for chosen in selected:
                a, b = token_sets[index], token_sets[chosen]
                union = len(a | b)
                similarity = (len(a & b) / union) if union else 0.0
                max_sim = max(max_sim, similarity)
            mmr = mmr_lambda * normalized[index] - (1 - mmr_lambda) * max_sim
            if mmr > best_mmr:
                best_index, best_mmr = index, mmr
        selected.append(best_index)
        remaining.remove(best_index)
    return [rows[index] for index in selected]


def assign_quality_groups(rows: Sequence[Row], strategy: str) -> tuple[list[Row], int]:
    """Split the ranked rows into quality tiers wherever the score drops by
    more than the mean gap, and tag each row with its 1-based ``qualityGroup``.

    A ranked list has no natural cut-off; the gaps do. The threshold is
    ``mean_gap * 1.5 * multiplier`` (the ``similar`` strategy multiplies by a
    further 1.5, so it splits later and keeps tiers broad) with a floor of
    0.05, so an evenly-spaced list stays one group instead of fragmenting into
    noise. Returns ``(rows, group_count)``; an empty input is zero groups.
    """
    if not rows:
        return [], 0
    ordered = [dict(row) for row in sorted(rows, key=lambda row: -float(row["score"]))]
    if len(ordered) == 1:
        ordered[0]["qualityGroup"] = 1
        return ordered, 1
    multiplier = 1.5 if strategy == "similar" else 1.0
    gaps = [
        float(ordered[i]["score"]) - float(ordered[i + 1]["score"]) for i in range(len(ordered) - 1)
    ]
    threshold = max((sum(gaps) / len(gaps)) * 1.5 * multiplier, MIN_QUALITY_GAP)
    groups: list[list[Row]] = []
    current: list[Row] = []
    for index, row in enumerate(ordered):
        current.append(row)
        if index < len(gaps) and gaps[index] > threshold:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    for number, group in enumerate(groups, start=1):
        for row in group:
            row["qualityGroup"] = number
    return [row for group in groups for row in group], len(groups)
