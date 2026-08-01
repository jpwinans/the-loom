"""Hofstadter concept slippage.

Temperature maps to a similarity threshold via 0.9 * e^(-2.9t); candidates are
scored on structural similarity (crossdomain roles with default weights),
graph proximity (1/(1+shortest-path) via the bidirectional shortest-path
search), and shared context (Jaccard of neighbor sets); slippage paths and
their natural-language summaries are part of the wire output.
"""

from __future__ import annotations

import math
from typing import Any

from theloom.analysis.crossdomain import (
    compute_similarity_breakdown,
    compute_structural_roles,
    compute_weighted_similarity,
    profile_similarity,
    resolve_options,
)
from theloom.graph.hydrate import Doc, hydrate_graph
from theloom.graph.paths import bidirectional

MAX_CANDIDATES = 500
DEFAULT_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 100
DEFAULT_SLIPPAGE_TIMEOUT_MS = 10000
DEFAULT_TEMPERATURE = 0.5
DEFAULT_STRUCTURAL_WEIGHT = 0.5
DEFAULT_PROXIMITY_WEIGHT = 0.25
DEFAULT_CONTEXT_WEIGHT = 0.25


def resolve_slippage_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = options or {}
    temperature = max(0.0, min(1.0, opts.get("temperature", DEFAULT_TEMPERATURE)))
    limit = max(1, min(MAX_RESULT_LIMIT, opts.get("limit", DEFAULT_RESULT_LIMIT)))
    resolved: dict[str, Any] = {
        "temperature": temperature,
        "limit": limit,
        "structuralWeight": opts.get("structuralWeight", DEFAULT_STRUCTURAL_WEIGHT),
        "proximityWeight": opts.get("proximityWeight", DEFAULT_PROXIMITY_WEIGHT),
        "contextWeight": opts.get("contextWeight", DEFAULT_CONTEXT_WEIGHT),
        "timeoutMs": opts.get("timeoutMs", DEFAULT_SLIPPAGE_TIMEOUT_MS),
    }
    if opts.get("entityType") is not None:
        resolved["entityType"] = opts["entityType"]
    if opts.get("relationType") is not None:
        resolved["relationType"] = opts["relationType"]
    return resolved


def temperature_to_threshold(temperature: float) -> float:
    t = max(0.0, min(1.0, temperature))
    return 0.9 * math.exp(-2.9 * t)


def _shared_context(adjacency: dict[str, set[str]], entity_a: str, entity_b: str) -> float:
    neighbors_a = set(adjacency.get(entity_a, set()))
    neighbors_a.discard(entity_a)
    neighbors_a.discard(entity_b)
    neighbors_b = set(adjacency.get(entity_b, set()))
    neighbors_b.discard(entity_a)
    neighbors_b.discard(entity_b)
    if not neighbors_a and not neighbors_b:
        return 0.0
    intersection = len(neighbors_a & neighbors_b)
    union = len(neighbors_a) + len(neighbors_b) - intersection
    return intersection / union if union else 0.0


