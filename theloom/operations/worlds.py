"""Branchable belief worlds (desire 12 / Part 5): the CLI-facing commands —
``fork-world``, ``list-worlds``, ``abandon-world``, ``diff-worlds``,
``merge-world``. Thin wiring for fork/list/abandon, matching
``theloom.operations.sessions``'s pattern exactly (the mechanism is
``theloom.store.worlds``, reusing ``theloom.store.refs.RefRegistry`` with
``kind="world"``); ``diff-worlds``/``merge-world`` do their own semantic
work here because it needs notices/envelope wiring that ``theloom/store/``
must not depend on.

``diff-worlds`` replays the event log of whichever side(s) name a world —
never a full-doc snapshot comparison — using
``theloom.operations.receipts.field_diffs``, the exact function
``what-changed`` diffs with, so both commands agree about what a "change"
is by construction, not by convention. This is load-bearing, not stylistic:
the store's overlay makes a fork's projection a *superset* of its parent's
(an inherited, untouched entity is still visible through it), so comparing
full docs between two projections can only ever detect entities the fork's
own doc disagrees with its parent about — a rename back to the same name,
a status flip into a terminal state with no confidence change, or any
write a snapshot diff happens not to select for, drops out silently. The
event log has no such blind spot: every write the fork's own segment ever
recorded produces at least one row, so ``diff-worlds``' event ids are
always a superset of what ``merge-world`` can act on (see
``tests/test_worlds.py``'s ``test_diff_worlds_event_ids_are_a_superset_of_
what_merge_world_applies``).
"""

from __future__ import annotations

from pydantic import Field

from theloom.errors import ValidationError
from theloom.model import EntityFilter, EntityStatus, Relation
from theloom.operations import receipts as receipts_ops
from theloom.operations.common import CommandInput
from theloom.operations.notices import Doc, list_envelope, notice, with_notices
from theloom.store.events import Event
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.store.worlds import WORLD_KIND, require_world, world_graph_name

_ALL_STATUS_FILTER = EntityFilter.model_validate(
    {"statusFilter": [status.value for status in EntityStatus]}
)

# Status values a transition INTO counts as invalidating the entity, the
# same way a hard/soft delete does — "retracted" is explicitly irreversible
# (theloom.store.falkor's transition table refuses to reactivate one).
_TERMINAL_ENTITY_STATUSES = frozenset({"retracted"})

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
    include_reaped: bool | None = Field(
        default=None,
        alias="includeReaped",
        description="Include abandoned/merged worlds. Defaults to false: a reaped world is "
        "never gone (list-worlds' history is still there, same as list-sessions'), but the "
        "default view does not grow monotonically as forks are abandoned/merged over a "
        "build's lifetime. Pass true for the full history.",
    )


def list_worlds(params: ListWorldsInput, multi: MultiGraph) -> Doc:
    include_reaped = params.include_reaped if params.include_reaped is not None else False
    return list_envelope(multi.list_worlds(include_reaped=include_reaped))


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


def _base_graph_of(multi: MultiGraph, world_id: str | None) -> str | None:
    """``world_id``'s own base graph, or ``None`` when ``world_id`` denotes
    ``main`` (which carries no base graph of its own — a caller must supply
    one, or infer it from the other side of a comparison)."""
    if world_id in (None, "", MAIN):
        return None
    return str(require_world(multi, world_id).metadata["baseGraph"])


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


def _entity_event_rows(event: Event, world_id: str, names: dict[str, str]) -> list[Doc]:
    """Every entity event in a world's own segment, as diff-worlds rows —
    exactly one taxonomy of "what changed", derived from the event itself
    rather than a before/after snapshot comparison (see the module
    docstring for why that distinction is load-bearing)."""
    if event.type == "entity_created":
        doc = event.payload["entity"]
        return [
            {
                "kind": "entityAdded",
                "entityId": doc["id"],
                "entityName": doc.get("name"),
                "entityType": doc.get("entityType"),
                "eventId": event.id,
                "world": world_id,
            }
        ]
    if event.type == "entity_deleted":
        doc = event.payload["entity"]
        return [
            {
                "kind": "entityInvalidated",
                "entityId": doc["id"],
                "entityName": doc.get("name"),
                "entityType": doc.get("entityType"),
                "reason": "deleted",
                "eventId": event.id,
                "world": world_id,
            }
        ]
    if event.type not in ("entity_updated", "entity_status_changed"):
        return []
    doc = event.payload["entity"]
    previous = event.payload.get("previous")
    entity_id, entity_name = doc["id"], doc.get("name")
    rows: list[Doc] = []
    for field, old_value, new_value in receipts_ops.field_diffs(previous, doc):
        base: Doc = {
            "entityId": entity_id,
            "entityName": entity_name,
            "eventId": event.id,
            "world": world_id,
        }
        if field == "confidence":
            rows.append(
                {
                    **base,
                    "kind": "confidenceChanged",
                    "oldConfidence": (old_value or {}).get("score"),
                    "newConfidence": (new_value or {}).get("score"),
                }
            )
        elif field == "status" and new_value in _TERMINAL_ENTITY_STATUSES:
            rows.append(
                {
                    **base,
                    "kind": "entityInvalidated",
                    "reason": "status",
                    "oldStatus": old_value,
                    "newStatus": new_value,
                }
            )
        else:
            rows.append(
                {
                    **base,
                    "kind": "entityRevised",
                    "field": field,
                    "old": old_value,
                    "new": new_value,
                }
            )
    return rows


