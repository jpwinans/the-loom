"""``what-changed``: replay a span of the event log as a compact diff (desire 1).

The event log (``theloom.store.events.EventLog``) is already the append-only
truth of every mutation; this module is a pure read surface over it, not a
new source of truth. A caller names a span three ways:

- ``eventIds`` — replay exactly the ids a prior mutating response's
  ``eventIds`` receipt named (the primary use case: "did that command do what
  it reported?"), in the ids' own order.
- ``fromEventId``/``toEventId`` — a contiguous stream range, both bounds
  inclusive; either may be omitted for an open bound.
- neither — the whole log (bounded by ``limit``, which also caps the raw
  event count read for the other two forms).

Each replayed event becomes zero or more diff rows,
``{entity, entityName, field, old, new, causedBy}`` (plus, for a relation
event, ``from``/``to``/``fromName``/``toName`` beside it — desire 11's
"names travel with ids" applied to the diff itself, not just to
list-relations). ``entity`` holds whichever record actually changed — an
entity id for an entity event, a relation id for a relation event — since the
underlying event log the spec asks this to replay is not entity-exclusive; a
"recordType" field disambiguates and is documented per row.

Field-level diffing intentionally excludes ``id``/``created_at``/``updated_at``
(identity and write-timestamp bookkeeping that changes on every mutation
regardless of content, so it would drown every real diff in noise) but
otherwise reports every field that actually differs, comparing the event's
``previous`` payload (an update-shaped event) or ``{}`` (a creation) or the
current doc against ``{}`` (an erasure — a hard delete has no ``previous``
because there is no later doc for one to have been captured against).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import Field

from theloom.operations.common import CommandInput
from theloom.operations.notices import Doc, list_envelope
from theloom.store.events import Event, EventLog
from theloom.store.multigraph import MultiGraph
from theloom.store.worlds import require_world, world_graph_name

# Bookkeeping fields that change on every write regardless of content — never
# meaningful "what changed" rows on their own.
_SKIP_FIELDS = frozenset({"id", "created_at", "updated_at"})

# Relation/bridge endpoints are immutable once created and are already
# surfaced on every relation-kind row as from/to/fromName/toName — repeating
# them as field-level diff rows on creation (old=None, new=<id>) would be
# pure noise, not information the row-level from/to doesn't already carry.
_ENDPOINT_FIELDS = frozenset({"from", "to"})

_DEFAULT_LIMIT = 500


class WhatChangedInput(CommandInput):
    graph: str | None = None
    event_ids: list[str] | None = Field(
        default=None,
        alias="eventIds",
        description=(
            "Replay exactly these event ids (e.g. a prior mutating response's "
            "eventIds), in the order given. Mutually exclusive with "
            "fromEventId/toEventId — when set, those are ignored."
        ),
    )
    from_event_id: str | None = Field(
        default=None,
        alias="fromEventId",
        description="Inclusive lower bound of the stream span to replay. Omit for the "
        "start of the log.",
    )
    to_event_id: str | None = Field(
        default=None,
        alias="toEventId",
        description="Inclusive upper bound of the stream span to replay. Omit for the "
        "end of the log.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description=f"Caps how many raw events are read before diffing (default "
        f"{_DEFAULT_LIMIT}). Ignored when eventIds is given — an explicit id list is "
        "never truncated.",
    )


def _field_diffs(
    old: Doc | None, new: Doc | None, *, skip: frozenset[str] = _SKIP_FIELDS
) -> list[tuple[str, Any, Any]]:
    old = old or {}
    new = new or {}
    rows: list[tuple[str, Any, Any]] = []
    for field in sorted({*old.keys(), *new.keys()} - skip):
        old_value, new_value = old.get(field), new.get(field)
        if old_value != new_value:
            rows.append((field, old_value, new_value))
    return rows


def field_diffs(old: Doc | None, new: Doc | None) -> list[tuple[str, Any, Any]]:
    """Public form of ``_field_diffs`` (the default skip-set — id/created_at/
    updated_at), for other replay consumers that want what-changed's exact
    notion of "what changed" between two doc incarnations without
    re-deriving it. ``diff-worlds`` (``theloom.operations.worlds``) uses
    this for its per-field entity/relation rows, so both commands agree
    about what counts as a change by construction, not by convention."""
    return _field_diffs(old, new)


# One raw row before name resolution: which record changed, its kind, and the
# field-level diff. `record` is the full doc the row's id/name is drawn from
# (the *new* doc when there is one, else the old one) so name resolution can
# also fall back to a name embedded in the event itself when the store no
# longer has the record (a hard delete).
class _RawRow:
    __slots__ = ("record_id", "kind", "field", "old", "new", "record")

    def __init__(
        self, record_id: str, kind: str, field: str, old: Any, new: Any, record: Doc
    ) -> None:
        self.record_id = record_id
        self.kind = kind
        self.field = field
        self.old = old
        self.new = new
        self.record = record


def _rows_for(kind: str, record_id: str, old: Doc | None, new: Doc | None) -> list[_RawRow]:
    record = new or old or {}
    skip = _SKIP_FIELDS | _ENDPOINT_FIELDS if kind in ("relation", "bridge") else _SKIP_FIELDS
    return [
        _RawRow(record_id, kind, field, old_value, new_value, record)
        for field, old_value, new_value in _field_diffs(old, new, skip=skip)
    ]


def _entity_created(event: Event) -> list[_RawRow]:
    doc = event.payload["entity"]
    return _rows_for("entity", doc["id"], None, doc)


def _entity_updated(event: Event) -> list[_RawRow]:
    doc = event.payload["entity"]
    previous = event.payload.get("previous")
    return _rows_for("entity", doc["id"], previous, doc)


def _entity_deleted(event: Event) -> list[_RawRow]:
    doc = event.payload["entity"]
    return _rows_for("entity", doc["id"], doc, None)


def _relation_created(event: Event) -> list[_RawRow]:
    doc = event.payload["relation"]
    return _rows_for("relation", doc["id"], None, doc)


def _relation_updated(event: Event) -> list[_RawRow]:
    doc = event.payload["relation"]
    previous = event.payload.get("previous")
    return _rows_for("relation", doc["id"], previous, doc)


def _relation_removed(event: Event) -> list[_RawRow]:
    doc = event.payload["relation"]
    return _rows_for("relation", doc["id"], doc, None)


def _bridge_created(event: Event) -> list[_RawRow]:
    doc = event.payload["bridge"]
    return _rows_for("bridge", doc["id"], None, doc)


def _bridge_removed(event: Event) -> list[_RawRow]:
    doc = event.payload["bridge"]
    return _rows_for("bridge", doc["id"], doc, None)


def _entities_merged(event: Event) -> list[_RawRow]:
    primary, secondary = event.payload["primary"], event.payload["secondary"]
    rows = _rows_for("entity", primary["id"], event.payload.get("previousPrimary"), primary)
    rows += _rows_for("entity", secondary["id"], event.payload.get("previousSecondary"), secondary)
    return rows


_REF_EVENT_TYPES = frozenset(
    {"ref_registered", "ref_touched", "ref_expired", "ref_reaped", "ref_metadata_updated"}
)


def _ref_lifecycle(event: Event, history: dict[str, Doc]) -> list[_RawRow]:
    """Ref lifecycle events (``theloom.store.refs`` — session workspaces,
    branchable belief worlds) carry their full doc verbatim as the payload,
    the same "payload is the document" convention every other event here
    follows, but with no explicit ``previous`` field the way an entity/
    relation update has one. Replaying *in order* recovers it anyway:
    ``history`` remembers the last doc seen per ref id within this replay's
    own span, so a later lifecycle event (a world's ``abandon-world``, a
    session's reap) diffs against what actually preceded it instead of
    falling back to ``_unrecognized``'s ``old: null`` for every field —
    the exact weakness Part 5's build was asked not to repeat. A ref whose
    creation falls outside this replay's own span still reports ``old:
    null`` for its first event here, honestly: there is nothing earlier in
    the span to compare against.
    """
    doc = dict(event.payload)
    record_id = str(doc.get("id") or "")
    previous = history.get(record_id)
    history[record_id] = doc
    return _rows_for(f"ref:{doc.get('kind', 'ref')}", record_id, previous, doc)


def _unrecognized(event: Event) -> list[_RawRow]:
    """A generic, honest fallback for an event type this module has no
    specific differ for (a bridge/chunk event type added after this was
    written, or a graph nobody expects, e.g. the reserved ``_chunks`` stream):
    every top-level payload key is reported as a "field", set from nothing —
    nothing about the payload is silently dropped, but there is no
    previous/current distinction to diff against."""
    record_id = str(event.payload.get("id") or event.payload.get("chunkId") or event.type)
    return _rows_for("event", record_id, None, dict(event.payload))


_DIFFERS: dict[str, Callable[[Event], list[_RawRow]]] = {
    "entity_created": _entity_created,
    "entity_updated": _entity_updated,
    "entity_status_changed": _entity_updated,
    "entity_retracted": _entity_updated,
    "entity_deleted": _entity_deleted,
    "relation_created": _relation_created,
    "relation_updated": _relation_updated,
    "relation_invalidated": _relation_removed,
    "relation_deleted": _relation_removed,
    "bridge_created": _bridge_created,
    "bridge_migrated": _bridge_created,
    "bridge_invalidated": _bridge_removed,
    "entities_merged": _entities_merged,
}


def _diff_event(event: Event) -> list[_RawRow]:
    return _DIFFERS.get(event.type, _unrecognized)(event)


def _resolve_names(
    rows: list[tuple[Event, _RawRow]], multi: MultiGraph, graph: str | None
) -> dict[str, str]:
    """One batched ``read_entity_docs`` for every entity id any row
    references (the changed entity itself, or a relation/bridge's from/to) —
    the same "hydrate the whole neighbourhood in one query" discipline
    ``get_neighbors`` uses, never a lookup per row."""
    wanted: set[str] = set()
    for _event, row in rows:
        if row.kind == "entity":
            wanted.add(row.record_id)
        elif row.kind in ("relation", "bridge"):
            for key in ("from", "to"):
                value = row.record.get(key)
                if isinstance(value, str):
                    wanted.add(value)
    if not wanted:
        return {}
    store = multi.get_store(graph)
    docs = store.read_entity_docs(wanted)
    return {entity_id: doc.get("name", "") for entity_id, doc in docs.items()}


def _output_row(event: Event, row: _RawRow, names: dict[str, str]) -> Doc:
    out: Doc = {
        "entity": row.record_id,
        "recordType": row.kind,
        "field": row.field,
        "old": row.old,
        "new": row.new,
        "causedBy": event.caused_by,
        "eventId": event.id,
        "eventType": event.type,
    }
    if row.kind == "entity" and row.record_id in names:
        out["entityName"] = names[row.record_id]
    if row.kind in ("relation", "bridge"):
        from_id, to_id = row.record.get("from"), row.record.get("to")
        if isinstance(from_id, str):
            out["from"] = from_id
            out["fromName"] = names.get(from_id)
        if isinstance(to_id, str):
            out["to"] = to_id
            out["toName"] = names.get(to_id)
    return out


def _event_log_for(multi: MultiGraph, graph: str | None, world: str | None) -> EventLog:
    """The one span ``what-changed`` replays: a plain graph's own stream by
    default (unchanged), or -- when ``world`` names a non-``main`` belief
    world -- that world's own segment instead of its parent's. A world's
    writes never land in the parent's stream (``theloom.store.worlds``), so
    reading the parent's log while a world is active would silently show
    the wrong span rather than the fork's own history.

    ``require_world`` validates the ref exists (and is spelled correctly)
    before ``multi.event_log`` ever runs: that call happily opens a stream
    for ANY name, existing or not (an event log is schemaless, lazily
    created on first append) -- without this check, ``what-changed`` against
    a typo'd or already-purged worldId would silently read an empty stream
    and report ``{"items": [], "count": 0}``, indistinguishable from "this
    world genuinely made no changes," instead of the NOT_FOUND every other
    world-addressed command raises for the same mistake.
    """
    if world in (None, "", "main"):
        return multi.event_log(graph)
    require_world(multi, world)
    return multi.event_log(world_graph_name(world))


def what_changed(params: WhatChangedInput, multi: MultiGraph) -> Doc:
    log = _event_log_for(multi, params.graph, params.world)
    if params.event_ids:
        events: Sequence[Event] = log.read_ids(params.event_ids)
    else:
        limit = params.limit if params.limit is not None else _DEFAULT_LIMIT
        events = log.read_range(params.from_event_id, params.to_event_id, count=limit)

    pairs: list[tuple[Event, _RawRow]] = []
    ref_history: dict[str, Doc] = {}
    for event in events:
        rows = (
            _ref_lifecycle(event, ref_history)
            if event.type in _REF_EVENT_TYPES
            else _diff_event(event)
        )
        for row in rows:
            pairs.append((event, row))

    names = _resolve_names(pairs, multi, params.graph)
    diffs = [_output_row(event, row, names) for event, row in pairs]
    return list_envelope(diffs)
