"""Cross-domain concept mapping.

This uses greedy assignment, not the Hungarian algorithm — a deliberate,
documented choice, because greedy assignment is the behavioral contract here:
a true optimal (Hungarian) assignment would change the produced mappings
whenever close-scoring alternatives exist.
"""

from __future__ import annotations

import math
from typing import Any

from theloom.graph.hydrate import Doc

MAX_DOMAIN_SIZE = 100
DEFAULT_PAIR_MIN_SIMILARITY = 0.1
DEFAULT_MAPPING_TIMEOUT_MS = 10000


def resolve_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = options or {}
    return {
        "degreeWeight": opts.get("degreeWeight", 0.3),
        "relationProfileWeight": opts.get("relationProfileWeight", 0.35),
        "neighborProfileWeight": opts.get("neighborProfileWeight", 0.2),
        "entityTypeWeight": opts.get("entityTypeWeight", 0.15),
        "pairMinSimilarity": opts.get("pairMinSimilarity", DEFAULT_PAIR_MIN_SIMILARITY),
        "timeoutMs": opts.get("timeoutMs", DEFAULT_MAPPING_TIMEOUT_MS),
    }


def _normalize_profile(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    if total == 0:
        return {}
    return {key: value / total for key, value in counts.items()}


def compute_structural_roles(
    domain_entities: list[Doc], all_relations: list[Doc], domain_ids: set[str]
) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}
    entity_map = {e["id"]: e for e in domain_entities}
    domain_relations = [
        r for r in all_relations if r["from"] in domain_ids and r["to"] in domain_ids
    ]
    for entity in domain_entities:
        out_counts: dict[str, int] = {}
        in_counts: dict[str, int] = {}
        neighbor_counts: dict[str, int] = {}
        in_degree = 0
        out_degree = 0
        for rel in domain_relations:
            if rel["from"] == entity["id"]:
                out_degree += 1
                out_counts[rel["relationType"]] = out_counts.get(rel["relationType"], 0) + 1
                neighbor = entity_map.get(rel["to"])
                if neighbor:
                    neighbor_counts[neighbor["entityType"]] = (
                        neighbor_counts.get(neighbor["entityType"], 0) + 1
                    )
            if rel["to"] == entity["id"]:
                in_degree += 1
                in_counts[rel["relationType"]] = in_counts.get(rel["relationType"], 0) + 1
                neighbor = entity_map.get(rel["from"])
                if neighbor:
                    neighbor_counts[neighbor["entityType"]] = (
                        neighbor_counts.get(neighbor["entityType"], 0) + 1
                    )
        roles[entity["id"]] = {
            "entityId": entity["id"],
            "entityName": entity["name"],
            "entityType": entity["entityType"],
            "degree": in_degree + out_degree,
            "inDegree": in_degree,
            "outDegree": out_degree,
            "outRelationTypeProfile": _normalize_profile(out_counts),
            "inRelationTypeProfile": _normalize_profile(in_counts),
            "neighborTypeProfile": _normalize_profile(neighbor_counts),
        }
    return roles


def profile_similarity(profile_a: dict[str, float], profile_b: dict[str, float]) -> float:
    all_keys = set(profile_a) | set(profile_b)
    if not all_keys:
        return 1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for key in all_keys:
        a = profile_a.get(key, 0.0)
        b = profile_b.get(key, 0.0)
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a == 0 and norm_b == 0:
        return 1.0
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


def compute_similarity_breakdown(
    role_a: dict[str, Any], role_b: dict[str, Any]
) -> dict[str, float]:
    max_degree = max(role_a["degree"], role_b["degree"], 1)
    return {
        "degreeSimilarity": 1 - abs(role_a["degree"] - role_b["degree"]) / max_degree,
        "relationProfileSimilarity": (
            profile_similarity(role_a["outRelationTypeProfile"], role_b["outRelationTypeProfile"])
            + profile_similarity(role_a["inRelationTypeProfile"], role_b["inRelationTypeProfile"])
        )
        / 2,
        "neighborProfileSimilarity": profile_similarity(
            role_a["neighborTypeProfile"], role_b["neighborTypeProfile"]
        ),
        "entityTypeMatch": 1.0 if role_a["entityType"] == role_b["entityType"] else 0.0,
    }


def compute_weighted_similarity(breakdown: dict[str, float], options: dict[str, Any]) -> float:
    total = (
        options["degreeWeight"]
        + options["relationProfileWeight"]
        + options["neighborProfileWeight"]
        + options["entityTypeWeight"]
    )
    if total == 0:
        return 0.0
    return float(
        (
            breakdown["degreeSimilarity"] * options["degreeWeight"]
            + breakdown["relationProfileSimilarity"] * options["relationProfileWeight"]
            + breakdown["neighborProfileSimilarity"] * options["neighborProfileWeight"]
            + breakdown["entityTypeMatch"] * options["entityTypeWeight"]
        )
        / total
    )


def extract_domain_entities(spec: dict[str, Any], all_entities: list[Doc]) -> list[Doc]:
    if spec.get("entityIds"):
        id_set = set(spec["entityIds"])
        return [e for e in all_entities if e["id"] in id_set]
    if spec.get("entityType"):
        return [e for e in all_entities if e["entityType"] == spec["entityType"]]
    raise ValueError("Domain specification must include either entityIds or entityType")


