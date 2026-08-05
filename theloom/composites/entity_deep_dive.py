"""Entity Deep Dive composite.

Comprehensive analysis of one entity: its details, relations (both directions),
neighbors, centrality scores
(degree, betweenness, pagerank exposed as ``eigenvector``), loop membership, and
semantic neighbors. Every section runs inside :func:`time_section`.

By default the relations and neighbors sections render one compact line per
item — ``{name, entityType, relationType, direction, anchor}`` — instead of
full relation/entity envelopes, keeping a typical file entity's payload well
under the ~70KB a full dive used to cost. Pass ``"full": true`` to get the
pre-compaction envelopes back.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.composites.framework import run_composite
from theloom.operations.analysis import (
    AnalyzeCentralityInput,
    DetectLoopsInput,
    analyze_centrality,
    detect_loops,
)
from theloom.operations.common import CommandInput, UuidStr, resolve_entity_ref
from theloom.operations.entity import entity_doc
from theloom.operations.relations import (
    GetNeighborsInput,
    GetRelationsInput,
    get_neighbors,
    get_relations,
)
from theloom.operations.semantic import SemanticSearchInput, semantic_search
from theloom.store.multigraph import MultiGraph


class EntityDeepDiveInput(CommandInput):
    """Addressed by ``entityId`` or by ``name`` — exactly one."""

    entity_id: UuidStr | None = Field(default=None, alias="entityId")
    name: str | None = None
    graph: str | None = None
    full: bool | None = None


# Anchor lines stay short — they exist to give an agent just enough context to
# decide whether to follow up, not to replace a real read of the entity.
ANCHOR_MAX_CHARS = 200


def _anchor(observations: list[str] | None) -> str | None:
    if not observations:
        return None
    text = observations[0]
    if len(text) > ANCHOR_MAX_CHARS:
        return text[:ANCHOR_MAX_CHARS].rstrip() + "…"
    return text


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


def _score_for(centrality_response: dict[str, Any], entity_id: str) -> float:
    return next(
        (entry["score"] for entry in centrality_response["results"] if entry["id"] == entity_id),
        0,
    )


def _relation_line(
    relation: dict[str, Any],
    other_id: str,
    direction: str,
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    other = lookup.get(other_id, {})
    return {
        "name": other.get("name", other_id),
        "entityType": other.get("entityType", "unknown"),
        "relationType": relation["relationType"],
        "direction": direction,
        "anchor": _anchor(other.get("observations")),
    }


def entity_deep_dive(params: EntityDeepDiveInput, multi: MultiGraph) -> dict[str, Any]:
    graph = params.graph
    entity_id = resolve_entity_ref(
        multi.get_store(graph), entity_id=params.entity_id, name=params.name, id_field="entityId"
    )

    # State threaded across sections (sections run sequentially): semantic
    # neighbors needs the entity section's own data.
    state: dict[str, Any] = {"entity_data": None}

    def _entity() -> dict[str, Any]:
        doc = entity_doc(multi.get_store(graph), entity_id)
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
        state["entity_data"] = info
        return info

    # The relations and neighbors sections both need the same compact neighbor
    # set; fetch it once and share it rather than paying two store round-trips.
    compact_neighbors: list[dict[str, Any]] | None = None

    def _compact_neighbors() -> list[dict[str, Any]]:
        nonlocal compact_neighbors
        if compact_neighbors is None:
            compact_neighbors = get_neighbors(
                GetNeighborsInput.model_validate(
                    {"entityId": entity_id, "graph": graph, "compact": True}
                ),
                multi,
            )
        return compact_neighbors

    def _neighbor_lookup() -> dict[str, dict[str, Any]]:
        return {n["id"]: n for n in _compact_neighbors() if "id" in n}

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
        if params.full:
            return {
                "outgoing": [_to_relation(r) for r in outgoing],
                "incoming": [_to_relation(r) for r in incoming],
            }
        lookup = _neighbor_lookup()
        return {
            "outgoing": [_relation_line(r, r["to"], "out", lookup) for r in outgoing],
            "incoming": [_relation_line(r, r["from"], "in", lookup) for r in incoming],
        }

    def _neighbors() -> list[dict[str, Any]]:
        if params.full:
            result = get_neighbors(
                GetNeighborsInput.model_validate({"entityId": entity_id, "graph": graph}),
                multi,
            )
            return [
                {
                    "id": n["id"],
                    "name": n.get("name", n["id"]),
                    "entityType": n.get("entityType", "unknown"),
                }
                for n in result
            ]
        return [
            {
                "name": n.get("name", n.get("id")),
                "entityType": n.get("entityType", "unknown"),
                "relationType": n.get("relationType"),
                "direction": n.get("direction"),
                "anchor": _anchor(n.get("observations")),
            }
            for n in _compact_neighbors()
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
            "degree": _score_for(degree, entity_id),
            "betweenness": _score_for(betweenness, entity_id),
            "eigenvector": _score_for(pagerank, entity_id),
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
            data = state["entity_data"]
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

    return run_composite(
        [
            ("entity", _entity),
            ("relations", _relations),
            ("neighbors", _neighbors),
            ("centrality", _centrality),
            ("loopMembership", _loop_membership),
            ("semanticNeighbors", _semantic_neighbors),
        ]
    )
