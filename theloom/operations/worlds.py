"""Branchable belief worlds (desire 12 / Part 5): the CLI-facing commands —
``fork-world``, ``list-worlds``, ``abandon-world``, ``diff-worlds``,
``merge-world``. Thin wiring for fork/list/abandon, matching
``theloom.operations.sessions``'s pattern exactly (the mechanism is
``theloom.store.worlds``, reusing ``theloom.store.refs.RefRegistry`` with
``kind="world"``); ``diff-worlds``/``merge-world`` do their own semantic
work here because it needs notices/envelope wiring that ``theloom/store/``
must not depend on.

``diff-worlds`` reuses the same event-payload shapes ``theloom.operations.
receipts`` (``what-changed``) already replays — every entity/relation event
carries the full doc under an ``"entity"``/``"relation"`` key — rather than
re-deriving them, so both commands agree about what a "change" is by
construction, not by convention.
"""

from __future__ import annotations

from pydantic import Field

from theloom.errors import NotFoundError, ValidationError
from theloom.model import Entity, EntityFilter, EntityStatus, Relation
from theloom.operations.common import CommandInput
from theloom.operations.notices import Doc, list_envelope, notice, with_notices
from theloom.store.events import Event
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.store.refs import RefRecord
from theloom.store.worlds import WORLD_KIND, world_graph_name

_ALL_STATUS_FILTER = EntityFilter.model_validate(
    {"statusFilter": [status.value for status in EntityStatus]}
)

MAIN = "main"


# =============================================================================
# fork-world / list-worlds / abandon-world
# =============================================================================


class ForkWorldInput(CommandInput):
    name: str | None = Field(
        default=None,
        description="Optional human label for the world; purely descriptive, shown back by "
        "list-worlds.",
    )
    graph: str | None = Field(
        default=None,
        description="The base graph to fork from. Only meaningful when forming a fresh fork off "
        "'main' (fromWorld omitted); ignored (inferred from the parent) when forkWorld names "
        "an existing world.",
    )
    from_world: str | None = Field(
        default=None,
        alias="fromWorld",
        description="The world to fork from — a worldId, or omitted/'main' for the graph's live "
        "state.",
    )
    as_of: str | None = Field(
        default=None,
        alias="asOf",
        description="Fork at this historical instant (ISO 8601, the wire format) instead of the "
        "parent's current tip — a bi-temporal fork.",
    )
    ttl_seconds: int | None = Field(
        default=None,
        alias="ttlSeconds",
        description="Informational TTL, like a session's — nothing reaps a world automatically; "
        "abandon-world always does the deleting.",
    )


def fork_world(params: ForkWorldInput, multi: MultiGraph) -> Doc:
    """Fork a new world. Writes no entity data — a ref registration and
    nothing else — so this is O(1) regardless of the graph's size."""
    doc = multi.fork_world(
        name=params.name,
        graph=params.graph,
        from_world=params.from_world,
        as_of=params.as_of,
        ttl_seconds=params.ttl_seconds,
    )
    return with_notices(doc, applied=True)


class ListWorldsInput(CommandInput):
    pass


def list_worlds(_: ListWorldsInput, multi: MultiGraph) -> Doc:
    return list_envelope(multi.list_worlds())


class AbandonWorldInput(CommandInput):
    world_id: str = Field(alias="worldId", description="The worldId returned by fork-world.")


def abandon_world(params: AbandonWorldInput, multi: MultiGraph) -> Doc:
    """Mark a world's ref dead and delete its segment in one call — reaping
    an already-reaped/merged world is a truthful no-op (``applied: false``,
    an ``ALREADY_REAPED`` notice), the same convention ``end-session`` uses.
    """
    doc = multi.abandon_world(params.world_id)
    already_reaped = doc.pop("alreadyReaped")
    notices = (
        [
            notice(
                "ALREADY_REAPED",
                f"World '{params.world_id}' was already reaped (abandoned or merged); there was "
                "no segment left to delete.",
            )
        ]
        if already_reaped
        else None
    )
    return with_notices(doc, notices=notices, applied=not already_reaped)


# =============================================================================
# shared resolution helpers
# =============================================================================


