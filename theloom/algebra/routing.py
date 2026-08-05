"""Type registry, morphisms, composer, router, and metapath engine.

Categories: structural (boolean), epistemic (viterbi), causal (tropical —
also the fallthrough). Six cross-category morphisms only; the two approximate
ones use τ=3 (exp(-d/3) and -3·ln(p) with a negative-zero guard). The router
picks metapath > default-homogeneous(no types) > product > homogeneous >
segmented; executeSegmented applies morphisms AND composer rules, the
metapath engine applies morphisms only.
"""

from __future__ import annotations

import math
from typing import Any

from theloom.algebra.core import (
    BOOLEAN,
    DEFAULT_MAX_DEPTH,
    INF,
    TROPICAL,
    VITERBI,
    Doc,
    Extractor,
    Semiring,
    boolean_extractor,
    resolve_semiring_router,
    tropical_extractor,
    viterbi_extractor,
)

STRUCTURAL_TYPES = {
    "related_to",
    "instance_of",
    "part_of",
    "sources",
    "calls",
    "references",
    "crystallized_from",
}
EPISTEMIC_TYPES = {"supports", "contradicts", "questions", "supersedes"}
TAU = 3


def relation_category(relation_type: str) -> str:
    if relation_type in STRUCTURAL_TYPES:
        return "structural"
    if relation_type in EPISTEMIC_TYPES:
        return "epistemic"
    return "causal"


def category_semiring_name(category: str) -> str:
    return {"structural": "boolean", "epistemic": "viterbi"}.get(category, "tropical")


def semiring_for_category(category: str) -> tuple[Semiring, Extractor]:
    if category == "structural":
        return BOOLEAN, boolean_extractor
    if category == "epistemic":
        return VITERBI, viterbi_extractor
    return TROPICAL, tropical_extractor


def _viterbi_to_tropical(p: Any) -> float:
    if not isinstance(p, int | float) or p <= 0:
        return INF
    result = -TAU * math.log(p)
    return 0.0 if result == 0 else result


MORPHISMS: dict[str, Doc] = {
    "structural_to_causal": {
        "name": "booleanToTropical",
        "grade": "strict",
        "transform": lambda b: 0 if b else INF,
    },
    "causal_to_structural": {
        "name": "tropicalToBoolean",
        "grade": "strict",
        "transform": lambda d: isinstance(d, int | float) and d < INF,
    },
    "structural_to_epistemic": {
        "name": "booleanToViterbi",
        "grade": "strict",
        "transform": lambda b: 1 if b else 0,
    },
    "epistemic_to_structural": {
        "name": "viterbiToBoolean",
        "grade": "strict",
        "transform": lambda p: isinstance(p, int | float) and p > 0,
    },
    "causal_to_epistemic": {
        "name": "tropicalToViterbi",
        "grade": "approximate",
        "transform": lambda d: (
            math.exp(-d / TAU) if isinstance(d, int | float) and math.isfinite(d) else 0
        ),
    },
    "epistemic_to_causal": {
        "name": "viterbiToTropical",
        "grade": "approximate",
        "transform": _viterbi_to_tropical,
    },
}


def categorize(relation_types: list[str]) -> list[str]:
    """Unique categories in first-appearance order."""
    seen: dict[str, None] = {}
    for relation_type in relation_types:
        seen.setdefault(relation_category(relation_type))
    return list(seen)


# =============================================================================
# Composer — segmented routing only
# =============================================================================


def _normalize_causal_score(x: Any) -> float:
    if not isinstance(x, int | float) or not math.isfinite(x):
        return 0.0
    if x >= 1:
        return math.exp(-x / TAU)
    if x >= 0:
        return float(x)
    return 0.0


def apply_composer_rules(
    value: Any, from_category: str, to_category: str, source_segment_value: Any
) -> tuple[Any, list[str]]:
    if from_category == to_category:
        return value, []
    triggered: list[str] = []
    result = value
    if from_category == "causal" and to_category == "epistemic" and isinstance(result, int | float):
        if result <= 0:
            composed: float = 0.0
        elif result >= 1:
            composed = 1.0
        else:
            k = 1 + _normalize_causal_score(source_segment_value)
            composed = float(result ** (1 / k))
        if composed != result:
            triggered.append("causal_strengthens_epistemic")
        result = composed
    if from_category == "epistemic" and to_category == "causal" and isinstance(result, int | float):
        composed = result * 0.8
        if composed != result:
            triggered.append("epistemic_decays_without_causal")
        result = composed
    return result, triggered


# =============================================================================
# Router
# =============================================================================


