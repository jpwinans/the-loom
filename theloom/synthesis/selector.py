"""Subgraph selection.

Anchors: hybrid search when a callable is provided (falling back on empty or
all-invalid results), else case-insensitive keyword scoring (name hits weigh
double). Ego BFS is undirected over ALL relations with a per-graph cap in
cross-graph mode; the centrality filter keeps anchors unconditionally and
fills remaining slots by descending degree with entity-order tie-breaks.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Protocol

from theloom.graph.analytics import degree_centrality
from theloom.graph.hydrate import hydrate_graph

Doc = dict[str, Any]

FOCUS_MODE_DEFAULTS: dict[str, dict[str, int]] = {
    "narrow": {"maxDepth": 1, "maxEntities": 20},
    "balanced": {"maxDepth": 2, "maxEntities": 50},
    "broad": {"maxDepth": 3, "maxEntities": 100},
}
MAX_ANCHORS = 10

HybridSearch = Callable[[str, int], list[Doc]]


class DocStore(Protocol):
    """The slice of GraphStore the synthesis pipeline reads (wire docs)."""

    def list_entities(self) -> list[Doc]: ...

    def list_relations(self) -> list[Doc]: ...

    def read_entity(self, entity_id: str) -> Doc | None: ...


def filter_entity_only_results(results: list[Doc]) -> list[Doc]:
    return [r for r in results if r.get("entryType") != "document_chunk"]


def find_anchors(
    query: str, store: DocStore, hybrid_search: HybridSearch | None = None
) -> list[str]:
    if hybrid_search is not None:
        results = hybrid_search(query, MAX_ANCHORS)
        if results:
            entity_ids = {e["id"] for e in store.list_entities()}
            valid = [r for r in results if r["entityId"] in entity_ids]
            if valid:
                return [r["entityId"] for r in valid]

    entities = store.list_entities()
    query_lower = query.lower()
    terms = [t for t in query_lower.split() if len(t) > 2]
    search_terms = terms if terms else [t for t in query_lower.split() if t]
    if not search_terms:
        return []

    scored = []
    for entity in entities:
        name_lower = entity["name"].lower()
        name_score = sum(1 for t in search_terms if t in name_lower)
        obs_score = sum(
            1
            for t in search_terms
            if any(isinstance(o, str) and t in o.lower() for o in entity.get("observations", []))
        )
        score = name_score * 2 + obs_score
        if score > 0:
            scored.append({"entityId": entity["id"], "score": score})
    scored.sort(key=lambda s: -s["score"])  # stable: ties keep entity order
    return [s["entityId"] for s in scored[:MAX_ANCHORS]]


def extract_ego_subgraphs(
    all_entities: list[Doc],
    all_relations: list[Doc],
    anchor_ids: list[str],
    max_depth: int,
    *,
    entity_graph_origin: dict[str, str] | None = None,
    graph_count: int = 1,
    max_entities: float = math.inf,
) -> tuple[dict[str, None], dict[str, None]]:
    """BFS from each anchor; returns (entityIds, relationIds) as ordered sets."""
    per_graph_cap = (
        math.ceil((max_entities / graph_count) * 1.5)
        if entity_graph_origin is not None and graph_count > 1
        else math.inf
    )
    adjacency: dict[str, list[Doc]] = {}
    for rel in all_relations:
        adjacency.setdefault(rel["from"], []).append(rel)
        adjacency.setdefault(rel["to"], []).append(rel)

    entity_ids: dict[str, None] = {}
    relation_ids: dict[str, None] = {}
    graph_entity_counts: dict[str, int] = {}

    def add_entity(entity_id: str) -> bool:
        if entity_id in entity_ids:
            return False
        if entity_graph_origin is not None and graph_count > 1:
            origin = entity_graph_origin.get(entity_id)
            if origin is not None:
                count = graph_entity_counts.get(origin, 0)
                if count >= per_graph_cap:
                    return False
                graph_entity_counts[origin] = count + 1
        entity_ids[entity_id] = None
        return True

    for anchor_id in anchor_ids:
        add_entity(anchor_id)
        frontier = [anchor_id]
        for _depth in range(max_depth):
            if len(entity_ids) >= max_entities:
                break
            next_frontier: list[str] = []
            for node_id in frontier:
                for rel in adjacency.get(node_id, []):
                    relation_ids[rel["id"]] = None
                    neighbor = rel["to"] if rel["from"] == node_id else rel["from"]
                    if add_entity(neighbor):
                        next_frontier.append(neighbor)
            frontier = next_frontier

    return entity_ids, relation_ids


def filter_by_centrality(
    entities: list[Doc], relations: list[Doc], max_entities: int, anchor_ids: list[str]
) -> tuple[list[Doc], list[Doc]]:
    if len(entities) <= max_entities:
        return entities, relations
    graph = hydrate_graph(entities, relations)
    scores = degree_centrality(graph)
    kept = {a for a in anchor_ids if graph.has_node(a)}
    ranked = sorted(
        ((node, score) for node, score in scores.items() if node not in kept),
        key=lambda pair: -pair[1],
    )
    for node, _score in ranked:
        if len(kept) >= max_entities:
            break
        kept.add(node)
    return (
        [e for e in entities if e["id"] in kept],
        [r for r in relations if r["from"] in kept and r["to"] in kept],
    )


def select_subgraph(
    query: str,
    store: DocStore,
    config: Doc | None = None,
    hybrid_search: HybridSearch | None = None,
) -> Doc:
    config = config or {}
    focus = config.get("focus") or "balanced"
    defaults = FOCUS_MODE_DEFAULTS.get(focus, FOCUS_MODE_DEFAULTS["balanced"])
    max_depth = min(
        config["maxDepth"] if config.get("maxDepth") is not None else defaults["maxDepth"], 10
    )
    max_entities = min(
        config["maxEntities"] if config.get("maxEntities") is not None else defaults["maxEntities"],
        1000,
    )

    anchor_ids = find_anchors(query, store, hybrid_search)
    if not anchor_ids:
        return {"entities": [], "relations": [], "anchorEntityIds": []}

    all_entities = store.list_entities()
    all_relations = store.list_relations()
    entity_ids, relation_ids = extract_ego_subgraphs(
        all_entities,
        all_relations,
        anchor_ids,
        max_depth,
        entity_graph_origin=config.get("entityGraphOrigin"),
        graph_count=config.get("graphCount") or 1,
        max_entities=max_entities,
    )
    # No endpoint check here — relations whose far endpoint was capped out of the
    # entity set are kept; they count toward relationCount but are dropped later by
    # hydrateGraph/region filtering.
    selected_entities = [e for e in all_entities if e["id"] in entity_ids]
    selected_relations = [r for r in all_relations if r["id"] in relation_ids]
    entities, relations = filter_by_centrality(
        selected_entities, selected_relations, max_entities, anchor_ids
    )
    return {"entities": entities, "relations": relations, "anchorEntityIds": anchor_ids}