def _require_world(multi: MultiGraph, world_id: str) -> RefRecord:
    record = multi.refs.get(WORLD_KIND, world_id)
    if record is None:
        raise NotFoundError(f"World '{world_id}' not found. Use list-worlds to see active worlds.")
    return record


def _base_graph_of(multi: MultiGraph, world_id: str | None) -> str | None:
    """``world_id``'s own base graph, or ``None`` when ``world_id`` denotes
    ``main`` (which carries no base graph of its own — a caller must supply
    one, or infer it from the other side of a comparison)."""
    if world_id in (None, "", MAIN):
        return None
    return str(_require_world(multi, world_id).metadata["baseGraph"])


def _store_for(multi: MultiGraph, base_graph: str, world_id: str | None) -> FalkorGraphStore:
    if world_id in (None, "", MAIN):
        return multi.get_store(base_graph)
    return multi.get_store(None, world=world_id)


def _events_for(multi: MultiGraph, base_graph: str, world_id: str | None) -> list[Event]:
    log_name = base_graph if world_id in (None, "", MAIN) else world_graph_name(world_id)
    return multi.event_log(log_name).read_all()


def _last_event_by_record_id(events: list[Event]) -> dict[str, str]:
    """id -> the last event that touched it, entity or relation alike —
    every creation/update/status-change/deletion event carries the full
    record verbatim under an ``"entity"``/``"relation"`` payload key (see
    ``theloom.operations.receipts``'s differs, which read the same shape)."""
    out: dict[str, str] = {}
    for event in events:
        record = event.payload.get("entity") or event.payload.get("relation")
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            out[record["id"]] = event.id
    return out


# =============================================================================
# diff-worlds
# =============================================================================


class DiffWorldsInput(CommandInput):
    a: str = Field(description="A worldId, or 'main'.")
    b: str = Field(description="A worldId, or 'main'.")
    scope: str | None = Field(
        default=None,
        description="Restrict the diff to 'entities' or 'relations'; omitted (default) reports "
        "both.",
    )


def _shared_base_graph(multi: MultiGraph, a_id: str, b_id: str) -> str:
    a_base = _base_graph_of(multi, a_id)
    b_base = _base_graph_of(multi, b_id)
    if a_base and b_base and a_base != b_base:
        raise ValidationError(
            f"'{a_id}' and '{b_id}' fork from different base graphs ('{a_base}' vs "
            f"'{b_base}') and cannot be diffed against each other."
        )
    base_graph = a_base or b_base
    if base_graph is None:
        raise ValidationError("diff-worlds needs at least one of 'a'/'b' to be a non-main world.")
    return base_graph