def _slippage_path(
    source_role: dict[str, Any],
    candidate_role: dict[str, Any],
    breakdown: dict[str, float],
    options: dict[str, Any],
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if source_role["entityType"] != candidate_role["entityType"]:
        steps.append(
            {
                "dimension": "entity-type",
                "from": source_role["entityType"],
                "to": candidate_role["entityType"],
                "magnitude": 1.0,
            }
        )
    degree_diff = abs(source_role["degree"] - candidate_role["degree"])
    max_degree = max(source_role["degree"], candidate_role["degree"], 1)
    degree_magnitude = degree_diff / max_degree
    if degree_magnitude > 0.1:
        steps.append(
            {
                "dimension": "connectivity",
                "from": (
                    f"degree {source_role['degree']} (in:{source_role['inDegree']}, "
                    f"out:{source_role['outDegree']})"
                ),
                "to": (
                    f"degree {candidate_role['degree']} (in:{candidate_role['inDegree']}, "
                    f"out:{candidate_role['outDegree']})"
                ),
                "magnitude": degree_magnitude,
            }
        )
    out_sim = profile_similarity(
        source_role["outRelationTypeProfile"], candidate_role["outRelationTypeProfile"]
    )
    in_sim = profile_similarity(
        source_role["inRelationTypeProfile"], candidate_role["inRelationTypeProfile"]
    )
    avg_profile_sim = (out_sim + in_sim) / 2
    if avg_profile_sim < 0.9:
        source_types = list(
            dict.fromkeys(
                [
                    *source_role["outRelationTypeProfile"],
                    *source_role["inRelationTypeProfile"],
                ]
            )
        )
        candidate_types = list(
            dict.fromkeys(
                [
                    *candidate_role["outRelationTypeProfile"],
                    *candidate_role["inRelationTypeProfile"],
                ]
            )
        )
        steps.append(
            {
                "dimension": "relation-pattern",
                "from": ", ".join(source_types) or "none",
                "to": ", ".join(candidate_types) or "none",
                "magnitude": 1 - avg_profile_sim,
            }
        )
    neighbor_sim = profile_similarity(
        source_role["neighborTypeProfile"], candidate_role["neighborTypeProfile"]
    )
    if neighbor_sim < 0.9:
        steps.append(
            {
                "dimension": "neighbor-context",
                "from": ", ".join(source_role["neighborTypeProfile"]) or "none",
                "to": ", ".join(candidate_role["neighborTypeProfile"]) or "none",
                "magnitude": 1 - neighbor_sim,
            }
        )

    summary = _summary(
        source_role["entityName"], candidate_role["entityName"], steps, breakdown, options
    )
    return {"steps": steps, "summary": summary}


def _summary(
    source_name: str,
    candidate_name: str,
    steps: list[dict[str, Any]],
    breakdown: dict[str, float],
    options: dict[str, Any],
) -> str:
    if not steps:
        return (
            f'"{candidate_name}" is structurally identical to "{source_name}" '
            "— a direct substitution."
        )
    parts: list[str] = []
    sw = options.get("structuralWeight", DEFAULT_STRUCTURAL_WEIGHT)
    pw = options.get("proximityWeight", DEFAULT_PROXIMITY_WEIGHT)
    cw = options.get("contextWeight", DEFAULT_CONTEXT_WEIGHT)
    total = sw + pw + cw
    distance = (
        1
        - (
            breakdown["structuralSimilarity"] * sw
            + breakdown["graphProximity"] * pw
            + breakdown["sharedContext"] * cw
        )
        / total
        if total > 0
        else 1
    )
    if distance < 0.2:
        parts.append(f'"{candidate_name}" is a close structural analogue of "{source_name}"')
    elif distance < 0.5:
        parts.append(
            f'"{candidate_name}" shares moderate structural similarity with "{source_name}"'
        )
    else:
        parts.append(f'"{candidate_name}" represents a creative leap from "{source_name}"')
    significant = [s for s in steps if float(s["magnitude"]) > 0.3]
    if significant:
        descriptions = []
        for step in significant:
            if step["dimension"] == "entity-type":
                descriptions.append(f"shifting from {step['from']} to {step['to']}")
            elif step["dimension"] == "connectivity":
                descriptions.append("changing connectivity pattern")
            elif step["dimension"] == "relation-pattern":
                descriptions.append("reframing relational role")
            elif step["dimension"] == "neighbor-context":
                descriptions.append("moving to a different neighborhood context")
            else:
                descriptions.append(f"along {step['dimension']}")
        parts.append(", ".join(descriptions))
    return " — ".join(parts) + "."


def find_concept_slippages(
    all_entities: list[Doc],
    all_relations: list[Doc],
    concept_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolve_slippage_options(options)

    source_entity = next((e for e in all_entities if e["id"] == concept_id), None)
    if source_entity is None:
        raise ValueError(f"Source concept not found: {concept_id}")

    effective_threshold = temperature_to_threshold(resolved["temperature"])

    candidate_entities = [e for e in all_entities if e["id"] != concept_id]
    if resolved.get("entityType"):
        candidate_entities = [
            e for e in candidate_entities if e["entityType"] == resolved["entityType"]
        ]
    if resolved.get("relationType"):
        with_type: set[str] = set()
        for rel in all_relations:
            if rel["relationType"] == resolved["relationType"]:
                with_type.add(rel["from"])
                with_type.add(rel["to"])
        candidate_entities = [e for e in candidate_entities if e["id"] in with_type]
    candidate_entities = candidate_entities[:MAX_CANDIDATES]

    all_entity_ids = {e["id"] for e in all_entities}
    roles = compute_structural_roles(
        [source_entity, *candidate_entities], all_relations, all_entity_ids
    )
    source_role = roles[concept_id]

    graph = hydrate_graph(all_entities, all_relations)
    adjacency: dict[str, set[str]] = {}
    for rel in all_relations:
        adjacency.setdefault(rel["from"], set()).add(rel["to"])
        adjacency.setdefault(rel["to"], set()).add(rel["from"])
    mapping_options = resolve_options()

    candidates: list[dict[str, Any]] = []
    total_evaluated = 0
    for candidate_entity in candidate_entities:
        total_evaluated += 1
        candidate_role = roles.get(candidate_entity["id"])
        if candidate_role is None:
            continue
        sim_breakdown = compute_similarity_breakdown(source_role, candidate_role)
        structural = compute_weighted_similarity(sim_breakdown, mapping_options)
        if not graph.has_node(concept_id) or not graph.has_node(candidate_entity["id"]):
            proximity = 0.0
        elif concept_id == candidate_entity["id"]:
            proximity = 1.0
        else:
            path = bidirectional(graph, concept_id, candidate_entity["id"])
            proximity = 1 / (1 + (len(path) - 1)) if path is not None else 0.0
        shared = _shared_context(adjacency, concept_id, candidate_entity["id"])
        breakdown = {
            "structuralSimilarity": structural,
            "graphProximity": proximity,
            "sharedContext": shared,
        }
        total_weight = (
            resolved["structuralWeight"] + resolved["proximityWeight"] + resolved["contextWeight"]
        )
        score = (
            (
                structural * resolved["structuralWeight"]
                + proximity * resolved["proximityWeight"]
                + shared * resolved["contextWeight"]
            )
            / total_weight
            if total_weight
            else 0.0
        )
        if score < effective_threshold:
            continue
        candidates.append(
            {
                "entityId": candidate_entity["id"],
                "entityName": candidate_entity["name"],
                "entityType": candidate_entity["entityType"],
                "score": score,
                "distance": 1 - score,
                "scoreBreakdown": breakdown,
                "slippagePath": _slippage_path(source_role, candidate_role, breakdown, resolved),
            }
        )

    candidates.sort(key=lambda c: -float(c["score"]))
    return {
        "sourceId": concept_id,
        "sourceName": source_entity["name"],
        "sourceType": source_entity["entityType"],
        "temperature": resolved["temperature"],
        "effectiveThreshold": effective_threshold,
        "candidates": candidates[: resolved["limit"]],
        "totalEvaluated": total_evaluated,
        "options": resolved,
    }