def analyze_query(
    relation_types: list[str] | None,
    metapath: Doc | None = None,
    product_mode: bool | None = None,
) -> Doc:
    if metapath and metapath.get("steps"):
        categories: dict[str, None] = {}
        rtypes: list[str] = []
        for step in metapath["steps"]:
            if step.get("relationType"):
                categories.setdefault(relation_category(step["relationType"]))
                rtypes.append(step["relationType"])
            elif step.get("relationCategory"):
                categories.setdefault(step["relationCategory"])
        return {
            "strategy": "metapath",
            "metapath": metapath,
            "categories": list(categories),
            "relationTypes": rtypes,
            "crossTypeRules": len(categories) > 1,
        }
    if not relation_types:
        return {
            "strategy": "homogeneous",
            "semiring": "tropical",
            "extractor": "tropical",
            "categories": [],
            "relationTypes": [],
            "crossTypeRules": False,
        }
    categories_list = categorize(relation_types)
    if product_mode and len(categories_list) == 2:
        return {
            "strategy": "product",
            "productPair": [
                category_semiring_name(categories_list[0]),
                category_semiring_name(categories_list[1]),
            ],
            "categories": categories_list,
            "relationTypes": relation_types,
            "crossTypeRules": False,
        }
    if len(categories_list) <= 1:
        category = categories_list[0] if categories_list else "causal"
        return {
            "strategy": "homogeneous",
            "semiring": category_semiring_name(category),
            "extractor": category_semiring_name(category),
            "categories": categories_list,
            "relationTypes": relation_types,
            "crossTypeRules": False,
        }
    segments = []
    for index, category in enumerate(categories_list):
        next_category = categories_list[index + 1] if index + 1 < len(categories_list) else None
        boundary = None
        if next_category:
            morphism = MORPHISMS.get(f"{category}_to_{next_category}")
            if morphism is None:
                raise ValueError(f"No morphism registered for {category} -> {next_category}")
            boundary = morphism["name"]
        segments.append(
            {
                "category": category,
                "semiring": category_semiring_name(category),
                "relationTypes": [t for t in relation_types if relation_category(t) == category],
                "boundaryMorphism": boundary,
            }
        )
    return {
        "strategy": "segmented",
        "segments": segments,
        "categories": categories_list,
        "relationTypes": relation_types,
        "crossTypeRules": True,
    }


def execute_homogeneous(
    entities: list[Doc],
    relations: list[Doc],
    source_id: str,
    target_id: str | None,
    plan: Doc,
    max_depth: int | None,
    mode: str | None,
) -> Doc:
    from theloom.algebra.core import single_source

    semiring, extractor = resolve_semiring_router(plan.get("semiring"))
    relation_types = plan.get("relationTypes") or None
    results = single_source(
        entities,
        relations,
        source_id,
        semiring,
        extractor,
        mode=mode or "ACYCLIC",
        max_depth=max_depth if max_depth is not None else DEFAULT_MAX_DEPTH,
        relation_types=relation_types,
    )
    if target_id is not None:
        narrowed: dict[str, Doc] = {source_id: {"value": semiring.one, "path": []}}
        if target_id in results:
            narrowed[target_id] = results[target_id]
        return {"plan": plan, "results": narrowed}
    return {"plan": plan, "results": results}


