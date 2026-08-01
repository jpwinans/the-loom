"""The 12 semiring-composition and adaptive-routing commands.

Store-read asymmetry by design: semiring-* commands traverse
lazily (readEntity/getRelations — any status), while transitive-closure /
adaptive-* / metapath / cross-type validate against listEntities (active
only) but read ALL relations. Path tie-breaking, sort quirks (distances has
no NaN guard; adaptive-distances does), and the single-source
"first non-source entry" behavior are all intentional.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from theloom.algebra import core, routing
from theloom.algebra.core import (
    BOOLEAN,
    CAPACITY,
    COUNTING,
    VITERBI,
    Doc,
    boolean_extractor,
    capacity_extractor,
    counting_extractor,
    viterbi_extractor,
)
from theloom.errors import NotFoundError, OperationError
from theloom.model import EntityType, RelationType
from theloom.operations.common import CommandInput, UuidStr
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

MAX_DEPTH_LIMIT = 10
_PATH_MODES = ("WALK", "TRAIL", "ACYCLIC", "SIMPLE")
_SEMIRING_NAMES = ("boolean", "tropical", "tropical-uniform", "viterbi", "counting", "capacity")
_CATEGORY_NAMES = ("structural", "epistemic", "causal")


def _require_source(store: FalkorGraphStore, entity_id: str) -> None:
    if store.read_entity(entity_id) is None:
        raise NotFoundError(f"Source entity '{entity_id}' not found in graph.")


def _require_target(store: FalkorGraphStore, entity_id: str) -> None:
    if store.read_entity(entity_id) is None:
        raise NotFoundError(f"Target entity '{entity_id}' not found in graph.")


def _docs(store: FalkorGraphStore) -> tuple[list[Doc], list[Doc]]:
    entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    return entities, relations


def _resolve_named(name: str) -> tuple[core.Semiring, core.Extractor]:
    try:
        return core.resolve_semiring(name)
    except ValueError as exc:
        raise OperationError(str(exc)) from None


# =============================================================================
# Input models
# =============================================================================


class MetapathStep(CommandInput):
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    relation_category: str | None = Field(default=None, alias="relationCategory")
    target_entity_type: EntityType | None = Field(default=None, alias="targetEntityType")


class MetapathSpec(CommandInput):
    name: str | None = None
    source_entity_type: EntityType | None = Field(default=None, alias="sourceEntityType")
    steps: list[MetapathStep]


SemiringName = Literal["boolean", "tropical", "tropical-uniform", "viterbi", "counting", "capacity"]
PathMode = Literal["WALK", "TRAIL", "ACYCLIC", "SIMPLE"]


class SemiringTraverseInput(CommandInput):
    source: UuidStr
    target: UuidStr
    semiring: SemiringName | None = None
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    mode: PathMode | None = None
    graph: str | None = None


class SemiringDistancesInput(CommandInput):
    source: UuidStr
    semiring: SemiringName
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    mode: PathMode | None = None
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None
    direction: str | None = None


class SourceTargetInput(CommandInput):
    source: UuidStr
    target: UuidStr
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    mode: PathMode | None = None
    graph: str | None = None


class CountPathsInput(CommandInput):
    source: UuidStr
    target: UuidStr
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    graph: str | None = None


class TransitiveClosureInput(CommandInput):
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    graph: str | None = None


class AdaptiveTraverseInput(CommandInput):
    source: UuidStr
    target: UuidStr | None = None
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    path_mode: str | None = Field(default=None, alias="pathMode")
    product_mode: bool | None = Field(default=None, alias="productMode")
    graph: str | None = None


class AdaptiveDistancesInput(CommandInput):
    source: UuidStr
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    path_mode: str | None = Field(default=None, alias="pathMode")
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None


class MetapathTraverseInput(CommandInput):
    source: UuidStr
    metapath: str | MetapathSpec
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    source_entity_type: EntityType | None = Field(default=None, alias="sourceEntityType")
    target: UuidStr | None = None
    graph: str | None = None


class CrossTypeQueryInput(CommandInput):
    source: UuidStr
    target: UuidStr | None = None
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    path_mode: str | None = Field(default=None, alias="pathMode")
    graph: str | None = None


class TypeAnalyzeInput(CommandInput):
    source: UuidStr | None = None
    target: UuidStr | None = None
    relation_types: list[RelationType] | None = Field(default=None, alias="relationTypes")
    metapath: str | MetapathSpec | None = None
    graph: str | None = None


def _relation_type_values(types: list[RelationType] | None) -> list[str] | None:
    return [t.value for t in types] if types is not None else None


def _metapath_doc(value: str | MetapathSpec) -> Doc:
    if isinstance(value, str):
        return routing.parse_metapath_string(value)
    doc = value.model_dump(by_alias=True, exclude_unset=True)
    doc["steps"] = [
        {k: (v.value if hasattr(v, "value") else v) for k, v in step.items()}
        for step in doc.get("steps", [])
    ]
    return doc


# =============================================================================
# semiring-* (lazy traversal, any-status entities)
# =============================================================================


def semiring_traverse(params: SemiringTraverseInput, multi: MultiGraph) -> Doc | None:
    store = multi.get_store(params.graph)
    semiring, extractor = _resolve_named(params.semiring or "tropical")
    _require_source(store, params.source)
    _require_target(store, params.target)
    results = core.lazy_single_source(
        store,
        params.source,
        semiring,
        extractor,
        mode=params.mode or "ACYCLIC",
        max_depth=params.max_depth if params.max_depth is not None else 10,
        relation_types=_relation_type_values(params.relation_types),
    )
    result = results.get(params.target)
    if result is None:
        return None
    return {
        "value": result["value"],
        "path": result["path"],
        "semiring": params.semiring or "tropical",
    }


def semiring_distances(params: SemiringDistancesInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    semiring, extractor = _resolve_named(params.semiring)
    _require_source(store, params.source)
    results = core.lazy_single_source(
        store,
        params.source,
        semiring,
        extractor,
        mode=params.mode or "ACYCLIC",
        max_depth=params.max_depth if params.max_depth is not None else 10,
        relation_types=_relation_type_values(params.relation_types),
        direction=params.direction or "outgoing",
    )
    distances = []
    for entity_id, result in results.items():
        if entity_id == params.source:
            continue
        entity = store.read_entity(entity_id)
        distances.append(
            {
                "entityId": entity_id,
                "entityName": entity.name if entity else entity_id,
                "value": result["value"],
                "path": result["path"],
            }
        )
    ascending = params.semiring in ("tropical", "tropical-uniform")
    # No NaN guard here — the semiring-distances comparator is intentionally bare.
    distances.sort(key=lambda d: float(str(d["value"])) if ascending else -float(str(d["value"])))
    if params.limit is not None and params.limit < len(distances):
        distances = distances[: params.limit]
    return {"source": params.source, "semiring": params.semiring, "distances": distances}


def semiring_reachable(params: SourceTargetInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    _require_source(store, params.source)
    _require_target(store, params.target)
    results = core.lazy_single_source(
        store,
        params.source,
        BOOLEAN,
        boolean_extractor,
        mode=params.mode or "ACYCLIC",
        max_depth=params.max_depth if params.max_depth is not None else 10,
        relation_types=_relation_type_values(params.relation_types),
    )
    result = results.get(params.target)
    if not result or not result["value"]:
        return {"reachable": False, "path": None}
    return {"reachable": True, "path": result["path"]}


def semiring_most_confident(params: SourceTargetInput, multi: MultiGraph) -> Doc | None:
    store = multi.get_store(params.graph)
    _require_source(store, params.source)
    _require_target(store, params.target)
    results = core.lazy_single_source(
        store,
        params.source,
        VITERBI,
        viterbi_extractor,
        mode=params.mode or "ACYCLIC",
        max_depth=params.max_depth if params.max_depth is not None else 10,
        relation_types=_relation_type_values(params.relation_types),
    )
    result = results.get(params.target)
    if result is None:
        return None
    return {"confidence": result["value"], "path": result["path"]}


def semiring_count_paths(params: CountPathsInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    _require_source(store, params.source)
    _require_target(store, params.target)
    results = core.lazy_single_source(
        store,
        params.source,
        COUNTING,
        counting_extractor,
        mode="ACYCLIC",
        max_depth=params.max_depth if params.max_depth is not None else MAX_DEPTH_LIMIT,
        relation_types=_relation_type_values(params.relation_types),
    )
    result = results.get(params.target)
    return {
        "count": result["value"] if result else 0,
        "bounded": params.max_depth is None,
    }


def semiring_bottleneck(params: SourceTargetInput, multi: MultiGraph) -> Doc | None:
    store = multi.get_store(params.graph)
    _require_source(store, params.source)
    _require_target(store, params.target)
    results = core.lazy_single_source(
        store,
        params.source,
        CAPACITY,
        capacity_extractor,
        mode=params.mode or "ACYCLIC",
        max_depth=params.max_depth if params.max_depth is not None else 10,
        relation_types=_relation_type_values(params.relation_types),
    )
    result = results.get(params.target)
    if result is None:
        return None
    minimum = float("inf")
    bottleneck_relation: Doc | None = None
    for step in result["path"]:
        candidates = store.read_relations(step["from"], step["to"], step["relationType"])
        docs = [r.model_dump(by_alias=True, exclude_unset=True) for r in candidates]
        rel = next((r for r in docs if r["id"] == step["relationId"]), None)
        if rel is None:
            rel = docs[0] if docs else None
        if rel is None:
            continue
        edge_capacity = capacity_extractor(rel)
        if edge_capacity < minimum:
            minimum = edge_capacity
            bottleneck_relation = {
                "from": step["from"],
                "to": step["to"],
                "relationType": rel["relationType"],
                "strength": rel["strength"],
            }
    return {
        "bottleneckValue": 0 if minimum == float("inf") else minimum,
        "bottleneckRelation": bottleneck_relation,
        "path": result["path"],
        "pathCapacity": result["value"],
    }


def transitive_closure(params: TransitiveClosureInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _docs(store)
    name_map = {e["id"]: e["name"] for e in entities}
    relation_types = [params.relation_type.value] if params.relation_type else None
    pairs = []
    for entity in entities:
        results = core.single_source(
            entities,
            relations,
            entity["id"],
            BOOLEAN,
            boolean_extractor,
            mode="WALK",
            max_depth=params.max_depth if params.max_depth is not None else 10,
            relation_types=relation_types,
        )
        for target_id, result in results.items():
            if target_id == entity["id"] or result["value"] is not True:
                continue
            pairs.append(
                {
                    "source": entity["id"],
                    "sourceName": entity["name"],
                    "target": target_id,
                    "targetName": name_map.get(target_id, target_id),
                }
            )
    return {"pairs": pairs, "entityCount": len(entities), "pairCount": len(pairs)}


# =============================================================================
# adaptive-* / metapath / cross-type / type-analyze
# =============================================================================


def _first_non_source(results: dict[str, Doc], source_id: str) -> tuple[Any, list[Doc]]:
    for entity_id, result in results.items():
        if entity_id != source_id:
            return result["value"], result["path"]
    return None, []


def adaptive_traverse(params: AdaptiveTraverseInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _docs(store)
    active_ids = {e["id"] for e in entities}
    if params.source not in active_ids:
        raise NotFoundError(f"Source entity '{params.source}' not found in graph.")
    if params.target is not None and params.target not in active_ids:
        raise NotFoundError(f"Target entity '{params.target}' not found in graph.")
    plan = routing.analyze_query(
        _relation_type_values(params.relation_types), product_mode=params.product_mode
    )
    if plan["strategy"] == "homogeneous" and plan.get("semiring"):
        semiring, extractor = _resolve_named(plan["semiring"])
        results = core.single_source(
            entities,
            relations,
            params.source,
            semiring,
            extractor,
            mode=params.path_mode or "ACYCLIC",
            max_depth=params.max_depth if params.max_depth is not None else 10,
            relation_types=_relation_type_values(params.relation_types),
        )
        if params.target is not None:
            result = results.get(params.target)
            if result is None:
                return {"plan": plan, "value": semiring.zero, "path": []}
            return {"plan": plan, "value": result["value"], "path": result["path"]}
        value, path = _first_non_source(results, params.source)
        return {"plan": plan, "value": value, "path": path}

    try:
        executed = routing.execute_routing_plan(
            entities,
            relations,
            params.source,
            params.target,
            plan,
            params.max_depth,
            params.path_mode,
        )
    except ValueError as exc:
        raise OperationError(str(exc)) from None
    results = executed["results"]
    metadata = executed.get("crossTypeMetadata")
    if params.target is not None:
        result = results.get(params.target)
        row: Doc = {
            "plan": executed["plan"],
            "value": result["value"] if result else None,
            "path": result["path"] if result else [],
        }
    else:
        value, path = _first_non_source(results, params.source)
        row = {"plan": executed["plan"], "value": value, "path": path}
    if metadata is not None:
        row["crossTypeMetadata"] = metadata
    return row


def adaptive_distances(params: AdaptiveDistancesInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _docs(store)
    if params.source not in {e["id"] for e in entities}:
        raise NotFoundError(f"Source entity '{params.source}' not found in graph.")
    entity_map = {e["id"]: e for e in entities}
    plan = routing.analyze_query(_relation_type_values(params.relation_types))
    try:
        executed = routing.execute_routing_plan(
            entities,
            relations,
            params.source,
            None,
            plan,
            params.max_depth,
            params.path_mode,
        )
    except ValueError as exc:
        raise OperationError(str(exc)) from None
    distances = []
    for entity_id, result in executed["results"].items():
        if entity_id == params.source:
            continue
        entity = entity_map.get(entity_id)
        distances.append(
            {
                "entityId": entity_id,
                "entityName": entity["name"] if entity else entity_id,
                "value": result["value"],
                "path": result["path"],
            }
        )
    ascending = executed["plan"].get("semiring") in ("tropical", "tropical-uniform")

    def sort_key(row: Doc) -> tuple[int, float]:
        value = float(row["value"]) if isinstance(row["value"], int | float) else float("nan")
        if value != value:  # NaN sorts to the end (adaptive HAS the guard)
            return (1, 0.0)
        return (0, value if ascending else -value)

    distances.sort(key=sort_key)
    if params.limit is not None and params.limit < len(distances):
        distances = distances[: params.limit]
    return {"source": params.source, "plan": executed["plan"], "distances": distances}


def metapath_traverse(params: MetapathTraverseInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _docs(store)
    if params.source not in {e["id"] for e in entities}:
        raise NotFoundError(f"Source entity '{params.source}' not found in graph.")
    metapath = _metapath_doc(params.metapath)
    if params.source_entity_type is not None:
        metapath = {**metapath, "sourceEntityType": params.source_entity_type.value}
    entity_map = {e["id"]: e for e in entities}
    raw_results = routing.traverse_metapath(
        entities, relations, params.source, metapath, params.target
    )
    results = [
        {
            "terminal": r["terminal"],
            "terminalName": entity_map.get(r["terminal"], {}).get("name", r["terminal"]),
            "value": r["value"],
            "segments": r["segments"],
            "path": r["path"],
        }
        for r in raw_results
    ]
    return {"source": params.source, "metapath": metapath, "results": results}


def cross_type_query(params: CrossTypeQueryInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _docs(store)
    active_ids = {e["id"] for e in entities}
    if params.source not in active_ids:
        raise NotFoundError(f"Source entity '{params.source}' not found in graph.")
    if params.target is not None and params.target not in active_ids:
        raise NotFoundError(f"Target entity '{params.target}' not found in graph.")
    base_plan = routing.analyze_query(_relation_type_values(params.relation_types))
    if base_plan["strategy"] == "homogeneous":
        categories = base_plan.get("categories") or []
        plan = {
            **base_plan,
            "strategy": "segmented",
            "crossTypeRules": True,
            "segments": [
                {
                    "category": categories[0] if categories else "causal",
                    "semiring": base_plan.get("semiring") or "tropical",
                    "relationTypes": base_plan.get("relationTypes"),
                    "boundaryMorphism": None,
                }
            ],
        }
    else:
        plan = {**base_plan, "crossTypeRules": True}
    try:
        executed = routing.execute_routing_plan(
            entities,
            relations,
            params.source,
            params.target,
            plan,
            params.max_depth,
            params.path_mode,
        )
    except ValueError as exc:
        raise OperationError(str(exc)) from None
    metadata = executed.get("crossTypeMetadata") or {
        "morphismsApplied": [],
        "rulesTriggered": [],
    }
    results = executed["results"]
    if params.target is not None:
        result = results.get(params.target)
        return {
            "plan": executed["plan"],
            "value": result["value"] if result else None,
            "path": result["path"] if result else [],
            "crossTypeMetadata": metadata,
        }
    value, path = _first_non_source(results, params.source)
    return {
        "plan": executed["plan"],
        "value": value,
        "path": path,
        "crossTypeMetadata": metadata,
    }


def type_analyze(params: TypeAnalyzeInput, multi: MultiGraph) -> Doc:
    multi.get_store(params.graph)  # resolved for consistency; data unused
    metapath = _metapath_doc(params.metapath) if params.metapath is not None else None
    return routing.analyze_query(_relation_type_values(params.relation_types), metapath)
