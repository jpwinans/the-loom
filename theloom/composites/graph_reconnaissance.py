"""Graph Reconnaissance composite.

Comprehensive structural overview in one call, bundling six sections:
graph-stats, detect-loops (with
member/edge enrichment), list-leverage-points, analyze-centrality (degree,
betweenness, and pagerank *exposed as* ``eigenvector``), detect-components, and
list-bridges. Every section runs inside :func:`time_section`.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from theloom.composites.framework import build_composite_result, time_section
from theloom.operations.analysis import (
    AnalyzeCentralityInput,
    DetectComponentsInput,
    DetectLoopsInput,
    GraphOnlyInput,
    ListLeveragePointsInput,
    analyze_centrality,
    detect_components,
    detect_loops,
    graph_stats,
    list_leverage_points,
)
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph

DEFAULT_CENTRALITY_LIMIT = 10


class GraphReconInput(CommandInput):
    graph: str | None = None
    centrality_limit: int | None = Field(default=None, alias="centralityLimit", gt=0)


def _centrality_entries(scores: dict[str, float]) -> list[dict[str, Any]]:
    return [{"entityId": entity_id, "score": score} for entity_id, score in scores.items()]


def graph_reconnaissance(params: GraphReconInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    graph = params.graph
    centrality_limit = (
        params.centrality_limit if params.centrality_limit is not None else DEFAULT_CENTRALITY_LIMIT
    )

    def _stats() -> dict[str, Any]:
        result = graph_stats(GraphOnlyInput(graph=graph), multi)
        return {
            "entityCount": result["entityCount"],
            "relationCount": result["relationCount"],
            "entityTypeDistribution": result["entityTypeDistribution"],
            "relationTypeDistribution": result["relationTypeDistribution"],
        }

    def _loops() -> list[dict[str, Any]]:
        detected = detect_loops(DetectLoopsInput(graph=graph, persist=False), multi)
        store = multi.get_store(graph)
        all_relations = (
            [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
            if detected["loops"]
            else []
        )
        enriched: list[dict[str, Any]] = []
        for loop in detected["loops"]:
            members: list[dict[str, Any]] = []
            for member_id in loop["memberIds"]:
                entity = store.read_entity(member_id)
                if entity is not None:
                    doc = entity.model_dump(by_alias=True, exclude_unset=True)
                    members.append(
                        {"id": doc["id"], "name": doc["name"], "entityType": doc["entityType"]}
                    )
            edges: list[dict[str, Any]] = []
            path = loop["path"]
            for i in range(len(path) - 1):
                frm, to = path[i], path[i + 1]
                rel = next(
                    (r for r in all_relations if r["from"] == frm and r["to"] == to),
                    None,
                )
                if rel is not None:
                    edges.append(
                        {"from": rel["from"], "to": rel["to"], "relationType": rel["relationType"]}
                    )
            enriched.append(
                {
                    "name": loop["name"],
                    "classification": loop["classification"],
                    "memberCount": loop["memberCount"],
                    "members": members,
                    "edges": edges,
                }
            )
        return enriched

    def _leverage_points() -> list[dict[str, Any]]:
        result = list_leverage_points(ListLeveragePointsInput(graph=graph), multi)
        return [
            {
                "id": lp["id"],
                "name": lp["name"],
                "level": lp["_metadata"]["level"],
                "depthCategory": lp["_metadata"]["depthCategory"],
                "intervention": lp["_metadata"]["intervention"],
            }
            for lp in result["leveragePoints"]
        ]

    def _centrality() -> dict[str, list[dict[str, Any]]]:
        store = multi.get_store(graph)
        if not store.list_entities():
            return {"degree": [], "betweenness": [], "eigenvector": []}
        degree = analyze_centrality(
            AnalyzeCentralityInput(algorithm="degree", limit=centrality_limit, graph=graph), multi
        )
        betweenness = analyze_centrality(
            AnalyzeCentralityInput(algorithm="betweenness", limit=centrality_limit, graph=graph),
            multi,
        )
        pagerank = analyze_centrality(
            AnalyzeCentralityInput(algorithm="pagerank", limit=centrality_limit, graph=graph), multi
        )
        return {
            "degree": _centrality_entries(degree["scores"]),
            "betweenness": _centrality_entries(betweenness["scores"]),
            "eigenvector": _centrality_entries(pagerank["scores"]),
        }

    def _components() -> dict[str, Any]:
        result = detect_components(DetectComponentsInput(graph=graph), multi)
        sizes = sorted((len(c) for c in result["components"]), reverse=True)
        return {"count": result["summary"]["componentCount"], "sizes": sizes}

    def _bridges() -> list[dict[str, Any]]:
        try:
            return [
                {
                    "from": b["from"],
                    "to": b["to"],
                    "fromGraph": b["from_graph"],
                    "toGraph": b["to_graph"],
                    "relationType": b["relationType"],
                }
                for b in multi.bridges.list_bridges()
            ]
        except Exception:  # noqa: BLE001 — degrade to [] on any bridge error.
            return []

    sections = {
        "stats": time_section(_stats),
        "loops": time_section(_loops),
        "leveragePoints": time_section(_leverage_points),
        "centrality": time_section(_centrality),
        "components": time_section(_components),
        "bridges": time_section(_bridges),
    }
    total_ms = round((time.perf_counter() - start) * 1000)
    return build_composite_result(sections, total_ms)