def execute_segmented(
    entities: list[Doc],
    relations: list[Doc],
    source_id: str,
    target_id: str | None,
    plan: Doc,
    max_depth: int | None,
) -> Doc:
    depth_limit = max_depth if max_depth is not None else DEFAULT_MAX_DEPTH
    adjacency: dict[str, list[Doc]] = {}
    for rel in relations:
        adjacency.setdefault(rel["from"], []).append(rel)
    allowed = set(plan.get("relationTypes") or [])
    segments_plan = plan.get("segments") or []
    initial_category = segments_plan[0]["category"] if segments_plan else "causal"
    initial_semiring, _ = semiring_for_category(initial_category)

    results: dict[str, Doc] = {source_id: {"value": initial_semiring.one, "path": []}}
    morphisms_applied: list[Doc] = []
    rules_triggered: list[str] = []
    collected_segments: list[Doc] = []
    seen_segment_keys: set[str] = set()
    target_found = False

    def close_segment(segment: Doc) -> None:
        key = f"{segment['category']}::{segment['semiringName']}"
        if key not in seen_segment_keys:
            seen_segment_keys.add(key)
            collected_segments.append(segment)

    def dfs(
        node: str,
        value: Any,
        path: list[Doc],
        visited: frozenset[str],
        depth: int,
        category: str,
        seg_value: Any,
        seg_entities: list[str],
        path_segments: list[Doc],
    ) -> None:
        nonlocal target_found
        if target_found or depth >= depth_limit:
            return
        for rel in adjacency.get(node, []):
            if target_found:
                return
            if allowed and rel["relationType"] not in allowed:
                continue
            if rel["to"] in visited:
                continue
            edge_category = relation_category(rel["relationType"])
            edge_semiring, edge_extractor = semiring_for_category(edge_category)
            edge_weight = edge_extractor(rel)
            new_segments = list(path_segments)
            if edge_category == category:
                current_semiring, _ = semiring_for_category(category)
                new_value = current_semiring.times(value, edge_weight)
                new_seg_value = (
                    current_semiring.times(seg_value, edge_weight)
                    if seg_value is not None
                    else edge_weight
                )
                new_seg_entities = [*seg_entities, rel["to"]]
            else:
                morphism = MORPHISMS.get(f"{category}_to_{edge_category}")
                if morphism is None:
                    continue
                morphed = morphism["transform"](value)
                morphisms_applied.append(
                    {
                        "from": category,
                        "to": edge_category,
                        "morphismName": morphism["name"],
                        "grade": morphism["grade"],
                    }
                )
                new_segments.append(
                    {
                        "category": category,
                        "semiringName": category_semiring_name(category),
                        "value": seg_value,
                        "transformedValue": morphed,
                        "entities": seg_entities,
                    }
                )
                if plan.get("crossTypeRules") and isinstance(morphed, int | float):
                    composed, triggered = apply_composer_rules(
                        morphed, category, edge_category, seg_value
                    )
                    for rule in triggered:
                        if rule not in rules_triggered:
                            rules_triggered.append(rule)
                    morphed = composed
                new_value = edge_semiring.times(morphed, edge_weight)
                new_seg_value = edge_weight
                new_seg_entities = [rel["to"]]

            step = {
                "from": node,
                "to": rel["to"],
                "relationId": rel["id"],
                "relationType": rel["relationType"],
            }
            new_path = [*path, step]
            existing = results.get(rel["to"])
            if existing is not None:
                combined = edge_semiring.plus(existing["value"], new_value)
                new_is_better = combined == new_value and new_value != existing["value"]
                results[rel["to"]] = {
                    "value": combined,
                    "path": new_path if new_is_better else existing["path"],
                }
            else:
                results[rel["to"]] = {"value": new_value, "path": new_path}

            is_leaf = not adjacency.get(rel["to"])
            if is_leaf or rel["to"] == target_id:
                final_segments = [
                    *new_segments,
                    {
                        "category": edge_category,
                        "semiringName": category_semiring_name(edge_category),
                        "value": new_seg_value,
                        "entities": new_seg_entities,
                    },
                ]
                for segment in final_segments:
                    close_segment(segment)
            if rel["to"] == target_id:
                target_found = True
                return
            dfs(
                rel["to"],
                new_value,
                new_path,
                visited | {rel["to"]},
                depth + 1,
                edge_category,
                new_seg_value,
                new_seg_entities,
                new_segments,
            )

    dfs(
        source_id,
        initial_semiring.one,
        [],
        frozenset({source_id}),
        0,
        initial_category,
        None,
        [source_id],
        [],
    )
    result: Doc = {
        "plan": plan,
        "results": results,
        "crossTypeMetadata": {
            "morphismsApplied": morphisms_applied,
            "rulesTriggered": rules_triggered,
        },
    }
    if collected_segments:
        result["segmentDetails"] = collected_segments
    return result


def execute_routing_plan(
    entities: list[Doc],
    relations: list[Doc],
    source_id: str,
    target_id: str | None,
    plan: Doc,
    max_depth: int | None,
    mode: str | None,
) -> Doc:
    strategy = plan.get("strategy")
    if strategy == "homogeneous":
        return execute_homogeneous(entities, relations, source_id, target_id, plan, max_depth, mode)
    if strategy == "segmented":
        return execute_segmented(entities, relations, source_id, target_id, plan, max_depth)
    if strategy in ("metapath", "product"):
        engine = "metapath" if strategy == "metapath" else "product"
        raise ValueError(
            f"Strategy '{strategy}' not supported by executeRoutingPlan. "
            f"Use dedicated {engine} engine."
        )
    raise ValueError(f"Unknown routing strategy: '{strategy}'")


# =============================================================================
# Metapath engine
# =============================================================================


