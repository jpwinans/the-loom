"""Absence surprise scoring — Howard's "surprise at non-transfer".

When an analogy predicts an entity/relation that should exist in the target but
doesn't, score how surprising the absence is:

    S_absence = P_structural * P_transfer * C_completeness

P_structural = 0.4*typePattern + 0.3*neighborJaccard + 0.3*pathProximity
P_transfer   = per-relation-type prior (enables/requires 0.8, causes/influences
               0.7, contradicts 0.5, analogous_to 0.3, generalizes/exemplifies
               0.6), default 0.7.
C_completeness = min(1, entityRelCount / avgRelCountForType).

Two tiers: schema absences (whole relation types missing from the target,
scored at the transfer prior) and instance absences (each substituted relation
scored with the full formula). overallScore = max, meanScore = mean.

The graph-proximity, adjacency-index, and shared-context helpers are defined
directly here rather than reusing the concept-slippage internals, so this
module stays self-contained.
"""

from __future__ import annotations

from typing import Any

from theloom.graph.hydrate import Doc, LoomGraph, hydrate_graph
from theloom.graph.paths import bidirectional

DEFAULT_TRANSFER_PRIOR = 0.7
TYPE_PATTERN_WEIGHT = 0.4
NEIGHBOR_OVERLAP_WEIGHT = 0.3
PATH_PROXIMITY_WEIGHT = 0.3

DEFAULT_TRANSFER_PRIORS: dict[str, float] = {
    "enables": 0.8,
    "requires": 0.8,
    "causes": 0.7,
    "influences": 0.7,
    "contradicts": 0.5,
    "analogous_to": 0.3,
    "generalizes": 0.6,
    "exemplifies": 0.6,
}

NOVEL_PREFIX = "__NOVEL__"


# =============================================================================
# Slippage-derived structural helpers
# =============================================================================


