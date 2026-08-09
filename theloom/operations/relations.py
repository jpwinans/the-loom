"""Relation operations.

Semantics, including a few that look like bugs but are intentional:

- Polarity auto-inference for causal types (CAUSAL_POLARITY_DEFAULTS) when the
  caller passes null — on create AND on an update that retypes an edge.
- The polarity partition (causal types carry polarity, structural/epistemic
  types carry none) is an invariant of the stored edge: update-relation gates
  on the *resulting* type/polarity pair, not just create-relation.
- The verification gate runs against the *resolved single store* BEFORE the
  bridge branch — so a cross-graph relation is blocked with an
  entity-does-not-exist gate error, and the bridge auto-creation path is
  unreachable from the CLI. The store-level bridge
  capability (MultiGraph.create_relation) remains for when the gate is off.
- The endpoint gate (the endpoint exists AND is not retracted) is one verdict —
  guards.endpoint_error — for both arities: create-relation reads its two
  endpoints, create-relations prefetches every endpoint status in the target
  graph. Neither is the lenient way in.
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
    CAUSAL_RELATION_TYPES,
    Entity,
    EntityFilter,
    EntityStatus,
    Polarity,
    RelationCreate,
    RelationFilter,
    RelationType,
    Strength,
)
from theloom.operations.common import CommandInput, UuidStr, resolve_entity_ref
from theloom.operations.entity import compact_entity_doc
from theloom.operations.notices import Doc, list_envelope
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.verification.guards import (
    endpoint_error,
    non_causal_polarity_error,
    relation_gate_errors,
)

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
    # A per-item default, not a batch-wide override: an item's own `graph`
    # always wins when set. Without this field, `CommandInput`'s
    # `extra="ignore"` silently dropped a top-level `{"graph": ...}` and the
    # whole batch fell through to whatever each item's own `graph` said
    # (usually nothing — the default graph, i.e. production). Optional and
    # additive, so a caller that never passed a top-level `graph` sees no
    # behavior change.
    graph: str | None = Field(
        default=None,
        description="Default graph for any item that omits its own `graph` — an item's "
        "own `graph` always wins. Without this, a top-level `graph` on "
        "create-relations was silently ignored (extra fields are dropped) and "
        "the batch fell through to each item's own graph, usually the default "
        "graph.",
    )


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
    """``relationType`` is the *new* type; ``relationId`` selects which
    parallel edge between the pair to update (default: the oldest)."""

    from_: UuidStr = Field(alias="from")
    to: UuidStr
    relation_id: UuidStr | None = Field(default=None, alias="relationId")
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    polarity: Polarity | None = None
    strength: Strength | None = None
    evidence: str | None = None
    graph: str | None = None


class DeleteRelationInput(CommandInput):
    """``relationType``/``relationId`` select which parallel edge between the
    pair to retract (default: the oldest)."""

    from_: UuidStr = Field(alias="from")
    to: UuidStr
    relation_id: UuidStr | None = Field(default=None, alias="relationId")
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    hard: bool | None = None
    graph: str | None = None


class ListRelationsInput(CommandInput):
    from_: UuidStr | None = Field(default=None, alias="from")
    to: UuidStr | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    polarity: Polarity | None = None
    session: str | None = None
    graph: str | None = None


class GetRelationsInput(CommandInput):
    """Addressed by ``entityId`` or by ``name`` — exactly one."""

    entity_id: UuidStr | None = Field(default=None, alias="entityId")
    name: str | None = None
    direction: str | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None
    follow_bridges: bool | None = None
    compact: bool | None = None


class GetNeighborsInput(CommandInput):
    """Addressed by ``entityId`` or by ``name`` — exactly one."""

    entity_id: UuidStr | None = Field(default=None, alias="entityId")
    name: str | None = None
    direction: str | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None
    follow_bridges: bool | None = None
    compact: bool | None = None


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
    if not errors:
        return
    message = f"Relation creation blocked by verification gate: {'; '.join(errors)}"
    # Whether to append the "verify both entities exist" hint is decided from
    # the store directly — never by pattern-matching the gate's own prose,
    # which could misfire on unrelated wording.
    missing_endpoint = store.read_entity(item.from_) is None or store.read_entity(item.to) is None
    if missing_endpoint:
        raise OperationError(
            f"Failed to create relation from {item.from_} to {item.to}: "
            f"{message}. Use list_entities to verify both entities exist."
        )
    raise OperationError(f"Error creating relation: {message}")


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


def gated_relation_spec(params: RelationItem, multi: MultiGraph) -> RelationCreate:
    """Run the create-relation verification gate — including the causal
    polarity defaults — and return the spec it would write, without writing.

    The seam for callers that need several gated relations committed as ONE
    all-or-none store batch (``GraphStore.create_relations``) instead of N
    independent single-edge commits: gate each item through here, then hand
    the surviving specs to the store together.
    """
    polarity = _effective_polarity(params)
    _gate_or_raise(params, multi, polarity)
    return _spec(params, polarity)


def create_relation(params: CreateRelationInput, multi: MultiGraph) -> dict[str, Any]:
    spec = gated_relation_spec(params, multi)

    # Gate passed → both endpoints exist in the resolved store, so this is a
    # same-graph relation; the bridge branch is unreachable with the gate on
    # (see module docstring).
    store = multi.get_store(params.graph)
    relation = store.create_relation(spec)
    return _with_endpoint_name(relation.model_dump(by_alias=True, exclude_unset=True), store)


def create_relations(params: CreateRelationsInput, multi: MultiGraph) -> dict[str, Any]:
    continue_on_error = params.continue_on_error if params.continue_on_error is not None else True
    applied = 0
    failed = 0
    bridges_created = 0
    errors: list[dict[str, Any]] = []
    # Valid specs accumulate per target graph and are created in ONE
    # transactional UNWIND batch per graph.
    pending: dict[str | None, list[RelationCreate]] = {}

    def flush() -> None:
        nonlocal applied
        for graph, specs in pending.items():
            if specs:
                multi.get_store(graph).create_relations(specs)
        pending.clear()

    # Prefetch every endpoint's effective status per graph so the endpoint gate
    # is one query per graph instead of two reads per item. Status, not just
    # existence: the gate refuses a retracted endpoint (guards.endpoint_error),
    # and the batch may not be the lenient way in. The same fetch also builds
    # a name index (desire 11) so a failed item's error row can carry
    # fromName/toName beside the ids, at no extra query.
    known_status: dict[str | None, dict[str, EntityStatus]] = {}
    known_names: dict[str | None, dict[str, str]] = {}

    def status_for(graph: str | None) -> dict[str, EntityStatus]:
        if graph not in known_status:
            store = multi.get_store(graph)
            entities = store.list_entities(_ALL_STATUS_FILTER)
            known_status[graph] = {e.id: e.effective_status for e in entities}
            known_names[graph] = {e.id: e.name for e in entities}
        return known_status[graph]

    for item in params.relations:
        # The batch-level `graph` is a per-item default: an item that names
        # its own `graph` always wins, even inside a batch that also set a
        # top-level one.
        effective_graph = item.graph if item.graph is not None else params.graph
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
        graph_status = status_for(effective_graph)
        for role, endpoint in (("Source", item.from_), ("Target", item.to)):
            error = endpoint_error(role, endpoint, graph_status.get(endpoint))
            if error is not None:
                gate_errors.append(error)

        if gate_errors:
            failed += 1
            message = f"Relation creation blocked by verification gate: {'; '.join(gate_errors)}"
            names = known_names.get(effective_graph, {})
            errors.append(
                {
                    "from": item.from_,
                    "to": item.to,
                    "fromName": names.get(item.from_),
                    "toName": names.get(item.to),
                    "error": message,
                }
            )
            if not continue_on_error:
                flush()  # persist the valid prefix before raising
                raise OperationError(f"Error creating relations batch: {message}")
            continue

        pending.setdefault(effective_graph, []).append(_spec(item, polarity))
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
    store = multi.get_store(params.graph)
    relation = store.read_relation(params.from_, params.to)
    if relation is None:
        raise NotFoundError(
            f"Relation not found from {params.from_} to {params.to}. "
            "Use list_relations to see available relations."
        )
    return _with_endpoint_name(relation.model_dump(by_alias=True, exclude_unset=True), store)


def _with_endpoint_names(docs: list[Doc], store: FalkorGraphStore) -> list[Doc]:
    """Stamp ``fromName``/``toName`` beside every relation doc's ``from``/``to``
    id (desire 11) — one batched ``read_entity_docs`` for every endpoint any
    doc references, never a lookup per row. A row's endpoint absent from the
    store (a retracted-then-hard-deleted entity) simply carries no name,
    rather than the whole row failing."""
    wanted = {doc["from"] for doc in docs} | {doc["to"] for doc in docs}
    if not wanted:
        return docs
    endpoints = store.read_entity_docs(wanted)
    names = {entity_id: doc.get("name", "") for entity_id, doc in endpoints.items()}
    return [
        {**doc, "fromName": names.get(doc["from"]), "toName": names.get(doc["to"])} for doc in docs
    ]


def _with_endpoint_name(doc: Doc, store: FalkorGraphStore) -> Doc:
    """Single-relation-doc form of ``_with_endpoint_names`` — every command
    that reads or writes exactly one relation (create/read/update-relation)
    routes through this rather than shipping bare ``from``/``to`` ids, the
    same "self-describing enough to read without a join" contract
    ``list-relations`` already carries (desire 11)."""
    return _with_endpoint_names([doc], store)[0]


def read_relations(params: ReadRelationsInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    relations = store.read_relations(
        params.from_,
        params.to,
        params.relation_type.value if params.relation_type else None,
    )
    docs = [r.model_dump(by_alias=True, exclude_unset=True) for r in relations]
    return list_envelope(_with_endpoint_names(docs, store))


def _gated_update_polarity(
    params: UpdateRelationInput, multi: MultiGraph
) -> tuple[bool, str | None]:
    """The polarity the update must land on, and whether it has to be written.

    The create-time partition (causal types carry polarity, everything else
    carries none) is an invariant of the stored edge, not just of creation, so
    an update that changes the type and/or the polarity is gated on the
    *resulting* edge. Causal defaults are inferred exactly as on create.
    """
    if params.relation_type is None and not params.provided("polarity"):
        return False, None
    store = multi.get_store(params.graph)
    existing = store.read_relations(params.from_, params.to)
    if params.relation_id is not None:
        existing = [r for r in existing if r.id == params.relation_id]
    if not existing:
        # No such edge — store.update_relation raises the NotFoundError.
        return params.provided("polarity"), params.polarity
    current = existing[0]
    relation_type = params.relation_type or current.relation_type
    polarity: str | None = params.polarity if params.provided("polarity") else current.polarity
    if relation_type in CAUSAL_RELATION_TYPES:
        if polarity is None:
            polarity = CAUSAL_POLARITY_DEFAULTS.get(relation_type)
    elif polarity is not None:
        message = "Relation update blocked by verification gate: " + non_causal_polarity_error(
            relation_type.value, polarity
        )
        raise OperationError(f"Error updating relation: {message}")
    return polarity != current.polarity or params.provided("polarity"), polarity


def update_relation(params: UpdateRelationInput, multi: MultiGraph) -> dict[str, Any]:
    write_polarity, polarity = _gated_update_polarity(params, multi)
    updates: dict[str, Any] = {}
    if params.relation_type is not None:
        updates["relationType"] = params.relation_type.value
    if write_polarity:
        updates["polarity"] = polarity
    if params.strength is not None:
        updates["strength"] = params.strength.value
    if params.provided("evidence"):
        updates["evidence"] = params.evidence
    store = multi.get_store(params.graph)
    try:
        relation = store.update_relation(
            params.from_, params.to, updates, relation_id=params.relation_id
        )
    except NotFoundError:
        raise NotFoundError(
            f"Relation not found from {params.from_} to {params.to}. "
            "Use list_relations to verify the relation exists before updating."
        ) from None
    return _with_endpoint_name(relation.model_dump(by_alias=True, exclude_unset=True), store)


def delete_relation(params: DeleteRelationInput, multi: MultiGraph) -> str:
    """Retract a relation: the edge leaves every read path but its final doc is
    kept with a closed system-time interval, so history stays queryable.
    ``hard: true`` erases the edge instead."""
    hard = bool(params.hard)
    try:
        multi.get_store(params.graph).delete_relation(
            params.from_,
            params.to,
            params.relation_type.value if params.relation_type is not None else None,
            relation_id=params.relation_id,
            hard=hard,
        )
    except NotFoundError:
        raise NotFoundError(
            f"Relation not found from {params.from_} to {params.to}. "
            "Use list_relations to verify the relation exists before deleting."
        ) from None
    verb = "deleted" if hard else "retracted"
    return f"Relation from {params.from_} to {params.to} {verb} successfully."


def list_relations(params: ListRelationsInput, multi: MultiGraph) -> dict[str, Any]:
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
            docs = _with_endpoint_names(fetch(graph_name), multi.get_store(graph_name))
            for doc in docs:
                doc["graph"] = graph_name
                results.append(doc)
        return list_envelope(results)
    docs = _with_endpoint_names(fetch(params.graph), multi.get_store(params.graph))
    return list_envelope(docs)


# =============================================================================
# get-relations / get-neighbors (bridge inclusion)
# =============================================================================


def _wire_doc(raw: dict[str, Any]) -> dict[str, Any]:
    """The wire projection of a stored doc — the same envelope ``read_entity``
    plus ``model_dump`` produced, so batching the fetch cannot drift the shape."""
    return Entity.model_validate(raw).model_dump(by_alias=True, exclude_unset=True)


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


def get_relations(params: GetRelationsInput, multi: MultiGraph) -> dict[str, Any]:
    direction = params.direction or "both"
    relation_type = params.relation_type.value if params.relation_type else None
    store = multi.get_store(params.graph)
    entity_id = resolve_entity_ref(
        store, entity_id=params.entity_id, name=params.name, id_field="entityId"
    )
    relations = store.get_relations(entity_id, direction, relation_type)  # type: ignore[arg-type]
    results = _with_endpoint_names(
        [r.model_dump(by_alias=True, exclude_unset=True) for r in relations], store
    )

    for bridge in multi.bridges.list_bridges({"entity_id": entity_id}):
        if not _bridge_matches(bridge, entity_id, direction, relation_type):
            continue
        row = dict(bridge)
        if params.follow_bridges:
            from_store = multi.get_store(bridge["from_graph"])
            from_entity = from_store.read_entity(bridge["from"])
            if from_entity is not None:
                doc = from_entity.model_dump(by_alias=True, exclude_unset=True)
                row["from_entity"] = compact_entity_doc(doc) if params.compact else doc
                row["fromName"] = doc["name"]
            to_store = multi.get_store(bridge["to_graph"])
            to_entity = to_store.read_entity(bridge["to"])
            if to_entity is not None:
                doc = to_entity.model_dump(by_alias=True, exclude_unset=True)
                row["to_entity"] = compact_entity_doc(doc) if params.compact else doc
                row["toName"] = doc["name"]
        results.append(row)
    return list_envelope(results)


def get_neighbors(params: GetNeighborsInput, multi: MultiGraph) -> dict[str, Any]:
    direction = params.direction or "both"
    relation_type = params.relation_type.value if params.relation_type else None
    store = multi.get_store(params.graph)
    entity_id = resolve_entity_ref(
        store, entity_id=params.entity_id, name=params.name, id_field="entityId"
    )
    relations = store.get_relations(entity_id, direction, relation_type)  # type: ignore[arg-type]

    # One (relationType, direction) per unique neighbor id, first-seen — the
    # same neighbor set/order the store's own dedup (filters.extract_neighbor_ids)
    # has always produced, now keeping which edge made the connection.
    edges: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    for relation in relations:
        if direction == "outgoing":
            neighbor_id, edge_direction = relation.to, "out"
        elif direction == "incoming":
            neighbor_id, edge_direction = relation.from_, "in"
        elif relation.from_ == entity_id:
            neighbor_id, edge_direction = relation.to, "out"
        else:
            neighbor_id, edge_direction = relation.from_, "in"
        if neighbor_id not in edges:
            edges[neighbor_id] = (relation.relation_type.value, edge_direction)
            order.append(neighbor_id)

    # The whole neighbourhood is hydrated in ONE query: a single-id read costs a
    # label scan, so a per-neighbour loop is a round trip per edge on exactly the
    # hub entities that have the most of them.
    neighbor_docs = store.read_entity_docs(order)
    results: list[dict[str, Any]] = []
    for neighbor_id in order:
        raw = neighbor_docs.get(neighbor_id)
        if raw is None:
            continue
        doc = _wire_doc(raw)
        if params.compact:
            doc = compact_entity_doc(doc)
        edge_relation_type, edge_direction = edges[neighbor_id]
        doc["relationType"] = edge_relation_type
        doc["direction"] = edge_direction
        results.append(doc)

    # Cross-graph rows: collected first, then hydrated one query per remote
    # graph rather than one per bridge.
    bridge_rows: list[tuple[str, str, str, str]] = []
    seen_cross_graph: set[str] = set()
    for bridge in multi.bridges.list_bridges({"entity_id": entity_id}):
        if not _bridge_matches(bridge, entity_id, direction, relation_type):
            continue
        if bridge["from"] == entity_id:
            neighbor_id, neighbor_graph, edge_direction = bridge["to"], bridge["to_graph"], "out"
        else:
            neighbor_id, neighbor_graph, edge_direction = bridge["from"], bridge["from_graph"], "in"
        if neighbor_id in seen_cross_graph:
            continue
        seen_cross_graph.add(neighbor_id)
        bridge_rows.append((neighbor_id, neighbor_graph, edge_direction, bridge["relationType"]))

    cross_docs: dict[str, dict[str, dict[str, Any]]] = {}
    if params.follow_bridges:
        ids_by_graph: dict[str, list[str]] = {}
        for neighbor_id, neighbor_graph, _, _ in bridge_rows:
            ids_by_graph.setdefault(neighbor_graph, []).append(neighbor_id)
        cross_docs = {
            graph_name: multi.get_store(graph_name).read_entity_docs(ids)
            for graph_name, ids in ids_by_graph.items()
        }

    for neighbor_id, neighbor_graph, edge_direction, bridge_type in bridge_rows:
        raw = cross_docs.get(neighbor_graph, {}).get(neighbor_id)
        if raw is not None:
            doc = _wire_doc(raw)
            if params.compact:
                doc = compact_entity_doc(doc)
            doc["graph"] = neighbor_graph
            doc["relationType"] = bridge_type
            doc["direction"] = edge_direction
            results.append(doc)
            continue
        results.append(
            {
                "id": neighbor_id,
                "graph": neighbor_graph,
                "stub": True,
                "relationType": bridge_type,
                "direction": edge_direction,
            }
        )
    return list_envelope(results)