def map_cross_domain_concepts(
    all_entities: list[Doc],
    all_relations: list[Doc],
    source_domain: dict[str, Any],
    target_domain: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = resolve_options(options)

    source_entities = extract_domain_entities(source_domain, all_entities)
    target_entities = extract_domain_entities(target_domain, all_entities)
    if not source_entities:
        raise ValueError("Source domain contains no entities")
    if not target_entities:
        raise ValueError("Target domain contains no entities")
    if len(source_entities) > MAX_DOMAIN_SIZE:
        raise ValueError(
            f"Source domain has {len(source_entities)} entities, maximum is {MAX_DOMAIN_SIZE}"
        )
    if len(target_entities) > MAX_DOMAIN_SIZE:
        raise ValueError(
            f"Target domain has {len(target_entities)} entities, maximum is {MAX_DOMAIN_SIZE}"
        )

    source_ids = {e["id"] for e in source_entities}
    target_ids = {e["id"] for e in target_entities}
    source_roles = compute_structural_roles(source_entities, all_relations, source_ids)
    target_roles = compute_structural_roles(target_entities, all_relations, target_ids)

    # Greedy assignment over the similarity matrix (stable sorts throughout).
    candidates: list[dict[str, Any]] = []
    for source_id, source_role in source_roles.items():
        for target_id, target_role in target_roles.items():
            breakdown = compute_similarity_breakdown(source_role, target_role)
            similarity = compute_weighted_similarity(breakdown, resolved)
            if similarity >= resolved["pairMinSimilarity"]:
                candidates.append(
                    {
                        "sourceId": source_id,
                        "targetId": target_id,
                        "similarity": similarity,
                        "breakdown": breakdown,
                    }
                )
    candidates.sort(key=lambda c: -float(c["similarity"]))

    used_sources: set[str] = set()
    used_targets: set[str] = set()
    mappings: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["sourceId"] in used_sources or candidate["targetId"] in used_targets:
            continue
        source_role = source_roles[candidate["sourceId"]]
        target_role = target_roles[candidate["targetId"]]
        mappings.append(
            {
                "sourceId": candidate["sourceId"],
                "sourceName": source_role["entityName"],
                "sourceType": source_role["entityType"],
                "targetId": candidate["targetId"],
                "targetName": target_role["entityName"],
                "targetType": target_role["entityType"],
                "similarity": candidate["similarity"],
                "similarityBreakdown": candidate["breakdown"],
            }
        )
        used_sources.add(candidate["sourceId"])
        used_targets.add(candidate["targetId"])
    mappings.sort(key=lambda m: -float(m["similarity"]))

    mapping_lookup = {m["sourceId"]: m["targetId"] for m in mappings}
    source_relations = [
        r for r in all_relations if r["from"] in source_ids and r["to"] in source_ids
    ]
    target_relations = [
        r for r in all_relations if r["from"] in target_ids and r["to"] in target_ids
    ]

    # Structural preservation
    if not source_relations:
        preservation = 1.0
    else:
        target_edge_set = {f"{r['from']}->{r['to']}" for r in target_relations}
        preserved = 0
        total = 0
        for rel in source_relations:
            mapped_from = mapping_lookup.get(rel["from"])
            mapped_to = mapping_lookup.get(rel["to"])
            if not mapped_from or not mapped_to:
                continue
            total += 1
            if f"{mapped_from}->{mapped_to}" in target_edge_set:
                preserved += 1
        preservation = preserved / total if total else 1.0

    unmapped: list[dict[str, Any]] = []
    for entity in source_entities:
        if entity["id"] not in used_sources:
            role = source_roles[entity["id"]]
            unmapped.append(
                {
                    "entityId": entity["id"],
                    "entityName": entity["name"],
                    "entityType": entity["entityType"],
                    "domain": "source",
                    "classification": "gap" if role["degree"] > 0 else "novel",
                    "role": role,
                }
            )
    for entity in target_entities:
        if entity["id"] not in used_targets:
            role = target_roles[entity["id"]]
            unmapped.append(
                {
                    "entityId": entity["id"],
                    "entityName": entity["name"],
                    "entityType": entity["entityType"],
                    "domain": "target",
                    "classification": "gap" if role["degree"] > 0 else "novel",
                    "role": role,
                }
            )

    average = sum(float(m["similarity"]) for m in mappings) / len(mappings) if mappings else 0
    return {
        "sourceDomain": source_domain.get("label") or "source",
        "targetDomain": target_domain.get("label") or "target",
        "sourceSize": len(source_entities),
        "targetSize": len(target_entities),
        "mappings": mappings,
        "unmapped": unmapped,
        "quality": {
            "averageSimilarity": average,
            "sourceCoverage": len(used_sources) / len(source_entities) if source_entities else 0,
            "targetCoverage": len(used_targets) / len(target_entities) if target_entities else 0,
            "structuralPreservation": preservation,
            "mappedCount": len(mappings),
            "unmappedCount": len(unmapped),
        },
        "options": resolved,
        "sourceRelations": source_relations,
        "targetRelations": target_relations,
    }