def _build_adjacency_index(all_relations: list[Doc]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for rel in all_relations:
        index.setdefault(rel["from"], set()).add(rel["to"])
        index.setdefault(rel["to"], set()).add(rel["from"])
    return index


def _compute_shared_context(
    adjacency_index: dict[str, set[str]], entity_a: str, entity_b: str
) -> float:
    neighbors_a = set(adjacency_index.get(entity_a, set()))
    neighbors_a.discard(entity_a)
    neighbors_a.discard(entity_b)
    neighbors_b = set(adjacency_index.get(entity_b, set()))
    neighbors_b.discard(entity_a)
    neighbors_b.discard(entity_b)

    if not neighbors_a and not neighbors_b:
        return 0.0

    intersection = len(neighbors_a & neighbors_b)
    union = len(neighbors_a) + len(neighbors_b) - intersection
    if union == 0:
        return 0.0
    return intersection / union


def _compute_graph_proximity(graph: LoomGraph, source_id: str, target_id: str) -> float:
    if not graph.has_node(source_id) or not graph.has_node(target_id):
        return 0.0
    if source_id == target_id:
        return 1.0
    path = bidirectional(graph, source_id, target_id)
    if path is None:
        return 0.0
    path_length = len(path) - 1
    return 1 / (1 + path_length)


# =============================================================================
# Type pattern & completeness
# =============================================================================


def compute_type_pattern_score(
    all_relations: list[Doc],
    all_entities: list[Doc],
    from_type: str,
    to_type: str,
    rel_type: str,
) -> float:
    """Fraction of possible directed fromType->toType pairs connected by relType."""
    from_entities = [e for e in all_entities if e["entityType"] == from_type]
    to_entities = [e for e in all_entities if e["entityType"] == to_type]

    if not from_entities or not to_entities:
        return 0.0

    if from_type == to_type:
        possible_pairs = len(from_entities) * (len(from_entities) - 1)
    else:
        possible_pairs = len(from_entities) * len(to_entities)

    if possible_pairs == 0:
        return 0.0

    from_ids = {e["id"] for e in from_entities}
    to_ids = {e["id"] for e in to_entities}

    actual_count = 0
    for rel in all_relations:
        if rel["relationType"] == rel_type and rel["from"] in from_ids and rel["to"] in to_ids:
            actual_count += 1

    return actual_count / possible_pairs


def compute_completeness_coefficient(
    all_relations: list[Doc], all_entities: list[Doc], entity_id: str
) -> float:
    """How complete an entity's relation profile is vs. its type's average."""
    entity = next((e for e in all_entities if e["id"] == entity_id), None)
    if entity is None:
        return 0.0

    entity_rel_count = 0
    for rel in all_relations:
        if rel["from"] == entity_id:
            entity_rel_count += 1
        if rel["to"] == entity_id:
            entity_rel_count += 1

    same_type_entities = [e for e in all_entities if e["entityType"] == entity["entityType"]]
    if not same_type_entities:
        return 1.0

    same_type_ids = {e["id"] for e in same_type_entities}
    total_rel_count = 0
    for rel in all_relations:
        if rel["from"] in same_type_ids:
            total_rel_count += 1
        if rel["to"] in same_type_ids:
            total_rel_count += 1

    average_rel_count = total_rel_count / len(same_type_entities)
    if average_rel_count == 0:
        return 1.0

    return min(1.0, entity_rel_count / average_rel_count)


def compute_structural_prediction(
    all_entities: list[Doc],
    all_relations: list[Doc],
    from_id: str,
    to_id: str | None,
    rel_type: str,
    prebuilt: dict[str, Any] | None = None,
) -> float:
    """Blend type-pattern (0.4), neighbor Jaccard (0.3), and path proximity (0.3).
    A null toId (NOVEL endpoint) collapses to the from-entity's type pattern."""
    from_entity = next((e for e in all_entities if e["id"] == from_id), None)
    if from_entity is None:
        return 0.0

    if to_id is None:
        return compute_type_pattern_score(
            all_relations,
            all_entities,
            from_entity["entityType"],
            from_entity["entityType"],
            rel_type,
        )

    to_entity = next((e for e in all_entities if e["id"] == to_id), None)
    if to_entity is None:
        return 0.0

    type_score = compute_type_pattern_score(
        all_relations, all_entities, from_entity["entityType"], to_entity["entityType"], rel_type
    )

    adjacency_index = (
        prebuilt["adjacencyIndex"]
        if prebuilt is not None
        else _build_adjacency_index(all_relations)
    )
    neighbor_overlap = _compute_shared_context(adjacency_index, from_id, to_id)

    graph = (
        prebuilt["graph"] if prebuilt is not None else hydrate_graph(all_entities, all_relations)
    )
    path_proximity = _compute_graph_proximity(graph, from_id, to_id)

    return (
        TYPE_PATTERN_WEIGHT * type_score
        + NEIGHBOR_OVERLAP_WEIGHT * neighbor_overlap
        + PATH_PROXIMITY_WEIGHT * path_proximity
    )


# =============================================================================
# Score a single absence
# =============================================================================


def score_absence(
    all_entities: list[Doc],
    all_relations: list[Doc],
    source_relation: Doc,
    target_from_id: str,
    target_to_id: str | None,
    rel_type: str,
    options: dict[str, Any] | None = None,
    prebuilt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one predicted-but-missing edge with the full S_absence formula."""
    options = options or {}
    priors = {**DEFAULT_TRANSFER_PRIORS, **(options.get("transferPriors") or {})}
    default_prior = _opt(options, "defaultTransferPrior", DEFAULT_TRANSFER_PRIOR)

    is_novel_to = target_to_id is None or target_to_id.startswith(NOVEL_PREFIX)
    resolved_to_id = None if is_novel_to else target_to_id
    is_novel_from = target_from_id.startswith(NOVEL_PREFIX)
    resolved_from_id = None if is_novel_from else target_from_id

    if resolved_from_id is None and resolved_to_id is None:
        p_structural = 0.1
    elif resolved_from_id is None or resolved_to_id is None:
        known_id = resolved_from_id if resolved_from_id is not None else resolved_to_id
        assert known_id is not None
        p_structural = compute_structural_prediction(
            all_entities, all_relations, known_id, None, rel_type, prebuilt
        )
    else:
        p_structural = compute_structural_prediction(
            all_entities, all_relations, resolved_from_id, resolved_to_id, rel_type, prebuilt
        )

    prior_value = priors.get(rel_type)
    p_transfer = default_prior if prior_value is None else prior_value

    if resolved_from_id is not None:
        c_completeness = compute_completeness_coefficient(
            all_relations, all_entities, resolved_from_id
        )
    elif resolved_to_id is not None:
        c_completeness = compute_completeness_coefficient(
            all_relations, all_entities, resolved_to_id
        )
    else:
        c_completeness = 0.5

    score = p_structural * p_transfer * c_completeness

    return {
        "sourceRelationId": source_relation["id"],
        "sourceRelationType": source_relation["relationType"],
        "predictedFromId": target_from_id,
        "predictedToId": None if is_novel_to else target_to_id,
        "pStructural": p_structural,
        "pTransfer": p_transfer,
        "cCompleteness": c_completeness,
        "score": score,
    }


# =============================================================================
# Score all transfer absences
# =============================================================================


def score_transfer_absences(
    all_entities: list[Doc],
    all_relations: list[Doc],
    transfer_result: dict[str, Any],
    mapping_result: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Schema-level (missing relation types) + instance-level (per substituted
    relation) absences. overallScore = max, meanScore = mean over all scores."""
    options = options or {}
    schema_absences: list[dict[str, Any]] = []
    instance_absences: list[dict[str, Any]] = []

    substituted = transfer_result["substitutedRelations"]
    if not substituted:
        return {
            "overallScore": 0,
            "meanScore": 0,
            "schemaAbsences": schema_absences,
            "instanceAbsences": instance_absences,
        }

    # Schema-level absences: relation types present in source-side substitutions
    # but absent from any relation touching a mapped target entity.
    source_rel_types: dict[str, int] = {}
    target_rel_types: dict[str, int] = {}

    for sub in substituted:
        rel_type = sub["relationType"]
        source_rel_types[rel_type] = source_rel_types.get(rel_type, 0) + 1

    target_entity_ids = {m["targetId"] for m in mapping_result["mappings"]}
    for rel in all_relations:
        if rel["from"] in target_entity_ids or rel["to"] in target_entity_ids:
            target_rel_types[rel["relationType"]] = target_rel_types.get(rel["relationType"], 0) + 1

    priors = {**DEFAULT_TRANSFER_PRIORS, **(options.get("transferPriors") or {})}
    default_prior = _opt(options, "defaultTransferPrior", DEFAULT_TRANSFER_PRIOR)

    for rel_type, source_count in source_rel_types.items():
        target_count = target_rel_types.get(rel_type, 0)
        if target_count == 0:
            prior_value = priors.get(rel_type)
            transfer_prior = default_prior if prior_value is None else prior_value
            schema_absences.append(
                {
                    "relationType": rel_type,
                    "score": transfer_prior,
                    "sourceCount": source_count,
                    "targetCount": 0,
                }
            )

    # Instance-level absences: score each substituted relation with prebuilt indices.
    prebuilt: dict[str, Any] = {
        "adjacencyIndex": _build_adjacency_index(all_relations),
        "graph": hydrate_graph(all_entities, all_relations),
    }

    for sub in substituted:
        absence = score_absence(
            all_entities,
            all_relations,
            sub["sourceRelation"],
            sub["targetFromId"],
            sub["targetToId"],
            sub["relationType"],
            options,
            prebuilt,
        )
        instance_absences.append(absence)

    all_scores = [a["score"] for a in schema_absences] + [a["score"] for a in instance_absences]
    overall_score = max(all_scores) if all_scores else 0
    mean_score = sum(all_scores) / len(all_scores) if all_scores else 0

    return {
        "overallScore": overall_score,
        "meanScore": mean_score,
        "schemaAbsences": schema_absences,
        "instanceAbsences": instance_absences,
    }


def _opt(options: dict[str, Any], key: str, default: float) -> float:
    value = options.get(key)
    return default if value is None else value
