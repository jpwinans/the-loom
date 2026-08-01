"""Entity Deep Dive composite.

Comprehensive analysis of one entity: its details, relations (both directions),
neighbors, centrality scores
(degree, betweenness, pagerank exposed as ``eigenvector``), loop membership, and
semantic neighbors. Every section runs inside :func:`time_section`.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from theloom.composites.framework import build_composite_result, time_section
from theloom.operations.analysis import (
    AnalyzeCentralityInput,
    DetectLoopsInput,
    analyze_centrality,
    detect_loops,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.entity import _entity_doc
from theloom.operations.relations import (
    GetNeighborsInput,
    GetRelationsInput,
    get_neighbors,
    get_relations,
)
from theloom.operations.semantic import SemanticSearchInput, semantic_search
from theloom.store.multigraph import MultiGraph


class EntityDeepDiveInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    graph: str | None = None


def _to_relation(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "from": r["from"],
        "to": r["to"],
        "relationType": r["relationType"],
        "polarity": r.get("polarity"),
        "strength": r.get("strength"),
        "evidence": r.get("evidence"),
    }


def entity_deep_dive(params: EntityDeepDiveInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    graph = params.graph
    entity_id = params.entity_id

    def _entity() -> dict[str, Any]:
        doc = _entity_doc(multi.get_store(graph), entity_id)
        if doc is None:
            raise RuntimeError(f"Entity not found: {entity_id}")
        info: dict[str, Any] = {
            "id": doc["id"],
            "name": doc["name"],
            "entityType": doc["entityType"],
            "observations": doc["observations"],
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
        }
        confidence = doc.get("confidence")
        if confidence:
            info["confidence"] = {
                "score": confidence["score"],
                "basis": confidence["basis"],
                "lastEvaluated": confidence["lastEvaluated"],
            }
        if doc.get("status"):
            info["status"] = doc["status"]
        if doc.get("statusReason"):
            info["statusReason"] = doc["statusReason"]
        if doc.get("provenance"):
            info["provenance"] = doc["provenance"]
        if doc.get("version"):
            info["version"] = doc["version"]
        return info

    entity_section = time_section(_entity)

    def _relations() -> dict[str, list[dict[str, Any]]]:
        outgoing = get_relations(
            GetRelationsInput.model_validate(
                {"entityId": entity_id, "direction": "outgoing", "graph": graph}
            ),
            multi,
        )
        incoming = get_relations(
            GetRelationsInput.model_validate(
                {"entityId": entity_id, "direction": "incoming", "graph": graph}
            ),
            multi,
        )
        return {
            "outgoing": [_to_relation(r) for r in outgoing],
            "incoming": [_to_relation(r) for r in incoming],
        }

    def _neighbors() -> list[dict[str, Any]]:
        result = get_neighbors(
            GetNeighborsInput.model_validate({"entityId": entity_id, "graph": graph}), multi
        )
        return [
            {
                "id": n["id"],
                "name": n.get("name", n["id"]),
                "entityType": n.get("entityType", "unknown"),
            }
            for n in result
        ]

    def _centrality() -> dict[str, Any]:
        store = multi.get_store(graph)
        if not store.list_entities():
            return {"degree": 0, "betweenness": 0, "eigenvector": 0}
        degree = analyze_centrality(AnalyzeCentralityInput(algorithm="degree", graph=graph), multi)
        betweenness = analyze_centrality(
            AnalyzeCentralityInput(algorithm="betweenness", graph=graph), multi
        )
        pagerank = analyze_centrality(
            AnalyzeCentralityInput(algorithm="pagerank", graph=graph), multi
        )
        return {
            "degree": degree["scores"].get(entity_id, 0),
            "betweenness": betweenness["scores"].get(entity_id, 0),
            "eigenvector": pagerank["scores"].get(entity_id, 0),
        }

    def _loop_membership() -> list[dict[str, Any]]:
        detected = detect_loops(DetectLoopsInput(graph=graph, persist=False), multi)
        return [
            {
                "name": loop["name"],
                "classification": loop["classification"],
                "memberCount": loop["memberCount"],
            }
            for loop in detected["loops"]
            if entity_id in loop["memberIds"]
        ]

    def _semantic_neighbors() -> list[dict[str, Any]]:
        try:
            data = entity_section["data"]
            if not data:
                return []
            query = f"{data['name']} {' '.join(data['observations'])}"
            results = semantic_search(
                SemanticSearchInput.model_validate({"query": query, "limit": 10, "graph": graph}),
                multi,
            )
            return [
                {
                    "entityId": r["entityId"],
                    "name": r["name"],
                    "entityType": r["entityType"],
                    "score": r["score"],
                }
                for r in results
                if r["entityId"] != entity_id
            ]
        except Exception:  # noqa: BLE001 — degrade to [] on any failure.
            return []

    sections = {
        "entity": entity_section,
        "relations": time_section(_relations),
        "neighbors": time_section(_neighbors),
        "centrality": time_section(_centrality),
        "loopMembership": time_section(_loop_membership),
        "semanticNeighbors": time_section(_semantic_neighbors),
    }
    total_ms = round((time.perf_counter() - start) * 1000)
    return build_composite_result(sections, total_ms)
