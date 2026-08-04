"""Entity operations.

The ops layer adds operation-level semantics on top of the store: verification-gate
warnings appended as observations, revision auto-population (version=1 and
changeType='created' on create; version increment + the deliberate
previousVersionId self-reference on update — kept on the wire while
real history lives in the event log and version snapshots), confidence
lastEvaluated / provenance extractionDate auto-dates, changeType
auto-detection (confidence > status > content), statusChangedAt, and the
replacedById → supersedes auto-relation. Handlers return wire-ready dicts.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError
from theloom.model import (
    ConfidenceBasis,
    Domain,
    Durability,
    EntityCreate,
    EntityFilter,
    EntityStatus,
    EntityType,
    MemoryType,
    is_valid_transition,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.verification.guards import entity_gate_warnings

WILDCARD_GRAPH = "*"


def _transition_error(from_status: str | None, to_status: str) -> str:
    effective = from_status or "active"
    if effective == "retracted":
        return (
            f"Invalid status transition from '{effective}' to '{to_status}'. "
            "Retracted entities cannot be reactivated - this status indicates the "
            "entity was withdrawn due to error or invalidity."
        )
    return f"Invalid status transition from '{effective}' to '{to_status}'."


# =============================================================================
# Input models (the input schemas)
# =============================================================================


class ConfidenceArg(CommandInput):
    score: float = Field(ge=0, le=1)
    basis: ConfidenceBasis
    last_evaluated: str | None = Field(default=None, alias="lastEvaluated")


class ProvenanceArg(CommandInput):
    source_type: str = Field(alias="sourceType")
    source_id: str | None = Field(alias="sourceId")
    external_ref: str | None = Field(alias="externalRef")
    extraction_date: str | None = Field(default=None, alias="extractionDate")
    extractor: str
    extraction_method: str | None = Field(alias="extractionMethod")


class CreateEntityInput(CommandInput):
    name: str
    entity_type: EntityType = Field(alias="entityType")
    observations: list[str]
    confidence: ConfidenceArg | None = None
    provenance: ProvenanceArg | None = None
    session: str | None = None
    version: int | None = Field(default=None, ge=1)
    previous_version_id: str | None = Field(default=None, alias="previousVersionId")
    change_type: str | None = Field(default=None, alias="changeType")
    change_reason: str | None = Field(default=None, alias="changeReason")
    memory_type: MemoryType | None = Field(default=None, alias="memoryType")
    domain: Domain | None = None
    durability: Durability | None = None
    expires_at: str | None = Field(default=None, alias="expiresAt")
    graph: str | None = None


class ReadEntityInput(CommandInput):
    id: UuidStr
    graph: str | None = None


class UpdateEntityInput(CommandInput):
    id: UuidStr
    name: str | None = None
    entity_type: EntityType | None = Field(default=None, alias="entityType")
    observations: list[str] | None = None
    confidence: ConfidenceArg | None = None
    status: EntityStatus | None = None
    status_reason: str | None = Field(default=None, alias="statusReason")
    provenance: ProvenanceArg | None = None
    change_type: str | None = Field(default=None, alias="changeType")
    change_reason: str | None = Field(default=None, alias="changeReason")
    replaced_by_id: UuidStr | None = Field(default=None, alias="replacedById")
    graph: str | None = None


class DeleteEntityInput(CommandInput):
    id: UuidStr
    graph: str | None = None


class ListEntitiesInput(CommandInput):
    entity_type: EntityType | None = Field(default=None, alias="entityType")
    name: str | None = None
    query: str | None = None
    sourced_from: list[UuidStr] | None = Field(default=None, alias="sourcedFrom")
    exclude_sourced_from: list[UuidStr] | None = Field(default=None, alias="excludeSourcedFrom")
    include_superseded: bool | None = Field(default=None, alias="includeSuperseded")
    include_deprecated: bool | None = Field(default=None, alias="includeDeprecated")
    include_retracted: bool | None = Field(default=None, alias="includeRetracted")
    include_investigating: bool | None = Field(default=None, alias="includeInvestigating")
    version: int | None = Field(default=None, ge=1)
    min_version: int | None = Field(default=None, ge=1, alias="minVersion")
    session: str | None = None
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None


class ReadEntitiesByNameInput(CommandInput):
    names: list[str] = Field(max_length=200_000)
    graph: str | None = None


# =============================================================================
# Helpers
# =============================================================================


def _entity_doc(store: FalkorGraphStore, entity_id: str) -> dict[str, Any] | None:
    entity = store.read_entity(entity_id)
    return entity.model_dump(by_alias=True, exclude_unset=True) if entity else None


def _confidence_doc(confidence: ConfidenceArg) -> dict[str, Any]:
    # `||` (falsy-coalescing) semantics: any falsy lastEvaluated is replaced.
    return {
        "score": confidence.score,
        "basis": confidence.basis.value,
        "lastEvaluated": confidence.last_evaluated or iso_now(),
    }


def _provenance_doc(provenance: ProvenanceArg) -> dict[str, Any]:
    # `??` semantics: only null/absent extractionDate is replaced.
    doc = provenance.model_dump(by_alias=True)
    doc["extractionDate"] = (
        provenance.extraction_date if provenance.extraction_date is not None else iso_now()
    )
    return doc


# =============================================================================
# Operations
# =============================================================================


def create_entity(params: CreateEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)

    warnings = entity_gate_warnings(store, params.name, params.observations)
    observations = [*params.observations, *warnings] if warnings else params.observations

    doc: dict[str, Any] = {
        "name": params.name,
        "entityType": params.entity_type.value,
        "observations": observations,
        "version": params.version if params.version is not None else 1,
        "previousVersionId": params.previous_version_id,
        "changeType": params.change_type if params.change_type is not None else "created",
        "changeReason": params.change_reason,
    }
    if params.confidence is not None:
        doc["confidence"] = _confidence_doc(params.confidence)
    if params.provenance is not None:
        doc["provenance"] = _provenance_doc(params.provenance)
    if params.session is not None:
        doc["session"] = params.session
    # 3D fields pass through only when truthy (conditional-spread guards).
    if params.memory_type:
        doc["memoryType"] = params.memory_type.value
    if params.domain:
        doc["domain"] = params.domain.value
    if params.durability:
        doc["durability"] = params.durability.value
    if params.expires_at:
        doc["expiresAt"] = params.expires_at

    entity = store.create_entity(EntityCreate.model_validate(doc))
    return entity.model_dump(by_alias=True, exclude_unset=True)


def read_entity(params: ReadEntityInput, multi: MultiGraph) -> dict[str, Any]:
    doc = _entity_doc(multi.get_store(params.graph), params.id)
    if doc is None:
        raise NotFoundError(
            f"Entity not found with ID: {params.id}. Use list_entities to see available entities."
        )
    return doc


def update_entity(params: UpdateEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    current = _entity_doc(store, params.id)
    if current is None:
        raise NotFoundError(
            f"Entity not found with ID: {params.id}. "
            "Use list_entities to verify the entity exists before updating."
        )

    if params.status is not None and not is_valid_transition(current.get("status"), params.status):
        from theloom.errors import ValidationError

        raise ValidationError(_transition_error(current.get("status"), params.status.value))

    updates: dict[str, Any] = {}
    if params.name is not None:
        updates["name"] = params.name
    if params.entity_type is not None:
        updates["entityType"] = params.entity_type.value
    if params.observations is not None:
        updates["observations"] = params.observations
    if params.confidence is not None:
        updates["confidence"] = _confidence_doc(params.confidence)
    if params.status is not None:
        updates["status"] = params.status.value
        updates["statusChangedAt"] = iso_now()
    if params.status_reason is not None:
        updates["statusReason"] = params.status_reason
    if params.provenance is not None:
        updates["provenance"] = _provenance_doc(params.provenance)

    updates["version"] = (current.get("version") or 0) + 1
    updates["previousVersionId"] = current["id"]  # self-reference, kept on the wire

    if params.change_type is not None:
        updates["changeType"] = params.change_type
    elif params.confidence is not None:
        updates["changeType"] = "confidence_updated"
    elif params.status is not None:
        updates["changeType"] = "status_changed"
    else:
        updates["changeType"] = "content_updated"

    if params.provided("changeReason"):
        updates["changeReason"] = params.change_reason

    entity = store.update_entity(params.id, updates)
    entity_doc = entity.model_dump(by_alias=True, exclude_unset=True)

    supersedes_relation: dict[str, Any] | None = None
    if params.status == EntityStatus.SUPERSEDED and params.replaced_by_id is not None:
        replacement_found = (
            store.read_entity(params.replaced_by_id) is not None
            or multi.find_entity_graph(params.replaced_by_id) is not None
        )
        if not replacement_found:
            raise NotFoundError(
                f"Replacement entity not found with ID: {params.replaced_by_id}. "
                "The entity has been marked as superseded but no relation was created."
            )
        from theloom.operations.relations import CreateRelationInput, create_relation

        supersedes_relation = create_relation(
            CreateRelationInput.model_validate(
                {
                    "from": params.replaced_by_id,
                    "to": params.id,
                    "relationType": "supersedes",
                    "polarity": None,
                    "strength": "strong",
                    "evidence": (
                        f"Auto-created when entity {params.id} was marked as "
                        f"superseded by {params.replaced_by_id}"
                    ),
                    "graph": params.graph,
                }
            ),
            multi,
        )

    return {"entity": entity_doc, "supersedesRelation": supersedes_relation}


def delete_entity(params: DeleteEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    try:
        deleted = store.delete_entity(params.id)
    except NotFoundError:
        raise NotFoundError(
            f"Entity not found with ID: {params.id}. "
            "Use list_entities to verify the entity exists before deleting."
        ) from None
    return deleted.model_dump(by_alias=True, exclude_unset=True)


TRUNCATION_HINT = "raise limit or narrow with entityType/query"


def list_entities(
    params: ListEntitiesInput, multi: MultiGraph
) -> list[dict[str, Any]] | dict[str, Any]:
    """Entity docs in the store's deterministic order.

    Output shape is behaviour-first: without ``limit`` the legacy bare array is
    preserved; with ``limit`` the result is
    ``{"items": [...], "truncated": {"shown", "total", "hint"}}`` so a capped
    read always says how much it did not show.
    """
    status_filter = ["active"]
    if params.include_superseded is True:
        status_filter.append("superseded")
    if params.include_deprecated is True:
        status_filter.append("deprecated")
    if params.include_retracted is True:
        status_filter.append("retracted")
    if params.include_investigating is True:
        status_filter.append("investigating")

    filter_doc: dict[str, Any] = {"statusFilter": status_filter}
    for field, key in (
        ("entity_type", "entityType"),
        ("name", "name"),
        ("query", "query"),
        ("sourced_from", "sourcedFrom"),
        ("exclude_sourced_from", "excludeSourcedFrom"),
        ("version", "version"),
        ("min_version", "minVersion"),
        ("session", "session"),
        ("limit", "limit"),
    ):
        value = getattr(params, field)
        if value is not None:
            filter_doc[key] = value
    entity_filter = EntityFilter.model_validate(filter_doc)

    results: list[dict[str, Any]] = []
    total = 0
    if params.graph == WILDCARD_GRAPH:
        # Each graph is capped at `limit` and reports its own total; the
        # concatenation is then re-capped, which is the same prefix a single
        # uncapped concatenation would have produced.
        for graph_name in multi.graph_names():
            entities, graph_total = multi.get_store(graph_name).list_entities_page(entity_filter)
            total += graph_total
            for entity in entities:
                doc = entity.model_dump(by_alias=True, exclude_unset=True)
                doc["graph"] = graph_name
                results.append(doc)
    else:
        entities, total = multi.get_store(params.graph).list_entities_page(entity_filter)
        results = [e.model_dump(by_alias=True, exclude_unset=True) for e in entities]

    if params.limit is None:
        return results
    shown = results[: params.limit]
    return {
        "items": shown,
        "truncated": {"shown": len(shown), "total": total, "hint": TRUNCATION_HINT},
    }


def read_entities_by_name(params: ReadEntitiesByNameInput, multi: MultiGraph) -> dict[str, Any]:
    if not params.names:
        return {"resolved": {}, "unresolved": []}
    store = multi.get_store(params.graph)
    actives = store.list_entities(EntityFilter.model_validate({"statusFilter": ["active"]}))
    name_to_id: dict[str, str] = {}
    for entity in actives:
        name_to_id[entity.name] = entity.id  # last one wins on duplicate names
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for name in params.names:
        if name in name_to_id:
            resolved[name] = name_to_id[name]
        else:
            unresolved.append(name)
    return {"resolved": resolved, "unresolved": unresolved}
