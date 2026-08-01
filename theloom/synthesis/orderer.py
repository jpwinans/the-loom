"""Core numbers, region grouping, and region ordering.

Core numbers: iterative min-degree peeling over an undirected UNIQUE-neighbor
adjacency (multi-edges collapse), FIFO queue, insertion-order scans — the
peel order affects nothing but the numbers, which are exact. Regions come
from weakly-connected components in entity order; ordering is a stable
descending sort, so equal scores keep component order. Region ids keep their
pre-sort component index (`region-N` is NOT renumbered after ordering).
"""

from __future__ import annotations

from typing import Any

from theloom.graph.analytics import (
    betweenness_centrality,
    connected_components,
    degree_centrality,
    pagerank_centrality,
)
from theloom.graph.hydrate import hydrate_graph

Doc = dict[str, Any]


def compute_core_numbers(entities: list[Doc], relations: list[Doc]) -> dict[str, int]:
    if not entities:
        return {}
    adjacency: dict[str, dict[str, None]] = {e["id"]: {} for e in entities}
    for r in relations:
        if r["from"] in adjacency:
            adjacency[r["from"]][r["to"]] = None
        if r["to"] in adjacency:
            adjacency[r["to"]][r["from"]] = None

    degree = {node_id: len(neighbors) for node_id, neighbors in adjacency.items()}
    core_number: dict[str, int] = {}
    removed: set[str] = set()
    remaining: dict[str, None] = {e["id"]: None for e in entities}

    while remaining:
        min_deg = min(degree[node_id] for node_id in remaining)
        queue = [node_id for node_id in remaining if degree[node_id] <= min_deg]
        while queue:
            node_id = queue.pop(0)
            if node_id in removed:
                continue
            removed.add(node_id)
            del remaining[node_id]
            core_number[node_id] = min_deg
            for neighbor in adjacency.get(node_id, {}):
                if neighbor not in removed:
                    new_deg = degree[neighbor] - 1
                    degree[neighbor] = new_deg
                    if new_deg <= min_deg:
                        queue.append(neighbor)

    return core_number


def group_into_regions(
    entities: list[Doc],
    relations: list[Doc],
    core_numbers: dict[str, int],
    sub_questions: list[Doc] | None = None,
) -> list[Doc]:
    if not entities:
        return []
    graph = hydrate_graph(entities, relations)
    components = connected_components(graph)

    regions: list[Doc] = []
    for idx, component_ids in enumerate(components):
        center_id = component_ids[0]
        max_core = core_numbers.get(center_id, 0)
        for node_id in component_ids:
            core = core_numbers.get(node_id, 0)
            if core > max_core:
                max_core = core
                center_id = node_id

        component_set = set(component_ids)
        region_relation_ids = [
            r["id"] for r in relations if r["from"] in component_set and r["to"] in component_set
        ]
        sub_question_id = sub_questions[idx % len(sub_questions)]["id"] if sub_questions else None
        regions.append(
            {
                "id": f"region-{idx}",
                "centerEntityId": center_id,
                "entityIds": component_ids,
                "relationIds": region_relation_ids,
                "coreNumber": max_core,
                "subQuestionId": sub_question_id,
            }
        )
    return regions


def order_regions(
    regions: list[Doc],
    entities: list[Doc],
    relations: list[Doc],
    metric: str = "core-number",
) -> list[Doc]:
    if metric == "core-number":
        return sorted(regions, key=lambda r: -r["coreNumber"])

    graph = hydrate_graph(entities, relations)
    scores: dict[str, float]
    if metric == "degree":
        scores = degree_centrality(graph)
    elif metric == "pagerank":
        scores = pagerank_centrality(graph)
    elif metric == "betweenness":
        scores = betweenness_centrality(graph)
    else:
        scores = {}
    return sorted(regions, key=lambda r: -(scores.get(r["centerEntityId"]) or 0))


def assign_regions_to_sub_questions(regions: list[Doc], sub_questions: list[Doc]) -> None:
    by_id = {sq["id"]: sq for sq in sub_questions}
    for region in regions:
        sq = by_id.get(region["subQuestionId"]) if region["subQuestionId"] else None
        if sq is not None and region["id"] not in sq["assignedRegionIds"]:
            sq["assignedRegionIds"].append(region["id"])