def _relation_event_rows(event: Event, world_id: str, names: dict[str, str]) -> list[Doc]:
    """The relation twin of ``_entity_event_rows``."""
    if event.type == "relation_created":
        doc = event.payload["relation"]
        return [
            {
                "kind": "relationAdded",
                "relationId": doc["id"],
                "from": doc["from"],
                "fromName": names.get(doc["from"]),
                "to": doc["to"],
                "toName": names.get(doc["to"]),
                "relationType": doc.get("relationType"),
                "eventId": event.id,
                "world": world_id,
            }
        ]
    if event.type in ("relation_invalidated", "relation_deleted"):
        doc = event.payload["relation"]
        return [
            {
                "kind": "relationRemoved",
                "relationId": doc["id"],
                "from": doc["from"],
                "fromName": names.get(doc["from"]),
                "to": doc["to"],
                "toName": names.get(doc["to"]),
                "relationType": doc.get("relationType"),
                "eventId": event.id,
                "world": world_id,
            }
        ]
    if event.type != "relation_updated":
        return []
    doc = event.payload["relation"]
    previous = event.payload.get("previous")
    rows: list[Doc] = []
    for field, old_value, new_value in receipts_ops.field_diffs(previous, doc):
        if field in ("from", "to"):  # endpoints are immutable; already on every row above
            continue
        rows.append(
            {
                "kind": "relationRevised",
                "relationId": doc["id"],
                "from": doc["from"],
                "fromName": names.get(doc["from"]),
                "to": doc["to"],
                "toName": names.get(doc["to"]),
                "field": field,
                "old": old_value,
                "new": new_value,
                "eventId": event.id,
                "world": world_id,
            }
        )
    return rows


def diff_worlds(params: DiffWorldsInput, multi: MultiGraph) -> Doc:
    if params.a == params.b:
        raise ValidationError("diff-worlds needs two different worlds ('a' and 'b' were the same).")
    base_graph = _shared_base_graph(multi, params.a, params.b)
    store_a = _store_for(multi, base_graph, params.a)
    store_b = _store_for(multi, base_graph, params.b)
    include_entities = params.scope in (None, "entities")
    include_relations = params.scope in (None, "relations")

    # Names for relation endpoints: every entity currently visible from
    # either side, so a relation row can name its from/to even if one
    # side's own log never touched that particular entity.
    entities_a = {e.id: e for e in store_a.list_entities(_ALL_STATUS_FILTER)}
    entities_b = {e.id: e for e in store_b.list_entities(_ALL_STATUS_FILTER)}
    names = {e.id: e.name for e in entities_a.values()}
    names.update({e.id: e.name for e in entities_b.values()})

    rows: list[Doc] = []
    for world_id in (params.a, params.b):
        if world_id in (None, "", MAIN):
            continue  # main has no segment of its own to replay
        for event in _events_for(multi, base_graph, world_id):
            if include_entities:
                rows.extend(_entity_event_rows(event, world_id, names))
            if include_relations:
                rows.extend(_relation_event_rows(event, world_id, names))

    if include_entities:
        for entity_id in sorted(set(entities_a) & set(entities_b)):
            entity_a, entity_b = entities_a[entity_id], entities_b[entity_id]
            if entity_a.entity_type.value != "claim":
                continue
            doc_a = entity_a.model_dump(by_alias=True, exclude_unset=True)
            doc_b = entity_b.model_dump(by_alias=True, exclude_unset=True)
            conf_a = (doc_a.get("confidence") or {}).get("score")
            conf_b = (doc_b.get("confidence") or {}).get("score")
            status_a, status_b = doc_a.get("status"), doc_b.get("status")
            if conf_a != conf_b or status_a != status_b:
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
    from_record = require_world(multi, params.from_)
    if from_record.status == "reaped":
        raise ValidationError(
            f"World '{params.from_}' has already been abandoned/merged and cannot be merged again."
        )
    into_id = params.into or MAIN
    if into_id == params.from_:
        raise ValidationError("'from' and 'into' must be different worlds.")
    base_graph = str(from_record.metadata["baseGraph"])
    if into_id != MAIN:
        into_record = require_world(multi, into_id)
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
        base_doc = into_store.read_entity_as_of(entity_id, forked_at)
        current_into = into_store.read_entity(entity_id)
        is_contested = (
            params.strategy == "endorse-all"
            and base_doc is not None
            and current_into is not None
            and current_into.model_dump(by_alias=True, exclude_unset=True)
            != base_doc.model_dump(by_alias=True, exclude_unset=True)
        )
        name_source = from_entity or current_into or base_doc
        entity_name = name_source.name if name_source is not None else entity_id

        if from_entity is None:
            # Hard-deleted inside the fork (theloom.store.worlds.
            # WorldGraphStore's tombstoning makes this reachable and
            # trustworthy — see Part 5's fixes). A silent no-op here would
            # be exactly the kind of gap the Agent Contract forbids: the
            # deletion either propagates or is contested, never dropped.
            if current_into is None:
                continue  # already gone from `into` too — nothing to do
            if is_contested:
                contested.append(
                    {
                        "entityId": entity_id,
                        "entityName": entity_name,
                        "intoValue": current_into.model_dump(by_alias=True, exclude_unset=True),
                        "fromValue": None,
                        "fromDeleted": True,
                    }
                )
                continue
            into_store.delete_entity(entity_id, hard=True)
            applied_entities.append(
                {"entityId": entity_id, "entityName": entity_name, "deleted": True}
            )
            continue

        if is_contested:
            contested.append(
                {
                    "entityId": entity_id,
                    "entityName": entity_name,
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
        applied_entities.append({"entityId": entity_id, "entityName": entity_name})

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
