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

from theloom.config import load_config
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
from theloom.operations.common import CommandInput, UuidStr, resolve_entity_ref
from theloom.operations.notices import Doc, list_envelope, notice, with_notices
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
    session: str | None = Field(
        default=None,
        description="The authoring identity attributed to this entity. When omitted, the "
        "server attributes a configured fallback identity (theloom/config.py's "
        "defaultSession) so every entity carries authorship -- never left absent.",
    )
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
    """Addressed by ``id`` or by ``name`` — exactly one."""

    id: UuidStr | None = None
    name: str | None = None
    graph: str | None = None
    compact: bool | None = None


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
    hard: bool | None = None
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
    compact: bool | None = None


class ReadEntitiesByNameInput(CommandInput):
    names: list[str] = Field(max_length=200_000)
    graph: str | None = None


# =============================================================================
# Helpers
# =============================================================================


def entity_doc(store: FalkorGraphStore, entity_id: str) -> dict[str, Any] | None:
    """Read an entity and return its wire-ready dict, or None if absent.

    Public so composites needing a plain entity doc (e.g. entity-deep-dive)
    can call the same helper the entity operations use, instead of reaching
    for a private name."""
    entity = store.read_entity(entity_id)
    return entity.model_dump(by_alias=True, exclude_unset=True) if entity else None


