"""Relation operations.

Semantics, including a few that look like bugs but are intentional:

- Polarity auto-inference for causal types (CAUSAL_POLARITY_DEFAULTS) when the
  caller passes null.
- The verification gate runs against the *resolved single store* BEFORE the
  bridge branch — so a cross-graph relation is blocked with an
  entity-does-not-exist gate error, and the bridge auto-creation path is
  unreachable from the CLI. The store-level bridge
  capability (MultiGraph.create_relation) remains for when the gate is off.
- list-relations distinguishes an explicit ``"polarity": null`` filter (matches
  only null-polarity relations) from an absent key (no filter).
- get-relations / get-neighbors append cross-graph bridge rows/stubs after the
  same-graph results, with direction/type filtering and follow_bridges.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, OperationError
from theloom.model import (
    CAUSAL_POLARITY_DEFAULTS,
    EntityFilter,
    Polarity,
    RelationCreate,
    RelationFilter,
    RelationType,
    Strength,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.store.multigraph import MultiGraph
from theloom.verification.guards import non_causal_polarity_error, relation_gate_errors

WILDCARD_GRAPH = "*"

# All-status filter used by the batch endpoint's endpoint-id prefetch — the
# orphan guard reads by id regardless of status.
_ALL_STATUS_FILTER = EntityFilter.model_validate(
    {"statusFilter": ["active", "superseded", "deprecated", "retracted", "investigating"]}
)

# =============================================================================
# Input models (the input schemas)
# =============================================================================


class RelationItem(CommandInput):
    from_: UuidStr = Field(alias="from")
    to: UuidStr
    relation_type: RelationType = Field(alias="relationType")
    polarity: Polarity | None
    strength: Strength
    evidence: str | None
    session: str | None = None
    graph: str | None = None


class CreateRelationInput(RelationItem):
    pass


class CreateRelationsInput(CommandInput):
    relations: list[RelationItem] = Field(max_length=200_000)
    continue_on_error: bool | None = Field(default=None, alias="continueOnError")


class ReadRelationInput(CommandInput):
    from_: UuidStr = Field(alias="from")
    to: UuidStr
    graph: str | None = None


class ReadRelationsInput(CommandInput):
    from_: UuidStr = Field(alias="from")
    to: UuidStr
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None


class UpdateRelationInput(CommandInput):
    from_: UuidStr = Field(alias="from")
    to: UuidStr
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    polarity: Polarity | None = None
    strength: Strength | None = None
    evidence: str | None = None
    graph: str | None = None


class DeleteRelationInput(CommandInput):
    from_: UuidStr = Field(alias="from")
    to: UuidStr
    graph: str | None = None


class ListRelationsInput(CommandInput):
    from_: UuidStr | None = Field(default=None, alias="from")
    to: UuidStr | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    polarity: Polarity | None = None
    session: str | None = None
    graph: str | None = None


class GetRelationsInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    direction: str | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None
    follow_bridges: bool | None = None


class GetNeighborsInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    direction: str | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None
    follow_bridges: bool | None = None


# =============================================================================
# create-relation (single + batch)
# =============================================================================


def _effective_polarity(item: RelationItem) -> str | None:
    if item.polarity is not None:
        return item.polarity
    return CAUSAL_POLARITY_DEFAULTS.get(item.relation_type)


def _gate_or_raise(item: RelationItem, multi: MultiGraph, polarity: str | None) -> None:
    store = multi.get_store(item.graph)
    errors = relation_gate_errors(store, item.from_, item.to, item.relation_type.value, polarity)
    if errors:
        message = f"Relation creation blocked by verification gate: {'; '.join(errors)}"
        raise OperationError(message)


def _spec(item: RelationItem, polarity: str | None) -> RelationCreate:
    doc: dict[str, Any] = {
        "from": item.from_,
        "to": item.to,
        "relationType": item.relation_type.value,
        "polarity": polarity,
        "strength": item.strength.value,
        "evidence": item.evidence,
    }
    if item.session is not None:
        doc["session"] = item.session
    return RelationCreate.model_validate(doc)


def create_relation(params: CreateRelationInput, multi: MultiGraph) -> dict[str, Any]:
    polarity = _effective_polarity(params)
    try:
        _gate_or_raise(params, multi, polarity)
    except OperationError as error:
        message = str(error)
        if "not found" in message or "exist" in message:
            raise OperationError(
                f"Failed to create relation from {params.from_} to {params.to}: "
                f"{message}. Use list_entities to verify both entities exist."
            ) from None
        raise OperationError(f"Error creating relation: {message}") from None

    # Gate passed → both endpoints exist in the resolved store, so this is a
    # same-graph relation; the bridge branch is unreachable with the gate on
    # (see module docstring).
    relation = multi.get_store(params.graph).create_relation(_spec(params, polarity))
    return relation.model_dump(by_alias=True, exclude_unset=True)


def create_relations(params: CreateRelationsInput, multi: MultiGraph) -> dict[str, Any]:
    continue_on_error = params.continue_on_error if params.continue_on_error is not None else True
    applied = 0
    failed = 0
    bridges_created = 0
    errors: list[dict[str, str]] = []
    # Valid specs accumulate per target graph and are created in ONE
    # transactional UNWIND batch per graph.
    pending: dict[str | None, list[RelationCreate]] = {}

    def flush() -> None:
        nonlocal applied
        for graph, specs in pending.items():
            if specs:
                multi.get_store(graph).create_relations(specs)
        pending.clear()

    # Prefetch existing entity ids per graph so the orphan gate is one query
    # per graph instead of two reads per item.
    known_ids: dict[str | None, set[str]] = {}

    def ids_for(graph: str | None) -> set[str]:
        if graph not in known_ids:
            store = multi.get_store(graph)
            known_ids[graph] = {e.id for e in store.list_entities(_ALL_STATUS_FILTER)}
        return known_ids[graph]

    for item in params.relations:
        polarity = _effective_polarity(item)
        gate_errors: list[str] = []
        if item.relation_type in CAUSAL_POLARITY_DEFAULTS:
            if polarity not in ("+", "-"):
                gate_errors.append(
                    f"Causal relation type '{item.relation_type.value}' requires polarity"
                )
        elif polarity is not None:
            gate_errors.append(non_causal_polarity_error(item.relation_type.value, polarity))
        if item.from_ == item.to:
            gate_errors.append(
                f"Relation cannot reference the same entity as source and target: '{item.from_}'"
            )
        graph_ids = ids_for(item.graph)
        if item.from_ not in graph_ids:
            gate_errors.append(f"Source entity '{item.from_}' does not exist")
        if item.to not in graph_ids:
            gate_errors.append(f"Target entity '{item.to}' does not exist")

        if gate_errors:
            failed += 1
            message = f"Relation creation blocked by verification gate: {'; '.join(gate_errors)}"
            errors.append({"from": item.from_, "to": item.to, "error": message})
            if not continue_on_error:
                flush()  # persist the valid prefix before raising
                raise OperationError(f"Error creating relations batch: {message}")
            continue

        pending.setdefault(item.graph, []).append(_spec(item, polarity))
        applied += 1

    flush()
    return {
        "applied": applied,
        "failed": failed,
        "bridgesCreated": bridges_created,
        "errors": errors,
    }


# =============================================================================
# read / update / delete / list
# =============================================================================


def read_relation(params: ReadRelationInput, multi: MultiGraph) -> dict[str, Any]:
    relation = multi.get_store(params.graph).read_relation(params.from_, params.to)
    if relation is None:
        raise NotFoundError(
            f"Relation not found from {params.from_} to {params.to}. "
            "Use list_relations to see available relations."
        )
    return relation.model_dump(by_alias=True, exclude_unset=True)


def read_relations(params: ReadRelationsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    relations = multi.get_store(params.graph).read_relations(
        params.from_,
        params.to,
        params.relation_type.value if params.relation_type else None,
    )
    return [r.model_dump(by_alias=True, exclude_unset=True) for r in relations]


def update_relation(params: UpdateRelationInput, multi: MultiGraph) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if params.relation_type is not None:
        updates["relationType"] = params.relation_type.value
    if params.provided("polarity"):
        updates["polarity"] = params.polarity
    if params.strength is not None:
        updates["strength"] = params.strength.value
    if params.provided("evidence"):
        updates["evidence"] = params.evidence
    try:
        relation = multi.get_store(params.graph).update_relation(params.from_, params.to, updates)
    except NotFoundError:
        raise NotFoundError(
            f"Relation not found from {params.from_} to {params.to}. "
            "Use list_relations to verify the relation exists before updating."
        ) from None
    return relation.model_dump(by_alias=True, exclude_unset=True)


def delete_relation(params: DeleteRelationInput, multi: MultiGraph) -> str:
    try:
        multi.get_store(params.graph).delete_relation(params.from_, params.to)
    except NotFoundError:
        raise NotFoundError(
            f"Relation not found from {params.from_} to {params.to}. "
            "Use list_relations to verify the relation exists before deleting."
        ) from None
    return f"Relation from {params.from_} to {params.to} deleted successfully."


def list_relations(params: ListRelationsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    filter_doc: dict[str, Any] = {}
    if params.from_ is not None:
        filter_doc["from"] = params.from_
    if params.to is not None:
        filter_doc["to"] = params.to
    if params.relation_type is not None:
        filter_doc["relationType"] = params.relation_type.value
    if params.session is not None:
        filter_doc["session"] = params.session
    relation_filter = RelationFilter.model_validate(filter_doc) if filter_doc else None

    def fetch(graph: str | None) -> list[dict[str, Any]]:
        relations = multi.get_store(graph).list_relations(relation_filter)
        docs = [r.model_dump(by_alias=True, exclude_unset=True) for r in relations]
        # Explicit-null-aware polarity filter (distinguishes null from absent).
        if params.provided("polarity"):
            docs = [d for d in docs if d.get("polarity") == params.polarity]
        return docs

    if params.graph == WILDCARD_GRAPH:
        results: list[dict[str, Any]] = []
        for graph_name in multi.graph_names():
            for doc in fetch(graph_name):
                doc["graph"] = graph_name
                results.append(doc)
        return results
    return fetch(params.graph)


# =============================================================================
# get-relations / get-neighbors (bridge inclusion)
# =============================================================================


def _bridge_matches(
    bridge: dict[str, Any], entity_id: str, direction: str, relation_type: str | None
) -> bool:
    if direction == "outgoing":
        matches = bridge["from"] == entity_id
    elif direction == "incoming":
        matches = bridge["to"] == entity_id
    else:
        matches = entity_id in (bridge["from"], bridge["to"])
    if not matches:
        return False
    return relation_type is None or bridge["relationType"] == relation_type


def get_relations(params: GetRelationsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    direction = params.direction or "both"
    relation_type = params.relation_type.value if params.relation_type else None
    store = multi.get_store(params.graph)
    relations = store.get_relations(params.entity_id, direction, relation_type)  # type: ignore[arg-type]
    results = [r.model_dump(by_alias=True, exclude_unset=True) for r in relations]

    for bridge in multi.bridges.list_bridges({"entity_id": params.entity_id}):
        if not _bridge_matches(bridge, params.entity_id, direction, relation_type):
            continue
        row = dict(bridge)
        if params.follow_bridges:
            from_store = multi.get_store(bridge["from_graph"])
            from_entity = from_store.read_entity(bridge["from"])
            if from_entity is not None:
                row["from_entity"] = from_entity.model_dump(by_alias=True, exclude_unset=True)
            to_store = multi.get_store(bridge["to_graph"])
            to_entity = to_store.read_entity(bridge["to"])
            if to_entity is not None:
                row["to_entity"] = to_entity.model_dump(by_alias=True, exclude_unset=True)
        results.append(row)
    return results


def get_neighbors(params: GetNeighborsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    direction = params.direction or "both"
    relation_type = params.relation_type.value if params.relation_type else None
    store = multi.get_store(params.graph)
    neighbors = store.get_neighbors(params.entity_id, direction, relation_type)  # type: ignore[arg-type]
    results = [n.model_dump(by_alias=True, exclude_unset=True) for n in neighbors]

    seen_cross_graph: set[str] = set()
    for bridge in multi.bridges.list_bridges({"entity_id": params.entity_id}):
        if not _bridge_matches(bridge, params.entity_id, direction, relation_type):
            continue
        if bridge["from"] == params.entity_id:
            neighbor_id, neighbor_graph = bridge["to"], bridge["to_graph"]
        else:
            neighbor_id, neighbor_graph = bridge["from"], bridge["from_graph"]
        if neighbor_id in seen_cross_graph:
            continue
        seen_cross_graph.add(neighbor_id)

        if params.follow_bridges:
            entity = multi.get_store(neighbor_graph).read_entity(neighbor_id)
            if entity is not None:
                doc = entity.model_dump(by_alias=True, exclude_unset=True)
                doc["graph"] = neighbor_graph
                results.append(doc)
                continue
        results.append({"id": neighbor_id, "graph": neighbor_graph, "stub": True})
    return results