def parse_metapath_string(text: str) -> Doc:
    steps = []
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        left, _, right = part.partition(":")
        step: Doc = {}
        if left.startswith("@"):
            step["relationCategory"] = left[1:]
        else:
            step["relationType"] = left
        if right:
            step["targetEntityType"] = right
        steps.append(step)
    return {"steps": steps}


def traverse_metapath(
    entities: list[Doc],
    relations: list[Doc],
    source_id: str,
    metapath: Doc,
    target_id: str | None,
) -> list[Doc]:
    entity_map = {e["id"]: e for e in entities}
    adjacency: dict[str, list[Doc]] = {}
    for rel in relations:
        adjacency.setdefault(rel["from"], []).append(rel)

    source = entity_map.get(source_id)
    if source is None:
        return []
    if metapath.get("sourceEntityType") and source["entityType"] != metapath["sourceEntityType"]:
        return []
    steps = metapath.get("steps") or []
    if not steps:
        return []

    frontier: list[Doc] = [
        {
            "value": None,
            "path": [
                {
                    "entityId": source_id,
                    "entityName": source["name"],
                    "relationId": None,
                    "relationType": None,
                    "category": None,
                    "segmentValue": None,
                }
            ],
            "segments": [],
            "currentCategory": None,
            "currentSegmentValue": None,
            "currentSegmentEntities": [source_id],
            "terminal": source_id,
        }
    ]
    for step in steps:
        next_frontier: list[Doc] = []
        for entry in frontier:
            for rel in adjacency.get(entry["terminal"], []):
                if step.get("relationType") and rel["relationType"] != step["relationType"]:
                    continue
                edge_category = relation_category(rel["relationType"])
                if step.get("relationCategory") and edge_category != step["relationCategory"]:
                    continue
                target_entity = entity_map.get(rel["to"])
                if target_entity is None:
                    continue
                if (
                    step.get("targetEntityType")
                    and target_entity["entityType"] != step["targetEntityType"]
                ):
                    continue
                semiring, extractor = semiring_for_category(edge_category)
                edge_weight = extractor(rel)
                new_segments = list(entry["segments"])
                previous_category = entry["currentCategory"]
                if previous_category is not None and previous_category != edge_category:
                    morphism = MORPHISMS.get(f"{previous_category}_to_{edge_category}")
                    if morphism is None:
                        raise ValueError(
                            f"No morphism registered for {previous_category} -> {edge_category}"
                        )
                    transformed = morphism["transform"](entry["value"])
                    step_value = semiring.times(transformed, edge_weight)
                    new_segments.append(
                        {
                            "category": previous_category,
                            "semiringName": category_semiring_name(previous_category),
                            "value": entry["currentSegmentValue"],
                            "transformedValue": transformed,
                            "entities": entry["currentSegmentEntities"],
                        }
                    )
                    new_segment_value: Any = edge_weight
                    new_segment_entities = [rel["to"]]
                elif entry["value"] is not None:
                    step_value = semiring.times(entry["value"], edge_weight)
                    new_segment_value = (
                        semiring.times(entry["currentSegmentValue"], edge_weight)
                        if entry["currentSegmentValue"] is not None
                        else edge_weight
                    )
                    new_segment_entities = [*entry["currentSegmentEntities"], rel["to"]]
                else:
                    step_value = edge_weight
                    new_segment_value = edge_weight
                    new_segment_entities = [*entry["currentSegmentEntities"], rel["to"]]

                path_step = {
                    "entityId": rel["to"],
                    "entityName": target_entity["name"],
                    "relationId": rel["id"],
                    "relationType": rel["relationType"],
                    "category": edge_category,
                    "segmentValue": new_segment_value,
                }
                next_frontier.append(
                    {
                        "value": step_value,
                        "path": [*entry["path"], path_step],
                        "segments": new_segments,
                        "currentCategory": edge_category,
                        "currentSegmentValue": new_segment_value,
                        "currentSegmentEntities": new_segment_entities,
                        "terminal": rel["to"],
                    }
                )
        frontier = next_frontier

    results: list[Doc] = []
    for entry in frontier:
        if target_id and entry["terminal"] != target_id:
            continue
        segments = list(entry["segments"])
        if entry["currentCategory"] is not None:
            segments.append(
                {
                    "category": entry["currentCategory"],
                    "semiringName": category_semiring_name(entry["currentCategory"]),
                    "value": entry["currentSegmentValue"],
                    "entities": entry["currentSegmentEntities"],
                }
            )
        results.append(
            {
                "source": source_id,
                "terminal": entry["terminal"],
                "value": entry["value"],
                "segments": segments,
                "path": entry["path"],
            }
        )
    return results