def compact_entity_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Agent-shaped entity projection — id/name/entityType/status/observations
    only, dropping confidence/provenance/embedding metadata/timestamps. Shared
    by every command that can embed entities in its output (read-entity,
    list-entities, get-neighbors, get-relations' bridge rows, entity-deep-dive)."""
    return {
        "id": doc["id"],
        "name": doc["name"],
        "entityType": doc["entityType"],
        "status": doc.get("status"),
        "observations": doc["observations"],
    }


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


def _confidence_out_of_line_notice(
    store: FalkorGraphStore,
    *,
    session: str,
    basis: str,
    domain: str | None,
    asserted_score: float,
) -> Doc | None:
    """``CONFIDENCE_OUT_OF_LINE`` (desire 14): compares the score just
    asserted against this author's OWN measured calibration bucket for this
    basis (narrowed to this domain, when the entity has one) -- a notice,
    never a rejection; the write already happened by the time this runs.

    Defined here, in ``entity.py``, rather than in
    ``theloom.operations.calibration`` where the bucket arithmetic actually
    lives: the notices-catalog reachability walk
    (``theloom.cli.notices_catalog``) only follows same-module calls from a
    command's own handler, so a ``notice()`` call two modules deep would
    never be attributed to ``create-entity``. ``calibration`` is imported
    lazily to avoid a create_entity <-> resolve_claim import cycle
    (``calibration.resolve_claim`` calls back into this module's
    ``create_entity``/``update_entity``, the same reason ``update_entity``
    below defers its own import of ``theloom.operations.relations``).
    """
    from theloom.operations import calibration

    config = load_config()
    gap_result = calibration.assertion_time_gap(
        store,
        session=session,
        basis=basis,
        domain=domain,
        floor=config.calibration_min_bucket_n,
    )
    if gap_result is None:
        return None
    gap = asserted_score - gap_result.empirical_hit_rate
    if abs(gap) < config.calibration_gap_threshold:
        return None
    domain_clause = f" in {domain}" if domain else ""
    return notice(
        "CONFIDENCE_OUT_OF_LINE",
        f"Your {basis}-based claims{domain_clause} resolve at "
        f"{gap_result.empirical_hit_rate:.2f} empirically (n={gap_result.n} judged); "
        f"you asserted {asserted_score:.2f}.",
        hint="Informational, not a rejection -- see calibration-profile for the full bucket.",
    )


def create_entity(params: CreateEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)

    warnings = entity_gate_warnings(store, params.name, params.observations)
    observations = [*params.observations, *warnings] if warnings else params.observations

    # Required-with-default (desire 14): every entity now carries an author,
    # server-supplied when the caller omits one -- never left absent. The
    # input schema itself stays optional (no new required field).
    session = params.session if params.session is not None else load_config().default_session

    doc: dict[str, Any] = {
        "name": params.name,
        "entityType": params.entity_type.value,
        "observations": observations,
        "version": params.version if params.version is not None else 1,
        "previousVersionId": params.previous_version_id,
        "changeType": params.change_type if params.change_type is not None else "created",
        "changeReason": params.change_reason,
        "session": session,
    }
    if params.confidence is not None:
        doc["confidence"] = _confidence_doc(params.confidence)
    if params.provenance is not None:
        doc["provenance"] = _provenance_doc(params.provenance)
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
    result = entity.model_dump(by_alias=True, exclude_unset=True)

    calibration_notices: list[Doc] = []
    if params.confidence is not None:
        out_of_line = _confidence_out_of_line_notice(
            store,
            session=session,
            basis=params.confidence.basis.value,
            domain=params.domain.value if params.domain else None,
            asserted_score=params.confidence.score,
        )
        if out_of_line is not None:
            calibration_notices.append(out_of_line)
    return with_notices(result, calibration_notices)


def read_entity(params: ReadEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity_id = resolve_entity_ref(store, entity_id=params.id, name=params.name)
    doc = entity_doc(store, entity_id)
    if doc is None:
        raise NotFoundError(
            f"Entity not found with ID: {entity_id}. Use list_entities to see available entities."
        )
    return compact_entity_doc(doc) if params.compact else doc


def update_entity(params: UpdateEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    current = entity_doc(store, params.id)
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
    updated_doc = entity.model_dump(by_alias=True, exclude_unset=True)

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

    return {"entity": updated_doc, "supersedesRelation": supersedes_relation}


def delete_entity(params: DeleteEntityInput, multi: MultiGraph) -> dict[str, Any]:
    """Retract an entity, returning the retracted record.

    History is preserved: the entity keeps its id, gains status 'retracted',
    and its attached relations are closed out bi-temporally, so a
    point-in-time read still reconstructs the graph as it was. ``hard: true``
    erases the entity and its edges outright — the only path that loses
    history.
    """
    store = multi.get_store(params.graph)
    try:
        deleted = store.delete_entity(params.id, hard=bool(params.hard))
    except NotFoundError:
        raise NotFoundError(
            f"Entity not found with ID: {params.id}. "
            "Use list_entities to verify the entity exists before deleting."
        ) from None
    return deleted.model_dump(by_alias=True, exclude_unset=True)


TRUNCATION_HINT = "raise limit or narrow with entityType/query"


def list_entities(params: ListEntitiesInput, multi: MultiGraph) -> dict[str, Any]:
    """Entity docs in the store's deterministic order, as the uniform
    ``{items, count, notices?}`` envelope (desire 9). Without ``limit`` every
    match is returned; with ``limit``, ``items`` is capped and — when the
    store holds more matches than were shown — a ``TRUNCATED`` notice says
    how many there really were and how to see the rest, rather than a
    separate ``truncated`` object with an ambiguous relationship to
    ``count``.
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
                # Compact first, then stamp: `graph` is the wildcard
                # disambiguator and must survive the projection.
                if params.compact:
                    doc = compact_entity_doc(doc)
                doc["graph"] = graph_name
                results.append(doc)
    else:
        entities, total = multi.get_store(params.graph).list_entities_page(entity_filter)
        results = [e.model_dump(by_alias=True, exclude_unset=True) for e in entities]
        if params.compact:
            results = [compact_entity_doc(doc) for doc in results]

    if params.limit is None:
        return list_envelope(results)
    shown = results[: params.limit]
    notices = None
    if len(shown) < total:
        notices = [
            notice(
                "TRUNCATED",
                f"Showing {len(shown)} of {total} matching entities.",
                hint=TRUNCATION_HINT,
            )
        ]
    return list_envelope(shown, notices)


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
