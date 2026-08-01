"""Influence Map composite.

Maps an entity's influence via two semiring distance passes (Viterbi confidence
+ Tropical shortest), per-target
path counts, an iterative multi-hop neighborhood BFS, and per-target bottleneck
analysis. Every section runs inside :func:`time_section`; per-target/per-node
loops swallow individual failures.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from theloom.composites.framework import build_composite_result, time_section
from theloom.operations.algebra import (
    CountPathsInput,
    SemiringDistancesInput,
    SourceTargetInput,
    semiring_bottleneck,
    semiring_count_paths,
    semiring_distances,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.relations import GetNeighborsInput, get_neighbors
from theloom.store.multigraph import MultiGraph

DEFAULT_MAX_DEPTH = 5
DEFAULT_LIMIT = 10
MAX_NEIGHBORHOOD_DEPTH = 3


class InfluenceMapInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None


def _distance_entries(distances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "entityId": d["entityId"],
            "entityName": d["entityName"],
            "value": d["value"],
            "path": d["path"],
        }
        for d in distances
    ]


def _derive_targets(
    confidence: dict[str, Any], shortest: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    source = shortest["data"] if shortest["data"] is not None else (confidence["data"] or [])
    seen: set[str] = set()
    targets: list[dict[str, Any]] = []
    for entry in source:
        if entry["entityId"] not in seen and len(targets) < limit:
            seen.add(entry["entityId"])
            targets.append({"entityId": entry["entityId"], "entityName": entry["entityName"]})
    return targets


def influence_map(params: InfluenceMapInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    graph = params.graph
    entity_id = params.entity_id
    max_depth = params.max_depth if params.max_depth is not None else DEFAULT_MAX_DEPTH
    limit = params.limit if params.limit is not None else DEFAULT_LIMIT

    def _confidence() -> list[dict[str, Any]]:
        result = semiring_distances(
            SemiringDistancesInput.model_validate(
                {
                    "source": entity_id,
                    "semiring": "viterbi",
                    "maxDepth": max_depth,
                    "limit": limit,
                    "graph": graph,
                    "direction": "both",
                }
            ),
            multi,
        )
        return _distance_entries(result["distances"])

    def _shortest() -> list[dict[str, Any]]:
        result = semiring_distances(
            SemiringDistancesInput.model_validate(
                {
                    "source": entity_id,
                    "semiring": "tropical",
                    "maxDepth": max_depth,
                    "limit": limit,
                    "graph": graph,
                    "direction": "both",
                }
            ),
            multi,
        )
        return _distance_entries(result["distances"])

    confidence_section = time_section(_confidence)
    shortest_section = time_section(_shortest)
    targets = _derive_targets(confidence_section, shortest_section, limit)

    def _path_counts() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for target in targets:
            try:
                count_result = semiring_count_paths(
                    CountPathsInput.model_validate(
                        {
                            "source": entity_id,
                            "target": target["entityId"],
                            "maxDepth": max_depth,
                            "graph": graph,
                        }
                    ),
                    multi,
                )
                results.append(
                    {
                        "targetEntityId": target["entityId"],
                        "targetEntityName": target["entityName"],
                        "count": count_result["count"],
                        "bounded": count_result["bounded"],
                    }
                )
            except Exception:  # noqa: BLE001 — skip unreachable targets.
                continue
        return results

    def _neighborhood() -> list[dict[str, Any]]:
        depth = min(max_depth, MAX_NEIGHBORHOOD_DEPTH)
        seen: set[str] = {entity_id}
        entries: list[dict[str, Any]] = []
        frontier: list[str] = [entity_id]
        for _hop in range(depth):
            next_frontier: list[str] = []
            for current_id in frontier:
                try:
                    neighbors = get_neighbors(
                        GetNeighborsInput.model_validate({"entityId": current_id, "graph": graph}),
                        multi,
                    )
                except Exception:  # noqa: BLE001 — skip failed neighbor lookups.
                    continue
                for neighbor in neighbors:
                    neighbor_id = neighbor["id"]
                    if neighbor_id not in seen:
                        seen.add(neighbor_id)
                        is_stub = neighbor.get("stub") is True
                        entries.append(
                            {
                                "entityId": neighbor_id,
                                "entityName": neighbor_id if is_stub else neighbor["name"],
                                "entityType": "unknown" if is_stub else neighbor["entityType"],
                            }
                        )
                        next_frontier.append(neighbor_id)
            frontier = next_frontier
            if not frontier:
                break
        return entries

    def _bottlenecks() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for target in targets:
            try:
                bn = semiring_bottleneck(
                    SourceTargetInput.model_validate(
                        {"source": entity_id, "target": target["entityId"], "graph": graph}
                    ),
                    multi,
                )
                if bn:
                    results.append(
                        {
                            "targetEntityId": target["entityId"],
                            "bottleneckValue": bn["bottleneckValue"],
                            "bottleneckRelation": bn["bottleneckRelation"],
                            "path": bn["path"],
                            "pathCapacity": bn["pathCapacity"],
                        }
                    )
            except Exception:  # noqa: BLE001 — skip failed targets.
                continue
        return results

    sections = {
        "confidenceDistances": confidence_section,
        "shortestDistances": shortest_section,
        "pathCounts": time_section(_path_counts),
        "neighborhood": time_section(_neighborhood),
        "bottlenecks": time_section(_bottlenecks),
    }
    total_ms = round((time.perf_counter() - start) * 1000)
    return build_composite_result(sections, total_ms)
