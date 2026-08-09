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

from typing import Any

from pydantic import Field

from theloom.errors import ValidationError
from theloom.model import EntityFilter, EntityStatus
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
    ``theloom.operations.receipts``'s differs, which read the same shape).
    ``entities_merged`` is the one event type shaped differently (a
    ``"primary"``/``"secondary"`` pair, no top-level ``"entity"`` key) and
    maps to BOTH entity ids it touched — a merge inside a world is
    invisible to merge-world's own candidate set without this."""
    out: dict[str, str] = {}
    for event in events:
        if event.type == "entities_merged":
            for key in ("primary", "secondary"):
                record = event.payload.get(key)
                if isinstance(record, dict) and isinstance(record.get("id"), str):
                    out[record["id"]] = event.id
            # Redirected relations carry no dedicated event of their own
            # (apply_entity_merge folds them into this one commit); the
            # supersedes relation DOES get one (a separate relation_created
            # right behind this event in the same commit), which -- being
            # later in stream order -- correctly overwrites this fallback
            # mapping on its own turn through this loop.
            for relation_doc in event.payload.get("redirectedRelations") or []:
                if isinstance(relation_doc, dict) and isinstance(relation_doc.get("id"), str):
                    out[relation_doc["id"]] = event.id
            continue
        record = event.payload.get("entity") or event.payload.get("relation")
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            out[record["id"]] = event.id
    return out


def _relation_touched_ids(events: list[Event]) -> dict[str, str]:
    """The relation-only subset of ``_last_event_by_record_id``'s domain:
    id -> the last event that touched it, but ONLY ids that were ever a
    relation. ``merge_world``'s relation loop needs this split because a
    bare presence probe can't tell "this id was never a relation" (skip)
    apart from "this id WAS a relation and the fork removed it" (propagate
    the removal or contest it) — both look identical as a miss against
    ``from_store``'s live projection. Kept as a second pass over the same
    events, rather than a single richer return type, so the well-tested
    combined view stays untouched."""
    out: dict[str, str] = {}
    for event in events:
        if event.type == "entities_merged":
            for relation_doc in event.payload.get("redirectedRelations") or []:
                if isinstance(relation_doc, dict) and isinstance(relation_doc.get("id"), str):
                    out[relation_doc["id"]] = event.id
            continue
        relation = event.payload.get("relation")
        if isinstance(relation, dict) and isinstance(relation.get("id"), str):
            out[relation["id"]] = event.id
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


