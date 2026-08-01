"""Adaptability scoring — Keane IAM constraints as a post-transfer validator.

Combined = 0.4*pragmatic + 0.3*incremental + 0.3*consistency, gated as
accept (>=0.5), warn ([0.3, 0.5)), reject (<0.3).

- Pragmatic centrality: max(connectedness-to-existing, purposeRelevance=0.5);
  a proposal with no relations scores max(0.5, purposeRelevance).
- Incremental preference: 1 - novelEndpoints/totalEndpoints; empty
  substitutedRelations (or zero endpoints) -> 1.0.
- Structural consistency: mean per-relation type-pattern match against the
  target graph; empty substitutedRelations -> 1.0, but non-empty with no target
  relations -> 0.0.
"""

from __future__ import annotations

from typing import Any

DEFAULT_PRAGMATIC_WEIGHT = 0.4
DEFAULT_INCREMENTAL_WEIGHT = 0.3
DEFAULT_CONSISTENCY_WEIGHT = 0.3
DEFAULT_REJECT_THRESHOLD = 0.3
DEFAULT_WARN_THRESHOLD = 0.5


def compute_pragmatic_score(
    proposal: dict[str, Any],
    existing_entity_ids: set[str],
    purpose_relevance: float | None = None,
) -> float:
    """IAM constraint 1: fraction of relations pointing at existing target
    entities, floored by the purpose relevance (default 0.5)."""
    relations = proposal["relations"]
    effective = purpose_relevance if purpose_relevance is not None else 0.5

    if not relations:
        return max(0.5, effective)

    connected_count = sum(1 for r in relations if r["targetId"] in existing_entity_ids)
    connectedness = connected_count / len(relations)
    return max(connectedness, effective)


def compute_incremental_score(substituted_relations: list[dict[str, Any]]) -> float:
    """IAM constraint 2: simpler transfers (fewer novel endpoints) score higher."""
    if not substituted_relations:
        return 1.0

    all_endpoints: set[str] = set()
    novel_endpoints: set[str] = set()

    for sub in substituted_relations:
        all_endpoints.add(sub["targetFromId"])
        all_endpoints.add(sub["targetToId"])
        if sub["fromIsNovel"]:
            novel_endpoints.add(sub["targetFromId"])
        if sub["toIsNovel"]:
            novel_endpoints.add(sub["targetToId"])

    total_endpoints = len(all_endpoints)
    if total_endpoints == 0:
        return 1.0

    return 1 - (len(novel_endpoints) / total_endpoints)


def compute_consistency_score(
    substituted_relations: list[dict[str, Any]],
    target_relations: list[dict[str, Any]],
    entity_type_map: dict[str, str],
) -> float:
    """IAM constraint 3: mean fraction of existing target relations sharing the
    predicted relation's fromType:toType:relType pattern."""
    if not substituted_relations:
        return 1.0

    if not target_relations:
        return 0.0

    pattern_counts: dict[str, int] = {}
    total_by_rel_type: dict[str, int] = {}

    for rel in target_relations:
        from_type = entity_type_map.get(rel["from"], "unknown")
        to_type = entity_type_map.get(rel["to"], "unknown")
        key = f"{from_type}:{to_type}:{rel['relationType']}"
        pattern_counts[key] = pattern_counts.get(key, 0) + 1
        total_by_rel_type[rel["relationType"]] = total_by_rel_type.get(rel["relationType"], 0) + 1

    total_consistency = 0.0
    count = 0

    for sub in substituted_relations:
        from_type = entity_type_map.get(sub["targetFromId"], "unknown")
        to_type = entity_type_map.get(sub["targetToId"], "unknown")
        key = f"{from_type}:{to_type}:{sub['relationType']}"
        pattern_count = pattern_counts.get(key, 0)
        rel_type_total = total_by_rel_type.get(sub["relationType"], 0)

        if rel_type_total == 0:
            total_consistency += 0
        else:
            total_consistency += pattern_count / rel_type_total
        count += 1

    return total_consistency / count if count > 0 else 1.0


def assess_adaptability(
    pragmatic_score: float,
    incremental_score: float,
    consistency_score: float,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine the three IAM sub-scores into an overall score + gate decision."""
    options = options or {}

    pragmatic_weight = _opt(options, "pragmaticWeight", DEFAULT_PRAGMATIC_WEIGHT)
    incremental_weight = _opt(options, "incrementalWeight", DEFAULT_INCREMENTAL_WEIGHT)
    consistency_weight = _opt(options, "consistencyWeight", DEFAULT_CONSISTENCY_WEIGHT)
    reject_threshold = _opt(options, "rejectThreshold", DEFAULT_REJECT_THRESHOLD)
    warn_threshold = _opt(options, "warnThreshold", DEFAULT_WARN_THRESHOLD)

    overall_score = (
        pragmatic_weight * pragmatic_score
        + incremental_weight * incremental_score
        + consistency_weight * consistency_score
    )

    if overall_score >= warn_threshold:
        decision = "accept"
    elif overall_score >= reject_threshold:
        decision = "warn"
    else:
        decision = "reject"

    reasoning = _build_reasoning(
        overall_score, pragmatic_score, incremental_score, consistency_score, decision
    )

    return {
        "overallScore": overall_score,
        "pragmaticScore": pragmatic_score,
        "incrementalScore": incremental_score,
        "consistencyScore": consistency_score,
        "decision": decision,
        "reasoning": reasoning,
    }


def assess_transfer_adaptability(
    proposals: list[dict[str, Any]],
    substituted_relations: list[dict[str, Any]],
    target_relations: list[dict[str, Any]],
    existing_entity_ids: set[str],
    entity_type_map: dict[str, str],
    options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Assess each proposal. incremental/consistency are shared across proposals
    (they depend only on the transfer's substituted relations); pragmatic is
    per-proposal."""
    incremental_score = compute_incremental_score(substituted_relations)
    consistency_score = compute_consistency_score(
        substituted_relations, target_relations, entity_type_map
    )

    results: list[dict[str, Any]] = []
    for proposal in proposals:
        pragmatic_score = compute_pragmatic_score(proposal, existing_entity_ids)
        results.append(
            assess_adaptability(pragmatic_score, incremental_score, consistency_score, options)
        )
    return results


def _opt(options: dict[str, Any], key: str, default: float) -> float:
    value = options.get(key)
    return default if value is None else value


def _build_reasoning(
    overall_score: float,
    pragmatic_score: float,
    incremental_score: float,
    consistency_score: float,
    decision: str,
) -> str:
    parts: list[str] = []
    parts.append(f"Adaptability score: {overall_score:.3f} ({decision})")
    parts.append(
        f"Pragmatic: {pragmatic_score:.3f}, Incremental: {incremental_score:.3f}, "
        f"Consistency: {consistency_score:.3f}"
    )

    if decision == "reject":
        weak: list[str] = []
        if pragmatic_score < DEFAULT_REJECT_THRESHOLD:
            weak.append("pragmatic centrality")
        if incremental_score < DEFAULT_REJECT_THRESHOLD:
            weak.append("incremental preference")
        if consistency_score < DEFAULT_REJECT_THRESHOLD:
            weak.append("structural consistency")
        if weak:
            parts.append(f"Weak signals: {', '.join(weak)}")

    return ". ".join(parts)