def diff_worlds(params: DiffWorldsInput, multi: MultiGraph) -> Doc:
    if params.a == params.b:
        raise ValidationError("diff-worlds needs two different worlds ('a' and 'b' were the same).")
    base_graph = _shared_base_graph(multi, params.a, params.b)
    store_a = _store_for(multi, base_graph, params.a)
    store_b = _store_for(multi, base_graph, params.b)
    events_b = _last_event_by_record_id(_events_for(multi, base_graph, params.b))
    include_entities = params.scope in (None, "entities")
    include_relations = params.scope in (None, "relations")

    rows: list[Doc] = []
    entities_a: dict[str, Entity] = {}
    entities_b: dict[str, Entity] = {}
    if include_entities or include_relations:
        # Relations need both sides' entity names even when scope='relations'.
        entities_a = {e.id: e for e in store_a.list_entities(_ALL_STATUS_FILTER)}
        entities_b = {e.id: e for e in store_b.list_entities(_ALL_STATUS_FILTER)}

    if include_entities:
        for entity_id in sorted(set(entities_b) - set(entities_a)):
            entity = entities_b[entity_id]
            rows.append(
                {
                    "kind": "entityAdded",
                    "entityId": entity_id,
                    "entityName": entity.name,
                    "entityType": entity.entity_type.value,
                    "eventId": events_b.get(entity_id),
                }
            )
        for entity_id in sorted(set(entities_a) - set(entities_b)):
            entity = entities_a[entity_id]
            rows.append(
                {
                    "kind": "entityInvalidated",
                    "entityId": entity_id,
                    "entityName": entity.name,
                    "entityType": entity.entity_type.value,
                    "eventId": events_b.get(entity_id),
                }
            )
        for entity_id in sorted(set(entities_a) & set(entities_b)):
            entity_a, entity_b = entities_a[entity_id], entities_b[entity_id]
            doc_a = entity_a.model_dump(by_alias=True, exclude_unset=True)
            doc_b = entity_b.model_dump(by_alias=True, exclude_unset=True)
            if doc_a == doc_b:
                continue
            conf_a = (doc_a.get("confidence") or {}).get("score")
            conf_b = (doc_b.get("confidence") or {}).get("score")
            if conf_a != conf_b:
                rows.append(
                    {
                        "kind": "confidenceChanged",
                        "entityId": entity_id,
                        "entityName": entity_b.name,
                        "oldConfidence": conf_a,
                        "newConfidence": conf_b,
                        "eventId": events_b.get(entity_id),
                    }
                )
            status_a, status_b = doc_a.get("status"), doc_b.get("status")
            if entity_a.entity_type.value == "claim" and (conf_a != conf_b or status_a != status_b):
                rows.append(
                    {
                        "kind": "contestedClaim",
                        "entityId": entity_id,
                        "entityName": entity_b.name,
                        "aConfidence": conf_a,
                        "bConfidence": conf_b,
                        "aStatus": status_a,
                        "bStatus": status_b,
                    }
                )

    if include_relations:
        relations_a = {r.id: r for r in store_a.list_relations()}
        relations_b = {r.id: r for r in store_b.list_relations()}
        names_a = {e.id: e.name for e in entities_a.values()}
        names_b = {e.id: e.name for e in entities_b.values()}
        for relation_id in sorted(set(relations_b) - set(relations_a)):
            relation = relations_b[relation_id]
            rows.append(
                {
                    "kind": "relationAdded",
                    "relationId": relation_id,
                    "from": relation.from_,
                    "fromName": names_b.get(relation.from_),
                    "to": relation.to,
                    "toName": names_b.get(relation.to),
                    "relationType": relation.relation_type.value,
                    "eventId": events_b.get(relation_id),
                }
            )
        for relation_id in sorted(set(relations_a) - set(relations_b)):
            relation = relations_a[relation_id]
            rows.append(
                {
                    "kind": "relationRemoved",
                    "relationId": relation_id,
                    "from": relation.from_,
                    "fromName": names_a.get(relation.from_),
                    "to": relation.to,
                    "toName": names_a.get(relation.to),
                    "relationType": relation.relation_type.value,
                    "eventId": events_b.get(relation_id),
                }
            )

    return list_envelope(rows)


# =============================================================================
# merge-world
# =============================================================================


class MergeWorldInput(CommandInput):
    from_: str = Field(alias="from", description="The worldId to merge from.")
    into: str | None = Field(
        default=None, description="The worldId (or 'main', the default) to merge into."
    )
    strategy: str = Field(
        default="endorse-all",
        description="'endorse-all' applies every uncontested change 'from' made; 'select' "
        "applies only the named entityIds/eventIds, regardless of contest status (selecting "
        "IS the manual resolution).",
    )
    entity_ids: list[str] | None = Field(default=None, alias="entityIds")
    event_ids: list[str] | None = Field(default=None, alias="eventIds")


def _find_relation_by_id(store: FalkorGraphStore, relation_id: str) -> Relation | None:
    for relation in store.list_relations():
        if relation.id == relation_id:
            return relation
    return None


