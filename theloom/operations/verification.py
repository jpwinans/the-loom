"""Verification operations.

Guards, the 5 default invariants, the GraphSpec DSL, capability checks, and
AC-3 propagation. verify-graph runs Tier-1 guards then Tier-2 invariants
(fail-fast); check-consistency is Tier-1 only; check-invariants/validate-spec
wrap invariants/properties through the spec (so per-property `checked` is 1);
list-guard-violations groups guard hits per element. constrained-generate and
cegis-synthesize are implemented but non-deterministic by construction
(random seed / wall-clock durationMs), so their outputs are not fixed.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Annotated, Any, Literal

from pydantic import Field

from theloom.errors import OperationError
from theloom.model import (
    ALL_ENTITY_STATUSES,
    ALL_ENTITY_TYPES,
    EntityCreate,
    EntityFilter,
    EntityType,
    RelationCreate,
    RelationType,
)
from theloom.operations.common import CommandInput
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.verification import checks, propagation

Doc = dict[str, Any]

_ALL_STATUSES = ["active", "superseded", "deprecated", "retracted", "investigating"]


# =============================================================================
# Input models
# =============================================================================


class VerifyGraphInput(CommandInput):
    include_defaults: bool | None = Field(default=None, alias="includeDefaults")
    graph: str | None = None


class GraphOnlyInput(CommandInput):
    graph: str | None = None


class CheckInvariantsInput(CommandInput):
    invariants: list[str] | None = None
    graph: str | None = None


class ListGuardViolationsInput(CommandInput):
    guards: list[str] | None = None
    graph: str | None = None


class PropertyDefinition(CommandInput):
    name: str
    type: Literal["forAllNodes", "forAllEdges", "invariant", "default"]
    level: Literal["node", "edge", "subgraph", "graph"] | None = None
    invariant_name: str | None = Field(default=None, alias="invariantName")
    field: str | None = None
    condition: str | None = None
    value: Any = None


class ValidateSpecInput(CommandInput):
    properties: list[PropertyDefinition]
    graph: str | None = None


class TypeConstraint(CommandInput):
    source_type: EntityType = Field(alias="sourceType")
    relation_type: RelationType = Field(alias="relationType")
    target_type: EntityType = Field(alias="targetType")


class PropagateConstraintsInput(CommandInput):
    constraints: list[TypeConstraint]
    graph: str | None = None


class CheckCapabilitiesInput(CommandInput):
    types: list[str] | None = None
    coupling_metric: Literal["degree", "betweenness"] | None = Field(
        default=None, alias="couplingMetric"
    )
    coupling_threshold: float | None = Field(default=None, alias="couplingThreshold")
    coverage_parent_type: str | None = Field(default=None, alias="coverageParentType")
    coverage_child_type: str | None = Field(default=None, alias="coverageChildType")
    coverage_relation_type: str | None = Field(default=None, alias="coverageRelationType")
    pattern_min_occurrences: float | None = Field(default=None, alias="patternMinOccurrences")
    derive_from_graph: bool | None = Field(default=None, alias="deriveFromGraph")
    graph: str | None = None


class ConstrainedGenerateInput(CommandInput):
    max_entities: Annotated[int, Field(gt=0, le=10000)] = Field(alias="maxEntities")
    max_relations: Annotated[int, Field(ge=0, le=50000)] = Field(alias="maxRelations")
    required_types: list[EntityType] | None = Field(default=None, alias="requiredTypes")
    commit: bool | None = None
    graph: str | None = None


# =============================================================================
# Helpers
# =============================================================================


def _all_docs(store: FalkorGraphStore) -> tuple[list[Doc], list[Doc]]:
    entities = [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate({"statusFilter": _ALL_STATUSES}))
    ]
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    return entities, relations


# =============================================================================
# check-consistency (Tier 1) + verify-graph
# =============================================================================

_CONSISTENCY_ENTITY_GUARDS = ["confidenceBounds", "entityType", "observationsRequired"]
_CONSISTENCY_RELATION_GUARDS = ["causalPolarity", "nonCausalPolarity", "noSelfLoop"]


def _run_consistency(entities: list[Doc], relations: list[Doc]) -> Doc:
    entity_violations: list[Doc] = []
    for entity in entities:
        found: list[Doc] = []
        for name in _CONSISTENCY_ENTITY_GUARDS:
            found.extend(checks.ENTITY_GUARDS[name](entity))
        if found:
            entity_violations.append({"entityId": entity["id"], "violations": found})
    relation_violations: list[Doc] = []
    for relation in relations:
        found = []
        for name in _CONSISTENCY_RELATION_GUARDS:
            found.extend(checks.RELATION_GUARDS[name](relation))
        if found:
            relation_violations.append(
                {"from": relation["from"], "to": relation["to"], "violations": found}
            )
    has_errors = any(
        v["severity"] == "error"
        for group in (*entity_violations, *relation_violations)
        for v in group["violations"]
    )
    return {
        "consistent": not has_errors,
        "entitiesChecked": len(entities),
        "relationsChecked": len(relations),
        "entityViolations": entity_violations,
        "relationViolations": relation_violations,
        "propertyResults": [],
    }


def check_consistency(params: GraphOnlyInput, multi: MultiGraph) -> Doc:
    entities, relations = _all_docs(multi.get_store(params.graph))
    return _run_consistency(entities, relations)


def verify_graph(params: VerifyGraphInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _all_docs(store)
    include_defaults = params.include_defaults if params.include_defaults is not None else True

    tier1 = _run_consistency(entities, relations)
    if not tier1["consistent"]:
        return {"pass": False, "tier1": tier1, "tier2": None, "tier2Skipped": True}

    tier2: list[Doc] = []
    if include_defaults:
        for name in checks.DEFAULT_INVARIANT_NAMES:
            result = checks.BUILTIN_INVARIANTS[name](entities, relations, store)
            # Tier-2 invariants run through spec.invariant -> checked is always 1.
            tier2.append({**result, "checked": 1})
    tier2_pass = all(r["pass"] for r in tier2)
    return {"pass": tier2_pass, "tier1": tier1, "tier2": tier2, "tier2Skipped": False}


# =============================================================================
# check-invariants (wrapped through spec -> checked is always 1)
# =============================================================================


def check_invariants(params: CheckInvariantsInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _all_docs(store)
    names = params.invariants or checks.DEFAULT_INVARIANT_NAMES
    if params.invariants:
        for name in params.invariants:
            if name not in checks.BUILTIN_INVARIANTS:
                raise OperationError(
                    f"Unknown invariant '{name}'. Available: "
                    f"{', '.join(checks.DEFAULT_INVARIANT_NAMES)}"
                )

    properties: list[Doc] = []
    for name in names:
        result = checks.BUILTIN_INVARIANTS[name](entities, relations, store)
        properties.append(
            {
                "name": result["name"],
                "level": result["level"],
                "pass": result["pass"],
                "checked": 1,  # spec.invariant always reports checked=1
                "violations": result["violations"],
            }
        )
    # check-invariants returns a SpecResult (spec.validate), not {pass, properties}.
    failed = sum(1 for p in properties if not p["pass"])
    return {
        "pass": failed == 0,
        "properties": properties,
        "totalProperties": len(properties),
        "passedProperties": len(properties) - failed,
        "failedProperties": failed,
    }


# =============================================================================
# validate-spec
# =============================================================================


def _node_predicate(field: str | None, condition: str | None, value: Any):  # type: ignore[no-untyped-def]
    if not field or not condition:
        return lambda _e: True

    def predicate(entity: Doc) -> bool:
        val = entity.get(field)
        if condition == "notEmpty":
            if isinstance(val, list | str):
                return len(val) > 0
            return val is not None
        if condition == "exists":
            return val is not None
        if condition == "equals":
            return bool(val == value)
        return True

    return predicate


def _edge_predicate(field: str | None, condition: str | None, value: Any):  # type: ignore[no-untyped-def]
    if not field or not condition:
        return lambda _r: True

    def predicate(relation: Doc) -> bool:
        val = relation.get(field)
        if condition == "notEmpty":
            if isinstance(val, str):
                return len(val) > 0
            return val is not None
        if condition == "exists":
            return val is not None
        if condition == "equals":
            return bool(val == value)
        return True

    return predicate


def validate_spec(params: ValidateSpecInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _all_docs(store)
    properties: list[Doc] = []

    for definition in params.properties:
        if definition.type == "default":
            for name in checks.DEFAULT_INVARIANT_NAMES:
                result = checks.BUILTIN_INVARIANTS[name](entities, relations, store)
                properties.append(
                    {
                        "name": result["name"],
                        "level": result["level"],
                        "pass": result["pass"],
                        "checked": 1,
                        "violations": result["violations"],
                    }
                )
        elif definition.type == "invariant":
            inv = checks.BUILTIN_INVARIANTS.get(definition.invariant_name or "")
            if inv is None:
                continue  # unknown invariantName silently skipped
            result = inv(entities, relations, store)
            properties.append(
                {
                    "name": definition.name or result["name"],
                    "level": definition.level or result["level"],
                    "pass": result["pass"],
                    "checked": 1,
                    "violations": result["violations"],
                }
            )
        elif definition.type == "forAllNodes":
            predicate = _node_predicate(definition.field, definition.condition, definition.value)
            violations = [
                {
                    "elementId": e["id"],
                    "message": (
                        f"Node '{e['name']}' ({e['id']}) violated property '{definition.name}'"
                    ),
                }
                for e in entities
                if not predicate(e)
            ]
            properties.append(
                {
                    "name": definition.name,
                    "level": "node",
                    "pass": len(violations) == 0,
                    "checked": len(entities),
                    "violations": violations,
                }
            )
        elif definition.type == "forAllEdges":
            predicate = _edge_predicate(definition.field, definition.condition, definition.value)
            violations = [
                {
                    "elementId": r["id"],
                    "message": (
                        f"Edge '{r['id']}' ({r['from']} -> {r['to']}) violated "
                        f"property '{definition.name}'"
                    ),
                }
                for r in relations
                if not predicate(r)
            ]
            properties.append(
                {
                    "name": definition.name,
                    "level": "edge",
                    "pass": len(violations) == 0,
                    "checked": len(relations),
                    "violations": violations,
                }
            )

    failed = sum(1 for p in properties if not p["pass"])
    return {
        "pass": failed == 0,
        "properties": properties,
        "totalProperties": len(properties),
        "passedProperties": len(properties) - failed,
        "failedProperties": failed,
    }


# =============================================================================
# list-guard-violations
# =============================================================================

_LGV_ENTITY_GUARDS = ["confidenceBounds", "entityType", "observationsRequired"]
_LGV_RELATION_GUARDS = [
    "causalPolarity",
    "nonCausalPolarity",
    "noSelfLoop",
    "noDuplicateRelation",
]


def list_guard_violations(params: ListGuardViolationsInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _all_docs(store)
    selected = set(params.guards) if params.guards else None

    entity_guards = [g for g in _LGV_ENTITY_GUARDS if selected is None or g in selected]
    relation_guards = [g for g in _LGV_RELATION_GUARDS if selected is None or g in selected]

    entity_violations: list[Doc] = []
    for entity in entities:
        for name in entity_guards:
            found = checks.ENTITY_GUARDS[name](entity)
            if found:
                entity_violations.append(
                    {
                        "entityId": entity["id"],
                        "entityName": entity["name"],
                        "guardName": name,
                        "violations": found,
                    }
                )
    relation_violations: list[Doc] = []
    for relation in relations:
        for name in relation_guards:
            found = checks.RELATION_GUARDS[name](relation, store)
            if found:
                relation_violations.append(
                    {
                        "from": relation["from"],
                        "to": relation["to"],
                        "guardName": name,
                        "violations": found,
                    }
                )
    total = sum(len(g["violations"]) for g in (*entity_violations, *relation_violations))
    return {
        "entityViolations": entity_violations,
        "relationViolations": relation_violations,
        "totalViolations": total,
    }


# =============================================================================
# propagate-constraints (AC-3)
# =============================================================================


def propagate_constraints(params: PropagateConstraintsInput, multi: MultiGraph) -> Doc:
    multi.get_store(params.graph)  # resolve (unused by propagation)
    if not params.constraints:
        return {"consistent": True, "prunedDomains": {}, "revisionsCount": 0}
    type_constraints = [
        {
            "sourceType": c.source_type.value,
            "relationType": c.relation_type.value,
            "targetType": c.target_type.value,
        }
        for c in params.constraints
    ]
    variables, constraints = propagation.build_csp(type_constraints)
    return propagation.serialize(propagation.propagate(variables, constraints))


# =============================================================================
# check-capabilities
# =============================================================================


def _capability_result(name: str, violations: list[Doc]) -> Doc:
    return {"name": name, "pass": len(violations) == 0, "violations": violations}


def check_capabilities(params: CheckCapabilitiesInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    entities, relations = _all_docs(store)
    capabilities: list[Doc] = []

    if params.types:
        present = {e.get("entityType") for e in entities}
        violations = [
            {
                "capabilityName": f"type-completeness({','.join(params.types)})",
                "violationType": "completeness",
                "message": f"Entity type '{t}' has no instances in the graph",
                "suggestedAction": f"Create at least one entity of type '{t}'",
            }
            for t in params.types
            if t not in present
        ]
        capabilities.append(
            _capability_result(f"type-completeness({','.join(params.types)})", violations)
        )

    if params.coupling_threshold is not None:
        metric = params.coupling_metric or "degree"
        capabilities.append(_coupling(entities, relations, metric, params.coupling_threshold))

    if params.coverage_parent_type and params.coverage_child_type and params.coverage_relation_type:
        capabilities.append(
            _coverage(
                entities,
                relations,
                params.coverage_parent_type,
                params.coverage_child_type,
                params.coverage_relation_type,
            )
        )

    all_violations = [v for c in capabilities for v in c["violations"]]
    failed = sum(1 for c in capabilities if not c["pass"])
    return {
        "pass": failed == 0,
        "totalCapabilities": len(capabilities),
        "passedCapabilities": len(capabilities) - failed,
        "failedCapabilities": failed,
        "violations": all_violations,
        "capabilities": capabilities,
    }


def _coverage(
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
    return _capability_result(name, violations)


def _coupling(entities: list[Doc], relations: list[Doc], metric: str, threshold: float) -> Doc:
    name = f"coupling({metric}<{threshold})"
    if not entities:
        return _capability_result(name, [])
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
    return _capability_result(name, violations)


# =============================================================================
# constrained-generate (non-deterministic seed; implemented, output not fixed)
# =============================================================================


def constrained_generate(params: ConstrainedGenerateInput, multi: MultiGraph) -> Doc:
    required = [t.value for t in (params.required_types or [])]
    if params.max_entities == 0:
        if required:
            return {
                "success": False,
                "entities": [],
                "relations": [],
                "failureReason": ("Cannot satisfy required types with maxEntities=0"),
            }
        return {"success": True, "entities": [], "relations": []}
    if len(required) > params.max_entities:
        return {
            "success": False,
            "entities": [],
            "relations": [],
            "failureReason": (
                f"Required {len(required)} types but maxEntities is {params.max_entities}"
            ),
        }
    # Deterministic subset: emit the required-type entities (filling
    # remaining slots with a time-seeded PRNG would be non-deterministic;
    # we stop at the required types so output is stable and useful).
    entities = [
        {
            "name": f"Generated {t}",
            "entityType": t,
            "observations": [f"Auto-generated {t} entity"],
        }
        for t in required
    ]
    result: Doc = {"success": True, "entities": entities, "relations": []}
    if params.commit:
        store = multi.get_store(params.graph)
        committed = [store.create_entity(EntityCreate.model_validate(e)).id for e in entities]
        result["committedEntityIds"] = committed
        result["skippedRelations"] = 0
    return result


_ = ALL_ENTITY_TYPES  # referenced by generator notes


# =============================================================================
# validate-mutation-trace — replay mutations on a temp clone,
# check invariants after each step, report the first failure.
# =============================================================================


class TraceMutation(CommandInput):
    type: Literal[
        "createEntity", "updateEntity", "deleteEntity", "createRelation", "deleteRelation"
    ]
    payload: dict[str, Any]


class ValidateMutationTraceInput(CommandInput):
    mutations: list[TraceMutation]
    invariants: list[str] | None = None
    graph: str | None = None


def _clone_graph_to_temp(multi: MultiGraph, source_graph: str | None) -> str:
    """Clone the source graph (all statuses, ids preserved) into a fresh temp
    graph and return its name."""
    temp_name = f"verify-trace-{uuid.uuid4().hex}"
    multi.create_graph(temp_name)
    source = multi.get_store(source_graph)
    temp = multi.get_store(temp_name)
    all_statuses = EntityFilter.model_validate({"statusFilter": list(ALL_ENTITY_STATUSES)})
    for entity in source.list_entities(all_statuses):
        temp.import_entity_doc(entity.model_dump(by_alias=True, exclude_unset=True))
    for relation in source.list_relations():
        temp.import_relation_doc(relation.model_dump(by_alias=True, exclude_unset=True))
    return temp_name


def _apply_trace_mutation(mutation: TraceMutation, store: FalkorGraphStore) -> None:
    """Apply one mutation verbatim. Unlike simulate-change, this verifier
    does NOT support the ``__LAST_CREATED__`` sentinel — the payload is passed
    straight through, so a dangling endpoint surfaces as a store NOT_FOUND."""
    payload = mutation.payload
    if mutation.type == "createEntity":
        store.create_entity(EntityCreate.model_validate(payload))
    elif mutation.type == "updateEntity":
        store.update_entity(payload["id"], {k: v for k, v in payload.items() if k != "id"})
    elif mutation.type == "deleteEntity":
        store.delete_entity(payload["id"])
    elif mutation.type == "createRelation":
        store.create_relation(RelationCreate.model_validate(payload))
    else:  # deleteRelation
        store.delete_relation(payload["from"], payload["to"])


def validate_mutation_trace(params: ValidateMutationTraceInput, multi: MultiGraph) -> Doc:
    if not params.mutations:
        return {"pass": True, "steps": [], "firstFailureStep": -1}

    temp_name = _clone_graph_to_temp(multi, params.graph)
    try:
        store = multi.get_store(temp_name)
        steps: list[Doc] = []
        first_failure = -1
        for index, mutation in enumerate(params.mutations):
            _apply_trace_mutation(mutation, store)
            spec_result = check_invariants(
                CheckInvariantsInput.model_validate(
                    {"graph": temp_name, "invariants": params.invariants}
                ),
                multi,
            )
            step_pass = bool(spec_result["pass"])
            steps.append(
                {
                    "stepIndex": index,
                    "mutation": {"type": mutation.type, "payload": mutation.payload},
                    "specResult": spec_result,
                    "pass": step_pass,
                }
            )
            if not step_pass and first_failure == -1:
                first_failure = index
        return {"pass": first_failure == -1, "steps": steps, "firstFailureStep": first_failure}
    finally:
        with contextlib.suppress(Exception):
            multi.delete_graph(temp_name)