def _entity_field_rows(
    entity_id: str, entity_name: Any, event: Event, world_id: str, previous: Doc | None, doc: Doc
) -> list[Doc]:
    """Per-field diff-worlds rows for one entity incarnation change (an
    ``entity_updated``/``entity_status_changed`` event, or one half of an
    ``entities_merged`` event) — the field-level classification
    ``_entity_event_rows`` and its ``entities_merged`` branch both need,
    factored out so the taxonomy (confidence/terminal-status/generic) is
    defined exactly once."""
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
    if event.type == "entities_merged":
        # Shaped differently (a primary/secondary pair, no top-level
        # "entity" key) but every bit as real a write to both entities as
        # an ordinary update -- each gets its own field-level rows against
        # its own "previous" half of the payload.
        rows: list[Doc] = []
        for doc_key, previous_key in (
            ("primary", "previousPrimary"),
            ("secondary", "previousSecondary"),
        ):
            doc = event.payload[doc_key]
            previous = event.payload.get(previous_key)
            rows.extend(
                _entity_field_rows(doc["id"], doc.get("name"), event, world_id, previous, doc)
            )
        return rows
    if event.type not in ("entity_updated", "entity_status_changed"):
        return []
    doc = event.payload["entity"]
    previous = event.payload.get("previous")
    return _entity_field_rows(doc["id"], doc.get("name"), event, world_id, previous, doc)


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
    if event.type == "entities_merged":
        # apply_entity_merge redirects every relation the secondary held to
        # the primary in the SAME commit, with no dedicated event of its
        # own (the supersedes relation is the one exception -- it earns a
        # real relation_created, handled above). The pre-redirect from/to
        # is not carried in this event's payload, so the row is honest
        # about what it does and doesn't know: the redirect happened, not
        # what the endpoint used to be.
        rows: list[Doc] = []
        for relation_doc in event.payload.get("redirectedRelations") or []:
            rows.append(
                {
                    "kind": "relationRevised",
                    "relationId": relation_doc["id"],
                    "from": relation_doc.get("from"),
                    "fromName": names.get(relation_doc.get("from")),
                    "to": relation_doc.get("to"),
                    "toName": names.get(relation_doc.get("to")),
                    "field": "endpoints",
                    "old": None,
                    "new": {"from": relation_doc.get("from"), "to": relation_doc.get("to")},
                    "eventId": event.id,
                    "world": world_id,
                }
            )
        return rows
    if event.type != "relation_updated":
        return []
    doc = event.payload["relation"]
    previous = event.payload.get("previous")
    rows = []
    for field, old_value, new_value in receipts_ops.field_diffs(previous, doc):
        row: Doc = {
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
        if field in ("from", "to"):
            # Endpoints are no longer immutable (Part 5's merge-world can
            # redirect a relation's from/to -- FalkorGraphStore.
            # update_relation's own docstring), so an endpoint-only
            # revision is real information the row-level from/to above
            # doesn't carry on its own (that pair is always the CURRENT
            # endpoint, i.e. `new`; the OLD one otherwise has no row at
            # all). Named the same way every other id in this function is.
            if isinstance(old_value, str):
                row["oldName"] = names.get(old_value)
            if isinstance(new_value, str):
                row["newName"] = names.get(new_value)
        rows.append(row)
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

    from_events = _events_for(multi, base_graph, params.from_)
    touched = _last_event_by_record_id(from_events)
    relation_ids = set(_relation_touched_ids(from_events))
    entity_ids_touched = set(touched) - relation_ids

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
    for entity_id in sorted(candidate_ids & entity_ids_touched):
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
                        "kind": "entity",
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
                    "kind": "entity",
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
    # Relation candidates are every id `_relation_touched_ids` names (an id
    # is looked up by kind up front, not inferred from a read miss — a
    # relation removed inside the fork is also absent from `from_store`'s
    # live projection, which a read-miss probe cannot tell apart from "this
    # id was never a relation").
    from_relations_by_id = {relation.id: relation for relation in from_store.list_relations()}
    into_relations_by_id = {relation.id: relation for relation in into_store.list_relations()}
    base_relations_by_id = {
        relation.id: relation for relation in into_store.read_graph_as_of(forked_at).relations
    }
    for record_id in sorted(candidate_ids & relation_ids):
        from_relation = from_relations_by_id.get(record_id)
        current_relation = into_relations_by_id.get(record_id)
        base_relation = base_relations_by_id.get(record_id)
        is_contested = (
            params.strategy == "endorse-all"
            and base_relation is not None
            and current_relation is not None
            and current_relation.model_dump(by_alias=True, exclude_unset=True)
            != base_relation.model_dump(by_alias=True, exclude_unset=True)
        )

        if from_relation is None:
            # Removed inside the fork — soft-invalidated or hard-deleted
            # (WorldGraphStore's relation tombstoning makes hard delete
            # trustworthy here the same way it does for entities above).
            # Mirrors the entity branch: the removal either propagates or
            # is contested, never silently dropped.
            if current_relation is None:
                continue  # already gone from `into` too — nothing to do
            if is_contested:
                contested.append(
                    {
                        "kind": "relation",
                        "relationId": record_id,
                        "from": current_relation.from_,
                        "to": current_relation.to,
                        "intoValue": current_relation.model_dump(by_alias=True, exclude_unset=True),
                        "fromValue": None,
                        "fromDeleted": True,
                    }
                )
                continue
            into_store.delete_relation(
                current_relation.from_,
                current_relation.to,
                current_relation.relation_type.value,
                record_id,
                hard=True,
            )
            applied_relations.append(
                {
                    "relationId": record_id,
                    "from": current_relation.from_,
                    "to": current_relation.to,
                    "deleted": True,
                }
            )
            continue

        if is_contested:
            contested.append(
                {
                    "kind": "relation",
                    "relationId": record_id,
                    "from": from_relation.from_,
                    "to": from_relation.to,
                    "intoValue": current_relation.model_dump(by_alias=True, exclude_unset=True)
                    if current_relation
                    else None,
                    "fromValue": from_relation.model_dump(by_alias=True, exclude_unset=True),
                }
            )
            continue

        if (
            from_relation.from_ not in applied_ids
            and into_store.read_entity(from_relation.from_) is None
        ):
            continue
        if from_relation.to not in applied_ids and into_store.read_entity(from_relation.to) is None:
            continue

        from_doc = from_relation.model_dump(by_alias=True, exclude_unset=True)
        if current_relation is None:
            into_store.graft_relation(from_doc)
        else:
            into_store.update_relation(
                current_relation.from_,
                current_relation.to,
                from_doc,
                current_relation.relation_type.value,
                record_id,
            )
        applied_relations.append(
            {"relationId": record_id, "from": from_relation.from_, "to": from_relation.to}
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
                f"{len(contested)} item{'s' if plural else ''} {'were' if plural else 'was'} "
                f"changed in both '{params.from_}' and '{into_id}' since the fork and "
                f"{'were' if plural else 'was'} not merged; see 'contested'.",
                hint="Retry with strategy: 'select' and entityIds/eventIds naming exactly which "
                "side wins, once resolved.",
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