def merge_world(params: MergeWorldInput, multi: MultiGraph) -> Doc:
    if params.strategy not in ("endorse-all", "select"):
        raise ValidationError("strategy must be 'endorse-all' or 'select'")
    from_record = _require_world(multi, params.from_)
    if from_record.status == "reaped":
        raise ValidationError(
            f"World '{params.from_}' has already been abandoned/merged and cannot be merged again."
        )
    into_id = params.into or MAIN
    if into_id == params.from_:
        raise ValidationError("'from' and 'into' must be different worlds.")
    base_graph = str(from_record.metadata["baseGraph"])
    if into_id != MAIN:
        into_record = _require_world(multi, into_id)
        if str(into_record.metadata["baseGraph"]) != base_graph:
            raise ValidationError(
                f"'{params.from_}' and '{into_id}' fork from different base graphs and cannot "
                "be merged together."
            )
    forked_at = str(from_record.metadata["forkedAt"])
    from_store = multi.get_store(None, world=params.from_)
    into_store = _store_for(multi, base_graph, into_id)

    touched = _last_event_by_record_id(_events_for(multi, base_graph, params.from_))

    if params.strategy == "select":
        selected_ids = set(params.entity_ids or [])
        if params.event_ids:
            by_event = {event_id: record_id for record_id, event_id in touched.items()}
            for event_id in params.event_ids:
                if event_id in by_event:
                    selected_ids.add(by_event[event_id])
        if not selected_ids:
            raise ValidationError(
                "strategy 'select' requires entityIds and/or eventIds naming what to graft."
            )
        unknown = selected_ids - set(touched)
        if unknown:
            raise ValidationError(
                f"select target(s) not touched by world '{params.from_}': {sorted(unknown)}"
            )
        candidate_ids = selected_ids
    else:
        candidate_ids = set(touched)

    contested: list[Doc] = []
    applied_entities: list[Doc] = []
    for entity_id in sorted(candidate_ids):
        from_entity = from_store.read_entity(entity_id)
        if from_entity is None:
            continue  # hard-deleted inside the fork — nothing left to graft
        base_doc = into_store.read_entity_as_of(entity_id, forked_at)
        current_into = into_store.read_entity(entity_id)
        is_contested = (
            params.strategy == "endorse-all"
            and base_doc is not None
            and current_into is not None
            and current_into.model_dump(by_alias=True, exclude_unset=True)
            != base_doc.model_dump(by_alias=True, exclude_unset=True)
        )
        if is_contested:
            contested.append(
                {
                    "entityId": entity_id,
                    "entityName": from_entity.name,
                    "intoValue": current_into.model_dump(by_alias=True, exclude_unset=True)
                    if current_into
                    else None,
                    "fromValue": from_entity.model_dump(by_alias=True, exclude_unset=True),
                }
            )
            continue
        from_doc = from_entity.model_dump(by_alias=True, exclude_unset=True)
        if current_into is None:
            into_store.graft_entity(from_doc)
        else:
            into_store.update_entity(entity_id, from_doc)
        applied_entities.append({"entityId": entity_id, "entityName": from_entity.name})

    applied_ids = {row["entityId"] for row in applied_entities}
    applied_relations: list[Doc] = []
    # Relation candidates are every id `touched` names that resolves to a
    # live relation in `from` (an entity id never does).
    for record_id in sorted(touched):
        relation = _find_relation_by_id(from_store, record_id)
        if relation is None:
            continue
        if params.strategy == "select" and record_id not in candidate_ids:
            continue
        if relation.from_ not in applied_ids and into_store.read_entity(relation.from_) is None:
            continue
        if relation.to not in applied_ids and into_store.read_entity(relation.to) is None:
            continue
        existing = into_store.read_relations(
            relation.from_, relation.to, relation.relation_type.value
        )
        if any(existing_relation.id == record_id for existing_relation in existing):
            continue
        into_store.graft_relation(relation.model_dump(by_alias=True, exclude_unset=True))
        applied_relations.append(
            {"relationId": record_id, "from": relation.from_, "to": relation.to}
        )

    applied = bool(applied_entities or applied_relations)
    if applied:
        multi.refs.update_metadata(WORLD_KIND, params.from_, {"domainStatus": "merged"})

    notices = []
    if contested:
        plural = len(contested) != 1
        notices.append(
            notice(
                "CONTESTED_ON_MERGE",
                f"{len(contested)} entit{'ies' if plural else 'y'} {'were' if plural else 'was'} "
                f"revised in both '{params.from_}' and '{into_id}' since the fork and were not "
                "merged; see 'contested'.",
                hint="Retry with strategy: 'select' and entityIds naming exactly which side wins, "
                "once resolved.",
            )
        )

    return with_notices(
        {
            "from": params.from_,
            "into": into_id,
            "strategy": params.strategy,
            "appliedEntities": applied_entities,
            "appliedRelations": applied_relations,
            "contested": contested,
        },
        notices,
        applied=applied,
    )
