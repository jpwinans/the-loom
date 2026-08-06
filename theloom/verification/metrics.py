"""Capability metric checks: coverage and coupling violation generators.

These are the shared golden generators behind both the ``check-capabilities``
command (``theloom/operations/verification.py``) and the CapabilitySpec DSL
(``theloom/verification/capability_spec.py``). They live here — in the
verification package, not the operations (command) layer — so that
verification never has to import operations to reuse them; operations
imports downward from verification instead.
"""

from __future__ import annotations

from typing import Any

Doc = dict[str, Any]


def capability_result(name: str, violations: list[Doc]) -> Doc:
    return {"name": name, "pass": len(violations) == 0, "violations": violations}


def coverage(
    entities: list[Doc], relations: list[Doc], parent_type: str, child_type: str, relation_type: str
) -> Doc:
    name = f"coverage({parent_type}->{child_type} via {relation_type})"
    child_ids = {e["id"] for e in entities if e.get("entityType") == child_type}
    parents = [e for e in entities if e.get("entityType") == parent_type]
    violations: list[Doc] = []
    for parent in parents:
        has_child = any(
            r["relationType"] == relation_type
            and (
                (r["from"] == parent["id"] and r["to"] in child_ids)
                or (r["to"] == parent["id"] and r["from"] in child_ids)
            )
            for r in relations
        )
        if not has_child:
            violations.append(
                {
                    "capabilityName": name,
                    "violationType": "coverage",
                    "elementId": parent["id"],
                    "message": (
                        f"Entity '{parent['name']}' (type: {parent_type}) has no "
                        f"linked '{child_type}' via '{relation_type}'"
                    ),
                    "suggestedAction": (
                        f"Create a '{child_type}' entity and link it to "
                        f"'{parent['name']}' via '{relation_type}'"
                    ),
                }
            )
    return capability_result(name, violations)


def coupling(entities: list[Doc], relations: list[Doc], metric: str, threshold: float) -> Doc:
    name = f"coupling({metric}<{threshold})"
    if not entities:
        return capability_result(name, [])
    from theloom.graph.analytics import betweenness_centrality, degree_centrality
    from theloom.graph.hydrate import hydrate_graph

    graph = hydrate_graph(entities, relations)
    scores = betweenness_centrality(graph) if metric == "betweenness" else degree_centrality(graph)
    names = {e["id"]: e["name"] for e in entities}
    violations = [
        {
            "capabilityName": name,
            "violationType": "coupling",
            "elementId": entity_id,
            "message": (
                f"Entity '{names.get(entity_id, entity_id)}' has {metric} centrality "
                f"{score:.3f} exceeding threshold {threshold}"
            ),
            "suggestedAction": (
                f"Decompose '{names.get(entity_id, entity_id)}' into smaller entities or "
                f"redistribute its relations to reduce {metric} centrality below {threshold}"
            ),
        }
        for entity_id, score in scores.items()
        if score > threshold
    ]
    return capability_result(name, violations)
