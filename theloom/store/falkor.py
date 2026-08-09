"""FalkorDB-backed GraphStore.

Storage model — one FalkorDB graph per named Loom graph:

- Entity  = node ``:_Entity {id, _doc, tx_from}``. ``_doc`` is the exact wire
  JSON (key presence preserved — the store serves exactly what was written,
  explicit nulls included). ``tx_from`` is the system time of the doc's current
  incarnation.
- Entity read index = four *derived* properties on the same node, projected
  from ``_doc`` on every write: ``_status`` (effective status — unset means
  'active'), ``_type`` (entityType), ``_name`` (lowercased name) and
  ``_search`` (lowercased name + observations). They carry no information
  ``_doc`` doesn't already have; they exist so status/entityType/name/query
  filtering runs server-side instead of shipping and validating the whole
  graph per list call. Graphs written before the index existed are tolerated
  (a missing ``_status`` always passes the prefilter) and migrated in place on
  the first filtered read.
- Relation = typed edge ``-[:<relationType> {id, _doc}]->`` between entity
  nodes; parallel edges between the same pair are native.
- Version  = node ``:_EntityVersion {entity_id, _doc, tx_from, tx_to}`` — an
  invalidated prior incarnation. Updates snapshot, they never erase;
  ``read_entity_as_of`` reads these.
- Closed-out relation = node ``:_RelationVersion {relation_id, _doc, tx_from,
  tx_to}`` — the same bi-temporal shape for an edge that has left the live
  projection (``invalidate_relation``). An edge carries no status field, so
  retiring one means closing its system-time interval, not flipping a flag.

Both version labels are read by ``read_graph_as_of``, the graph-level answer to
"state as of time T": it is the only read that can see a closed interval, so
anything reconstructing a past graph from the live projection alone is missing
exactly what these nodes record.
- Metadata = singleton node ``:_GraphMeta {_doc}``.

Every mutation goes through ``_commit``: ONE Cypher statement plus its event
append, sent as one Redis MULTI/EXEC transaction, so the projection and the log
move together (see ``theloom.store.space.GraphSpace._commit`` for the exact
guarantee, and ``_commit_steps`` for the single batch case that needs more than
one statement and what it owes in return — both, with the graph handle, the
event log, the paged read and the vector index, come from ``GraphSpace``, which
the chunk store shares). Status changes are validated against the lifecycle
transition table here in the store.

Deletion invalidates. ``delete_entity`` retracts (status 'retracted', prior
incarnation snapshotted, attached edges closed out bi-temporally, embedding
vector dropped so the retracted entity leaves the semantic reads too) and
``delete_relation`` closes the edge's system-time interval; both take
``hard=True`` for true erasure, which is the only path that destroys history.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from theloom.errors import NotFoundError, ValidationError
from theloom.model import (
    ALL_ENTITY_TYPES,
    ALL_RELATION_TYPES,
    Entity,
    EntityCreate,
    EntityFilter,
    EntityStatus,
    Relation,
    RelationCreate,
    RelationFilter,
    is_valid_transition,
)
from theloom.store import receipts
from theloom.store.base import Direction, GraphStore
from theloom.store.filters import (
    apply_entity_filters,
    apply_relation_filters,
    extract_neighbor_ids,
)
from theloom.store.paging import PAGE_SIZE
from theloom.store.read_port import GraphSnapshot
from theloom.store.space import VECTOR_PROPERTY, GraphSpace
from theloom.timeutil import iso_now

_ENTITY_LABEL = "_Entity"
_VECTOR_PROPERTY = VECTOR_PROPERTY

_IMMUTABLE_ENTITY_FIELDS = ("id", "created_at")
_IMMUTABLE_RELATION_FIELDS = ("id", "created_at")

# The derived read-index properties, in wire-doc projection order. Named
# without the leading underscore here; the node property is "_" + field.
_INDEX_FIELDS = ("status", "type", "name", "search")

# `_search` folds the name and every observation into one haystack with this
# separator. filters.py tests the name and each observation *separately*, so a
# query containing the separator can straddle two of them: it matches the
# folded haystack and nothing at all in the oracle. That keeps the prefilter a
# superset (which is all it promises) but makes it inexact — see
# ``_pushdown_is_exact``.
_SEARCH_SEPARATOR = "\n"


def _index_props(doc: Mapping[str, Any]) -> dict[str, str]:
    """Project a wire doc onto the derived read-index properties.

    Mirrors theloom/store/filters.py exactly: effective status (unset means
    'active'), and case-insensitive name / name+observations haystacks.
    """
    name = str(doc.get("name") or "")
    observations = doc.get("observations") or []
    return {
        "status": str(doc.get("status") or "active"),
        "type": str(doc.get("entityType") or ""),
        "name": name.lower(),
        "search": _SEARCH_SEPARATOR.join([name, *(str(obs) for obs in observations)]).lower(),
    }


def _index_params(doc: Mapping[str, Any], prefix: str) -> dict[str, str]:
    """Query parameters for a doc's index projection, under a name prefix."""
    return {f"{prefix}{field.capitalize()}": value for field, value in _index_props(doc).items()}


def _index_literal(prefix: str) -> str:
    """Cypher map entries for a CREATE pattern."""
    return ", ".join(f"_{field}: ${prefix}{field.capitalize()}" for field in _INDEX_FIELDS)


def _index_assignments(alias: str, prefix: str) -> str:
    """Cypher SET assignments for an already-bound node."""
    return ", ".join(f"{alias}._{field} = ${prefix}{field.capitalize()}" for field in _INDEX_FIELDS)


def _pushdown_is_exact(filter: EntityFilter | None) -> bool:
    """True when the Cypher prefilter alone decides membership — i.e. nothing
    is left for the Python pass to reject. Only then may LIMIT/count run
    server-side, because both are computed over the prefilter's candidate set.

    Two ways a field can leave work behind: it has no server-side counterpart
    at all (version/session/sourcedFrom), or its pushed-down predicate is only
    a superset of the real one (a ``query`` straddling the ``_search`` fold).
    """
    if filter is None:
        return True
    if filter.query is not None and _SEARCH_SEPARATOR in filter.query:
        return False
    return (
        filter.version is None
        and filter.min_version is None
        and filter.session is None
        and not filter.sourced_from
        and not filter.exclude_sourced_from
    )


def _entity_prefilter(filter: EntityFilter | None) -> tuple[str, dict[str, Any]]:
    """The server-side WHERE clause + params for an entity list read.

    Every predicate is written ``(prop IS NULL OR <test>)`` so a node that
    predates the read index always survives into the candidate set and is
    decided exactly by the Python pass.
    """
    statuses = (
        [s.value for s in filter.status_filter]
        if filter is not None and filter.status_filter is not None
        else ["active"]
    )
    clauses = ["(n._status IS NULL OR n._status IN $fStatuses)"]
    params: dict[str, Any] = {"fStatuses": statuses}
    if filter is not None:
        if filter.entity_type is not None:
            clauses.append("(n._type IS NULL OR n._type = $fType)")
            params["fType"] = filter.entity_type.value
        if filter.name is not None:
            clauses.append("(n._name IS NULL OR n._name CONTAINS $fName)")
            params["fName"] = filter.name.lower()
        if filter.query is not None:
            clauses.append("(n._search IS NULL OR n._search CONTAINS $fQuery)")
            params["fQuery"] = filter.query.lower()
    return " AND ".join(clauses), params


# The close-out snapshot for an edge leaving the live projection, as a clause
# over an UNWIND'd ``row``. A fragment rather than a statement of its own: a
# retraction closes out its edges in the same statement that retracts the
# entity, because Redis MULTI does not roll back (see ``_commit``).
_RELATION_VERSION_CLAUSE = (
    "CREATE (:_RelationVersion {relation_id: row.id, _doc: row.doc, "
    "tx_from: row.txFrom, tx_to: $now})"
)


def _relation_version_rows(
    edges: Sequence[tuple[Mapping[str, Any], str | None]], now: str
) -> list[dict[str, Any]]:
    """``$rows`` for ``_RELATION_VERSION_CLAUSE``: each edge's wire doc plus
    the system time its live incarnation opened at — the edge's ``tx_from``
    property, or the doc's own ``created_at`` for an edge never updated."""
    return [
        {
            "id": doc["id"],
            "doc": json.dumps(dict(doc)),
            "txFrom": tx_from or doc.get("created_at", now),
        }
        for doc, tx_from in edges
    ]


def _transition_error(from_status: str | None, to_status: str) -> str:
    """Lifecycle transition error message, code-classified VALIDATION_ERROR."""
    effective = from_status or "active"
    if effective == "retracted":
        return (
            f"Invalid status transition from '{effective}' to '{to_status}'. "
            "Retracted entities cannot be reactivated - this status indicates "
            "the entity was withdrawn due to error or invalidity."
        )
    return f"Invalid status transition from '{effective}' to '{to_status}'."


class FalkorGraphStore(GraphSpace, GraphStore):
    """GraphStore over one FalkorDB graph + one event stream.

    The graph handle, the connection, the event log, the commit primitive, the
    paged read and the vector index all come from ``GraphSpace`` — the shared
    store machinery — so this class is only the entity/relation rows and what
    they mean.
    """

    _VECTOR_LABEL = _ENTITY_LABEL

    # -- entities ----------------------------------------------------------------

    def create_entity(self, spec: EntityCreate) -> Entity:
        now = iso_now()
        doc = spec.model_dump(by_alias=True, exclude_unset=True)
        doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
        entity = Entity.model_validate(doc)
        self._commit(
            (
                f"CREATE (n:_Entity {{id: $id, _doc: $doc, tx_from: $now, "
                f"{_index_literal('ix')}}})",
                {
                    "id": doc["id"],
                    "doc": json.dumps(doc),
                    "now": now,
                    **_index_params(doc, "ix"),
                },
            ),
            [("entity_created", {"entity": doc})],
        )
        return entity

    def import_entity_doc(self, doc: Mapping[str, Any]) -> None:
        """Write a pre-existing wire doc verbatim (migration path; no event)."""
        self._query(
            f"CREATE (n:_Entity {{id: $id, _doc: $doc, tx_from: $tx, {_index_literal('ix')}}})",
            {
                "id": doc["id"],
                "doc": json.dumps(doc),
                "tx": doc.get("updated_at", iso_now()),
                **_index_params(doc, "ix"),
            },
        )

    def adopt_entity(self, doc: Mapping[str, Any], tx_from: str | None = None) -> Entity:
        """Materialize a verbatim copy of an entity doc read from elsewhere
        into *this* graph — the copy-on-write half of a branchable belief
        world's overlay (``theloom.store.worlds.WorldGraphStore``): a fork
        reads an inherited entity straight from its parent, but the first
        *write* addressing that id needs a local incarnation to snapshot and
        swap, because the parent's own node must never be touched (worlds:
        "main is never mutable from inside a fork").

        Routed through ``_commit`` (the one commit primitive every write
        goes through) like every other mutation, but with an empty events
        list: the doc is byte-identical to what a reader already saw through
        the overlay, so nothing observable has changed yet and there is
        nothing for ``what-changed``/``diff-worlds`` to report. The *next*
        mutation on this id (an ordinary ``update_entity``/``delete_entity``)
        emits the real event, with this doc as its ``previous`` — exactly
        mirroring ``import_entity_doc``'s "write doc verbatim; no event"
        contract, just through the transactional primitive instead of a bare
        query, since a copy-on-write is triggered by live traffic rather
        than a one-shot migration script.
        """
        doc = dict(doc)
        self._commit(
            (
                f"CREATE (n:_Entity {{id: $id, _doc: $doc, tx_from: $tx, {_index_literal('ix')}}})",
                {
                    "id": doc["id"],
                    "doc": json.dumps(doc),
                    "tx": tx_from or doc.get("updated_at") or iso_now(),
                    **_index_params(doc, "ix"),
                },
            ),
            [],
        )
        return Entity.model_validate(doc)

    def graft_entity(self, doc: Mapping[str, Any]) -> Entity:
        """Create a verbatim copy of an entity doc — id preserved — as a
        REAL, event-logged creation in this graph: ``merge-world``'s
        counterpart to ``adopt_entity``. A fork's copy-on-write is invisible
        (the doc a reader already saw, just relocated); grafting an entity
        from a world's segment into another world (typically ``main``) via
        an explicit merge is a genuine, user-visible write, so it earns a
        real ``entity_created`` event other than the id being pre-chosen
        rather than minted."""
        doc = dict(doc)
        index_literal = _index_literal("ix")
        self._commit(
            (
                f"CREATE (n:_Entity {{id: $id, _doc: $doc, tx_from: $now, {index_literal}}})",
                {
                    "id": doc["id"],
                    "doc": json.dumps(doc),
                    "now": iso_now(),
                    **_index_params(doc, "ix"),
                },
            ),
            [("entity_created", {"entity": doc})],
        )
        return Entity.model_validate(doc)

    # -- vectors (entity vectors live in the same store) ------------------------
    #
    # The index itself — create, width, OPERATIONAL barrier, k-NN with its
    # one retry — is ``GraphSpace``'s, keyed on ``_VECTOR_LABEL``. What is
    # entity-specific is only what a vector is attached to and read back with.

    def set_entity_vector(self, entity_id: str, vector: list[float]) -> None:
        """Attach/update an entity's embedding vector (same store, one query)."""
        self._query(
            "MATCH (n:_Entity {id: $id}) SET n._embedding = vecf32($vector)",
            {"id": entity_id, "vector": vector},
        )

    def get_entity_vectors(self) -> dict[str, list[float]]:
        """All embedded entities' vectors keyed by entity id."""
        rows = self._rows_paged(
            "MATCH (n:_Entity) WHERE n._embedding IS NOT NULL "
            "RETURN n.id, n._embedding ORDER BY id(n)"
        )
        return {row[0]: [float(x) for x in row[1]] for row in rows}

    def has_entity_vectors(self) -> bool:
        """True when at least one entity carries an embedding — a LIMIT 1 probe
        rather than ``get_entity_vectors()``, for callers that only need to know
        whether searching this graph is worth embedding a query for."""
        return self._stored_vector_dimension() is not None

    def vector_knn(self, query_vector: list[float], k: int) -> list[tuple[str, float]]:
        """(entity id, cosine similarity) for the k nearest embedded entities;
        empty when nothing is embedded (see ``GraphSpace._vector_knn``)."""
        return self._vector_knn(query_vector, k)

    def read_entity(self, entity_id: str) -> Entity | None:
        doc = self._read_doc(entity_id)
        return Entity.model_validate(doc) if doc is not None else None

    def _read_doc(self, entity_id: str) -> dict[str, Any] | None:
        rows = self._rows("MATCH (n:_Entity {id: $id}) RETURN n._doc", {"id": entity_id})
        if not rows:
            return None
        loaded: dict[str, Any] = json.loads(rows[0][0])
        return loaded

    def read_entity_as_of(self, entity_id: str, timestamp: str) -> Entity | None:
        rows = self._rows("MATCH (n:_Entity {id: $id}) RETURN n._doc, n.tx_from", {"id": entity_id})
        if rows and rows[0][1] <= timestamp:
            return Entity.model_validate(json.loads(rows[0][0]))
        version_rows = self._rows(
            "MATCH (v:_EntityVersion {entity_id: $id}) "
            "WHERE v.tx_from <= $t AND $t < v.tx_to RETURN v._doc",
            {"id": entity_id, "t": timestamp},
        )
        if version_rows:
            return Entity.model_validate(json.loads(version_rows[0][0]))
        return None

    def read_graph_as_of(self, timestamp: str) -> GraphSnapshot:
        """The whole graph as it stood at ``timestamp`` (see the read port)."""
        entities = self._entities_as_of(timestamp)
        present = {entity.id for entity in entities}
        relations = [
            relation
            for relation in self._relations_as_of(timestamp)
            if relation.from_ in present and relation.to in present
        ]
        return GraphSnapshot(entities=entities, relations=relations)

    def _entities_as_of(self, timestamp: str) -> list[Entity]:
        """Every entity's incarnation current at ``timestamp``, creation order.

        Two reads, not one per entity: the live nodes (whose ``tx_from`` opens
        the current incarnation) and the closed ``:_EntityVersion`` intervals
        containing the bound. A live node younger than the bound falls back to
        its version; with no covering version the entity was not yet born.
        """
        live = self._rows_paged("MATCH (n:_Entity) RETURN n.id, n._doc, n.tx_from ORDER BY id(n)")
        stale = [row[0] for row in live if row[2] > timestamp]
        versions = self._entity_versions_as_of(stale, timestamp) if stale else {}
        entities: list[Entity] = []
        for entity_id, doc, tx_from in live:
            covering = doc if tx_from <= timestamp else versions.get(entity_id)
            if covering is not None:
                entities.append(Entity.model_validate(json.loads(covering)))
        return entities

    def _entity_versions_as_of(self, entity_ids: Sequence[str], timestamp: str) -> dict[str, str]:
        """``entity_id -> _doc`` for the closed version interval containing the
        bound, for the ids asked about. Intervals per entity are disjoint, so
        at most one row can match each."""
        rows = self._rows_paged(
            "MATCH (v:_EntityVersion) WHERE v.entity_id IN $ids "
            "AND v.tx_from <= $t AND $t < v.tx_to "
            "RETURN v.entity_id, v._doc ORDER BY id(v)",
            {"ids": list(entity_ids), "t": timestamp},
        )
        return {row[0]: row[1] for row in rows}

    def _relations_as_of(self, timestamp: str) -> list[Relation]:
        """Every edge whose system-time interval was open at ``timestamp``.

        Two sources, because an edge's interval can be open or closed: the live
        edges whose open interval had begun by the bound (``tx_from``, or the
        doc's ``created_at`` for an edge never updated), then the
        ``:_RelationVersion`` snapshots whose closed ``[tx_from, tx_to)``
        contains it — an edge retired or updated since the bound was there in
        its earlier incarnation then, and it is this second read that keeps
        history queryable rather than merely stored. Live edges come first,
        each group in creation order; an id appearing in both (an edge whose
        live interval covers the bound) is answered by the live doc, the one
        still in the projection.
        """
        rows = self._rows_paged("MATCH ()-[r]->() RETURN r._doc, r.tx_from ORDER BY id(r)")
        docs: list[dict[str, Any]] = [
            doc
            for doc, tx_from in ((json.loads(row[0]), row[1]) for row in rows)
            if (tx_from or doc.get("created_at", "")) <= timestamp
        ]
        live_ids = {doc["id"] for doc in docs}
        version_rows = self._rows_paged(
            "MATCH (v:_RelationVersion) WHERE v.tx_from <= $t AND $t < v.tx_to "
            "RETURN v.relation_id, v._doc ORDER BY id(v)",
            {"t": timestamp},
        )
        seen = set(live_ids)
        for relation_id, doc_json in version_rows:
            if relation_id in seen:
                continue
            seen.add(relation_id)
            docs.append(json.loads(doc_json))
        return [Relation.model_validate(doc) for doc in docs]

    def update_entity(self, entity_id: str, updates: Mapping[str, Any]) -> Entity:
        current = self._read_doc(entity_id)
        if current is None:
            raise NotFoundError("Entity not found")

        new_status = updates.get("status")
        status_changed = "status" in updates and new_status != current.get("status")
        if new_status is not None and not is_valid_transition(current.get("status"), new_status):
            raise ValidationError(_transition_error(current.get("status"), str(new_status)))

        now = iso_now()
        merged = {**current, **dict(updates), "updated_at": now}
        for field in _IMMUTABLE_ENTITY_FIELDS:
            merged[field] = current[field]
        entity = Entity.model_validate(merged)

        # One atomic query: snapshot the prior incarnation, then swap the doc
        # (and its derived read-index projection).
        event_type = "entity_status_changed" if status_changed else "entity_updated"
        self._commit(
            self._swap_doc_step(entity_id, merged, now),
            [(event_type, {"entity": merged, "previous": current})],
        )
        return entity

    def _swap_doc_step(
        self, entity_id: str, doc: Mapping[str, Any], now: str
    ) -> tuple[str, dict[str, Any]]:
        """The invalidate-never-overwrite step: snapshot the entity's current
        incarnation as a closed ``:_EntityVersion``, then swap in the new doc
        and its derived read-index projection."""
        return (
            "MATCH (n:_Entity {id: $id}) "
            "CREATE (:_EntityVersion {entity_id: $id, _doc: n._doc, "
            "tx_from: n.tx_from, tx_to: $now}) "
            f"SET n._doc = $doc, n.tx_from = $now, {_index_assignments('n', 'ix')}",
            {
                "id": entity_id,
                "doc": json.dumps(dict(doc)),
                "now": now,
                **_index_params(doc, "ix"),
            },
        )

    def delete_entity(self, entity_id: str, hard: bool = False) -> Entity:
        """Retract an entity, or erase it outright with ``hard=True``.

        Retraction is what "delete" means in an event-sourced store that never
        overwrites: the doc moves to status 'retracted', its prior incarnation
        is snapshotted as a closed ``:_EntityVersion``, every attached edge is
        closed out bi-temporally (``:_RelationVersion``), and the entity's
        embedding vector is dropped. The entity leaves every default read —
        ``list_entities`` filters to active, and with no vector it is out of
        the ANN index every semantic read goes through — while
        ``read_entity_as_of`` can still reconstruct what the graph looked like
        before, which a hard delete makes impossible. Retraction is terminal
        (no transition leads back out of it), so the dropped vector can never
        be wanted again; re-embedding would rebuild it regardless.

        All of that is ONE Cypher statement: it is the whole point of the
        retraction that the doc, its history and its edges move together.

        Returns the record as it now stands: the retracted doc, or the erased
        doc under ``hard``. Retracting an already-retracted entity is a no-op.
        """
        doc = self._read_doc(entity_id)
        if doc is None:
            raise NotFoundError("Entity not found")
        if hard:
            self._commit(
                ("MATCH (n:_Entity {id: $id}) DETACH DELETE n", {"id": entity_id}),
                [("entity_deleted", {"entity": doc})],
            )
            return Entity.model_validate(doc)

        status = doc.get("status")
        if not is_valid_transition(status, EntityStatus.RETRACTED):
            raise ValidationError(_transition_error(status, EntityStatus.RETRACTED.value))
        now = iso_now()
        retracted = {**doc, "status": EntityStatus.RETRACTED.value, "updated_at": now}
        entity = Entity.model_validate(retracted)
        attached = self._attached_relation_docs(entity_id)
        cypher, params = self._swap_doc_step(entity_id, retracted, now)
        # The vector is a derived index entry, like the four ``_index_*``
        # props: it goes when the entity leaves the live projection.
        cypher += f", n.{_VECTOR_PROPERTY} = NULL"
        if attached:
            cypher += (
                f" WITH n UNWIND $rows AS row {_RELATION_VERSION_CLAUSE} "
                "WITH DISTINCT n MATCH (n)-[r]-() DELETE r"
            )
            params["rows"] = _relation_version_rows(attached, now)
        self._commit(
            (cypher, params),
            [
                (
                    "entity_retracted",
                    {
                        "entity": retracted,
                        "previous": doc,
                        "invalidatedRelations": [edge_doc for edge_doc, _ in attached],
                    },
                )
            ],
        )
        return entity

    def _attached_relation_docs(self, entity_id: str) -> list[tuple[dict[str, Any], str | None]]:
        """Every live edge touching an entity, in insertion order: its wire
        doc plus the ``tx_from`` its current incarnation opened at (None for
        an edge never updated, whose interval opened at ``created_at``)."""
        edges: dict[str, tuple[dict[str, Any], str | None]] = {}
        for row in self._rows_paged(
            "MATCH (n:_Entity {id: $id})-[r]-() RETURN r._doc, r.tx_from ORDER BY id(r)",
            {"id": entity_id},
        ):
            doc = json.loads(row[0])
            edges.setdefault(doc["id"], (doc, row[1]))
        return list(edges.values())

    def read_entity_doc(self, entity_id: str) -> dict[str, Any] | None:
        """The verbatim wire doc — key order preserved (synthesis `raw` output
        serializes docs into text, where JS object key order is contract)."""
        return self._read_doc(entity_id)

    def read_entity_docs(self, entity_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Wire docs for many ids in ONE query, keyed by id; ids with no live
        node are simply absent. A single-id read costs a label scan, so any
        command that hydrates a whole neighbourhood (the consumption commands
        resolve hundreds of rows at once) must fetch the set, not the elements.
        """
        ids = list(dict.fromkeys(entity_ids))
        if not ids:
            return {}
        rows = self._rows_paged(
            "MATCH (n:_Entity) WHERE n.id IN $ids RETURN n._doc ORDER BY id(n)", {"ids": ids}
        )
        docs: list[dict[str, Any]] = [json.loads(row[0]) for row in rows]
        return {doc["id"]: doc for doc in docs}

    def read_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        """``read_entity_docs`` in the model dialect — the read port's bulk
        entity read. One query, ids with no live node absent."""
        return {
            entity_id: Entity.model_validate(doc)
            for entity_id, doc in self.read_entity_docs(entity_ids).items()
        }

    def apply_entity_merge(
        self,
        primary_doc: Mapping[str, Any],
        secondary_doc: Mapping[str, Any],
        redirects: Sequence[Mapping[str, Any]],
        supersedes_doc: Mapping[str, Any] | None,
        previous_primary: Mapping[str, Any],
        previous_secondary: Mapping[str, Any],
        now: str,
    ) -> None:
        """Apply a precomputed entity merge (see operations/merge.py) as ONE
        atomic query + one ``entities_merged`` event.

        The single query: bi-temporal version snapshots and doc swaps for both
        entities, per-relation redirection (delete + recreate under the same
        relation id, types inlined because edge types cannot be parametrized),
        and the primary→secondary supersedes edge. Each redirect doc already
        carries its rewritten from/to, exactly one of which is the primary.
        """
        primary_id = str(primary_doc["id"])
        parts = [
            "MATCH (p:_Entity {id: $primaryId}), (s:_Entity {id: $secondaryId}) ",
            "CREATE (:_EntityVersion {entity_id: $primaryId, _doc: p._doc, "
            "tx_from: p.tx_from, tx_to: $now}) ",
            "CREATE (:_EntityVersion {entity_id: $secondaryId, _doc: s._doc, "
            "tx_from: s.tx_from, tx_to: $now}) ",
            "SET p._doc = $primaryDoc, p.tx_from = $now, ",
            "s._doc = $secondaryDoc, s.tx_from = $now, ",
            f"{_index_assignments('p', 'pIx')}, {_index_assignments('s', 'sIx')}",
        ]
        params: dict[str, Any] = {
            "primaryId": primary_id,
            "secondaryId": secondary_doc["id"],
            "primaryDoc": json.dumps(dict(primary_doc)),
            "secondaryDoc": json.dumps(dict(secondary_doc)),
            "now": now,
            **_index_params(primary_doc, "pIx"),
            **_index_params(secondary_doc, "sIx"),
        }
        for i, doc in enumerate(redirects):
            rid, rdoc, other = f"r{i}Id", f"r{i}Doc", f"r{i}Other"
            # A redirect rewrites the edge's endpoint — bi-temporally an
            # update like any other, so snapshot the pre-merge incarnation as
            # a closed ``:_RelationVersion`` (falling back to the doc's
            # ``created_at`` for a never-updated edge) and reopen the live
            # interval at the merge instant, exactly as ``update_relation``
            # does. Without this the recreated edge re-covers the past and
            # shadows the version nodes in as-of reads.
            snapshot = (
                f"CREATE (:_RelationVersion {{relation_id: ${rid}, _doc: r{i}._doc, "
                f"tx_from: coalesce(r{i}.tx_from, $r{i}Tf), tx_to: $now}}) "
            )
            edge = f"[:{doc['relationType']} {{id: ${rid}, _doc: ${rdoc}, tx_from: $now}}]"
            if doc["from"] == primary_id:  # was outgoing from the secondary
                params[other] = doc["to"]
                parts.append(
                    f" WITH p, s MATCH (s)-[r{i}]->(o{i}:_Entity {{id: ${other}}}) "
                    f"WHERE r{i}.id = ${rid} {snapshot}DELETE r{i} CREATE (p)-{edge}->(o{i})"
                )
            else:  # was incoming to the secondary
                params[other] = doc["from"]
                parts.append(
                    f" WITH p, s MATCH (o{i}:_Entity {{id: ${other}}})-[r{i}]->(s) "
                    f"WHERE r{i}.id = ${rid} {snapshot}DELETE r{i} CREATE (o{i})-{edge}->(p)"
                )
            params[rid] = doc["id"]
            params[rdoc] = json.dumps(dict(doc))
            params[f"r{i}Tf"] = str(doc.get("created_at") or now)
        if supersedes_doc is not None:
            parts.append(
                " WITH p, s CREATE (p)-[:supersedes {id: $supersedesId, _doc: $supersedesDoc}]->(s)"
            )
            params["supersedesId"] = supersedes_doc["id"]
            params["supersedesDoc"] = json.dumps(dict(supersedes_doc))
        events: list[tuple[str, dict[str, Any]]] = [
            (
                "entities_merged",
                {
                    "primary": dict(primary_doc),
                    "secondary": dict(secondary_doc),
                    "previousPrimary": dict(previous_primary),
                    "previousSecondary": dict(previous_secondary),
                    "redirectedRelations": [dict(doc) for doc in redirects],
                    "supersedesRelation": dict(supersedes_doc) if supersedes_doc else None,
                },
            )
        ]
        if supersedes_doc is not None:
            # A real, separately event-logged creation -- not just a field
            # on entities_merged's own payload -- so what-changed's/diff-
            # worlds' ordinary relation_created differ sees it (a relation
            # id with no dedicated event is invisible to both, not merely
            # eventId: null).
            events.append(("relation_created", {"relation": dict(supersedes_doc)}))
        self._commit(("".join(parts), params), events)

    def list_entity_docs(self, filter: EntityFilter | None = None) -> list[dict[str, Any]]:
        """Verbatim wire docs, same filtering/order as list_entities."""
        return self._entity_page(filter)[1]

    def list_entities(self, filter: EntityFilter | None = None) -> list[Entity]:
        return self._entity_page(filter)[0]

    def list_entities_page(self, filter: EntityFilter | None = None) -> tuple[list[Entity], int]:
        """``(entities, total)`` — the entities honour ``filter.limit``, the
        total counts every match had the limit not been applied."""
        entities, _, total = self._entity_page(filter)
        return entities, total

    def _entity_page(
        self, filter: EntityFilter | None
    ) -> tuple[list[Entity], list[dict[str, Any]], int]:
        """The one entity read path: Cypher prefilter → Python confirmation
        (filters.py stays the semantics oracle) → limit.

        ``limit`` and the total are computed server-side only when the
        prefilter alone decides membership (see ``_pushdown_is_exact``);
        otherwise the candidate window is read whole and sliced after the
        Python pass, so semantics never depend on which path ran.
        """
        where, params = _entity_prefilter(filter)
        cypher = f"MATCH (n:_Entity) WHERE {where} RETURN n._doc, n._status ORDER BY id(n)"
        limit = filter.limit if filter is not None else None
        server_limit = limit if limit is not None and _pushdown_is_exact(filter) else None

        if server_limit is not None and self._has_unindexed_entity():
            # The NULL check below only sees the limited window, but the
            # server-side count spans the whole graph — where an unmigrated
            # node passes every ``(prop IS NULL OR ...)`` predicate regardless
            # of its real status/type/name. Migrate before counting, not after.
            self._migrate_entity_index()

        rows = self._rows_paged(cypher, params, server_limit)
        if any(row[1] is None for row in rows):
            # A graph written before the read index existed. Migrate it in
            # place, then re-read: the window above was a superset (and, under
            # a server-side limit, possibly the wrong superset).
            self._migrate_entity_index()
            rows = self._rows_paged(cypher, params, server_limit)

        docs: list[dict[str, Any]] = [json.loads(row[0]) for row in rows]
        entities = [Entity.model_validate(doc) for doc in docs]
        entities = self._confirm_entity_filters(entities, filter)
        total = len(entities) if server_limit is None else self._count_entities(where, params)
        if limit is not None:
            entities = entities[:limit]
        by_id = {doc["id"]: doc for doc in docs}
        return entities, [by_id[e.id] for e in entities], total

    def _confirm_entity_filters(
        self, entities: list[Entity], filter: EntityFilter | None
    ) -> list[Entity]:
        """Exact filter semantics over the candidate set."""
        entities = apply_entity_filters(entities, filter)
        if filter is None:
            return entities
        included = self._sources_of(filter.sourced_from)
        excluded = self._sources_of(filter.exclude_sourced_from)
        if included is not None:
            entities = [e for e in entities if e.id in included]
        if excluded is not None:
            entities = [e for e in entities if e.id not in excluded]
        return entities

    def _count_entities(self, where: str, params: dict[str, Any]) -> int:
        rows = self._rows(f"MATCH (n:_Entity) WHERE {where} RETURN count(n)", params)
        return int(rows[0][0]) if rows else 0

    def _has_unindexed_entity(self) -> bool:
        """Does any entity predate the derived read index? Stops at the first
        hit, and is only asked on the server-limited path — where the window
        that would otherwise reveal one is capped."""
        return bool(self._rows("MATCH (n:_Entity) WHERE n._status IS NULL RETURN n.id LIMIT 1"))

    def _migrate_entity_index(self) -> None:
        """Backfill the derived read-index properties for every entity that
        predates them. Batched (each batch removes itself from the predicate),
        derived-only, so no event is appended — this changes no domain state."""
        seen: set[str] = set()
        while True:
            rows = self._rows(
                "MATCH (n:_Entity) WHERE n._status IS NULL RETURN n._doc LIMIT $batch",
                {"batch": PAGE_SIZE},
            )
            docs = [json.loads(row[0]) for row in rows]
            fresh = [doc for doc in docs if doc["id"] not in seen]
            if not fresh:
                return
            seen.update(doc["id"] for doc in fresh)
            self._query(
                "UNWIND $rows AS row MATCH (n:_Entity {id: row.id}) "
                "SET n._status = row.status, n._type = row.type, "
                "n._name = row.name, n._search = row.search",
                {"rows": [{"id": doc["id"], **_index_props(doc)} for doc in fresh]},
            )

    def list_relation_docs(self, filter: RelationFilter | None = None) -> list[dict[str, Any]]:
        """Verbatim relation wire docs, same filtering/order as list_relations."""
        return self._relation_page(filter)[1]

    def _sources_of(self, target_ids: list[str] | None) -> set[str] | None:
        """Ids of entities holding a 'sources' relation TO any of the targets."""
        if not target_ids:
            return None
        rows = self._rows_paged(
            "MATCH (s:_Entity)-[:sources]->(t:_Entity) WHERE t.id IN $ids "
            "RETURN DISTINCT s.id ORDER BY s.id",
            {"ids": list(target_ids)},
        )
        return {row[0] for row in rows}

    # -- relations ----------------------------------------------------------------

    def create_relation(self, spec: RelationCreate) -> Relation:
        return self.create_relations([spec])[0]

    def create_relations(self, specs: Sequence[RelationCreate]) -> list[Relation]:
        """Create a batch of edges, all of them or none of them.

        A batch spans one query per relation type (edge types cannot be
        parametrized), and MULTI does not roll back — so "none of them" is
        bought twice over: every endpoint is checked before anything is
        committed, and if the reply still reports fewer edges than asked for
        (an endpoint retracted by a concurrent writer between the check and the
        EXEC) the edges that did land are deleted again before the error
        propagates. Half a batch is never a resting state.
        """
        docs: list[dict[str, Any]] = []
        now = iso_now()
        for spec in specs:
            doc = spec.model_dump(by_alias=True, exclude_unset=True)
            doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
            docs.append(doc)

        self._require_endpoints(docs)

        # One UNWIND query per relation type (edge types cannot be parametrized)
        # — a 10k-item batch is ≤15 queries + one pipelined event append.
        by_type: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            by_type.setdefault(doc["relationType"], []).append(doc)
        steps: list[tuple[str, dict[str, Any]]] = []
        for relation_type, type_docs in by_type.items():
            rows = [
                {"from": d["from"], "to": d["to"], "id": d["id"], "doc": json.dumps(d)}
                for d in type_docs
            ]
            steps.append(
                (
                    "UNWIND $rows AS row "
                    "MATCH (a:_Entity {id: row.from}), (b:_Entity {id: row.to}) "
                    f"CREATE (a)-[:{relation_type} {{id: row.id, _doc: row.doc}}]->(b)",
                    {"rows": rows},
                )
            )
        results, event_ids = self._commit_steps(
            steps, [("relation_created", {"relation": doc}) for doc in docs]
        )
        created = sum(int(result.relationships_created) for result in results)
        if created != len(docs):
            # Lost a race with a concurrent delete: the endpoints were there
            # when checked and gone by EXEC. Undo both halves — the edges that
            # did land and the events for the whole batch — so the caller's
            # NOT_FOUND means what it says.
            self._delete_relations_by_id([doc["id"] for doc in docs])
            self._events.discard(event_ids)
            raise NotFoundError(
                f"Entity not found: relation endpoints must exist (created {created}/{len(docs)})"
            )
        return [Relation.model_validate(doc) for doc in docs]

    def _require_endpoints(self, docs: Sequence[Mapping[str, Any]]) -> None:
        """Raise NOT_FOUND unless every endpoint of a batch exists.

        One query for the whole batch, ahead of the commit: a missing endpoint
        makes its ``CREATE`` row silently produce nothing, which the reply only
        reports as a count. Pre-empting beats compensating.
        """
        wanted = {str(doc["from"]) for doc in docs} | {str(doc["to"]) for doc in docs}
        if not wanted:
            return
        rows = self._rows_paged(
            "MATCH (n:_Entity) WHERE n.id IN $ids RETURN n.id ORDER BY id(n)",
            {"ids": sorted(wanted)},
        )
        missing = sorted(wanted - {row[0] for row in rows})
        if missing:
            raise NotFoundError(
                f"Entity not found: relation endpoints must exist (missing {', '.join(missing)})"
            )

    def _delete_relations_by_id(self, relation_ids: Sequence[str]) -> None:
        """Erase edges by relation id — compensation only, never a domain
        delete (those invalidate; see ``invalidate_relation``)."""
        self._query(
            "UNWIND $ids AS rid MATCH ()-[r]->() WHERE r.id = rid DELETE r",
            {"ids": list(relation_ids)},
        )

    def import_relation_doc(self, doc: Mapping[str, Any]) -> None:
        """Write a pre-existing relation doc verbatim (migration path; no event)."""
        self._query(
            "MATCH (a:_Entity {id: $from}), (b:_Entity {id: $to}) "
            f"CREATE (a)-[:{doc['relationType']} {{id: $id, _doc: $doc}}]->(b)",
            {
                "from": doc["from"],
                "to": doc["to"],
                "id": doc["id"],
                "doc": json.dumps(doc),
            },
        )

    def adopt_relation(self, doc: Mapping[str, Any]) -> Relation:
        """Materialize a verbatim copy of a relation doc read from elsewhere
        into *this* graph — the relation twin of ``adopt_entity`` (see its
        docstring for the full rationale). Both endpoints must already exist
        in *this* graph (``WorldGraphStore`` adopts them first); no event,
        for the same reason ``adopt_entity`` has none — the next real
        mutation on this edge (``update_relation``/``delete_relation``)
        carries it."""
        doc = dict(doc)
        self._commit(
            (
                "MATCH (a:_Entity {id: $from}), (b:_Entity {id: $to}) "
                f"CREATE (a)-[:{doc['relationType']} {{id: $id, _doc: $doc}}]->(b)",
                {
                    "from": doc["from"],
                    "to": doc["to"],
                    "id": doc["id"],
                    "doc": json.dumps(doc),
                },
            ),
            [],
        )
        return Relation.model_validate(doc)

    def graft_relation(self, doc: Mapping[str, Any]) -> Relation:
        """Create a verbatim copy of a relation doc — id preserved — as a
        REAL, event-logged creation: ``merge-world``'s counterpart to
        ``adopt_relation`` (see ``graft_entity`` for the full rationale).
        Both endpoints must already exist in this graph."""
        doc = dict(doc)
        self._commit(
            (
                "MATCH (a:_Entity {id: $from}), (b:_Entity {id: $to}) "
                f"CREATE (a)-[:{doc['relationType']} {{id: $id, _doc: $doc}}]->(b)",
                {
                    "from": doc["from"],
                    "to": doc["to"],
                    "id": doc["id"],
                    "doc": json.dumps(doc),
                },
            ),
            [("relation_created", {"relation": doc})],
        )
        return Relation.model_validate(doc)

    def relation_ids_known_live(self) -> set[str]:
        """Every relation id this graph currently has an opinion about —
        live, closed (a ``:_RelationVersion`` snapshot), or hard-deleted
        with a tombstone (``:_RelationTombstone`` — see
        ``theloom.store.worlds.WorldGraphStore.delete_relation``) —
        regardless of when. Used by ``WorldGraphStore``'s overlay merge to
        tell "this layer deleted an inherited relation" (shadow deeper
        layers) apart from "this layer never heard of it" (fall through to
        them), since ``list_relations`` alone cannot distinguish the two: a
        closed or hard-deleted relation is simply absent from it either
        way."""
        live_rows = self._rows_paged("MATCH ()-[r]->() RETURN r.id")
        closed_rows = self._rows_paged("MATCH (v:_RelationVersion) RETURN DISTINCT v.relation_id")
        tombstoned_rows = self._rows_paged("MATCH (t:_RelationTombstone) RETURN t.id")
        return (
            {row[0] for row in live_rows}
            | {row[0] for row in closed_rows}
            | {row[0] for row in tombstoned_rows}
        )

    def relation_ids_known_as_of(self, timestamp: str) -> set[str]:
        """``relation_ids_known_live``, bounded to what this graph knew *as
        of* ``timestamp`` — the historical-layer form the overlay merge uses
        once an ancestor's own state has been clamped to a fork point (a
        later closure by that ancestor must not shadow a child that forked
        away before it happened).

        Built on ``_relations_as_of`` (the private half of
        ``read_graph_as_of``) rather than ``read_graph_as_of`` itself: this
        method is called as ``super().relation_ids_known_as_of(...)`` from
        ``WorldGraphStore.read_graph_as_of``, and ``self.read_graph_as_of``
        would resolve back to that same override through ordinary
        (non-``super``) polymorphism — infinite recursion. ``_relations_as_of``
        is never overridden, so it always reads *this* graph's own rows, plain
        or world-local alike, with no entity-presence filtering to complicate
        that (this method only needs relation ids, not a consistent snapshot).
        """
        open_ids = {relation.id for relation in self._relations_as_of(timestamp)}
        closed_rows = self._rows_paged(
            "MATCH (v:_RelationVersion) WHERE v.tx_to <= $t RETURN DISTINCT v.relation_id",
            {"t": timestamp},
        )
        # Tombstones aren't time-bounded (a hard delete destroys history —
        # see FalkorGraphStore's module docstring — so there is no earlier
        # incarnation to resurrect for an as-of read either): once written,
        # a tombstoned id is dead for every timestamp this method is asked
        # about, matching a hard delete's own "absent from every snapshot"
        # doctrine for a plain (non-world) graph.
        tombstoned_rows = self._rows_paged("MATCH (t:_RelationTombstone) RETURN t.id")
        return open_ids | {row[0] for row in closed_rows} | {row[0] for row in tombstoned_rows}

    def entity_tombstoned(self, entity_id: str) -> bool:
        """Whether ``entity_id`` was hard-deleted with a tombstone left
        behind (``theloom.store.worlds.WorldGraphStore.delete_entity``'s
        ``hard=True`` path). Only ever true in a belief world's own local
        segment — a plain graph never writes one — checked by the overlay's
        every entity read so a hard-deleted id is never resurrected by
        falling through to an ancestor that still has it: the live node and
        any ``:_EntityVersion`` history are both gone by design (a hard
        delete destroys history), so nothing short of an explicit marker
        can tell "erased here" apart from "never touched here"."""
        return bool(
            self._rows("MATCH (t:_EntityTombstone {id: $id}) RETURN t LIMIT 1", {"id": entity_id})
        )

    def entity_ids_tombstoned(self) -> set[str]:
        """Every entity id hard-deleted (with a tombstone) in this graph —
        the bulk form of ``entity_tombstoned``, for listing/snapshot reads
        that need the whole set rather than one id at a time."""
        rows = self._rows_paged("MATCH (t:_EntityTombstone) RETURN t.id")
        return {row[0] for row in rows}

    def replay_creation_events(
        self,
        entity_docs: Sequence[Mapping[str, Any]],
        relation_docs: Sequence[Mapping[str, Any]],
    ) -> int:
        """Append creation events for imported docs (event-replay
        migration): the graph's history starts clean, with every migrated
        entity/relation recorded as a creation. Payloads carry the docs verbatim
        (original ids/timestamps preserved); entities replay before relations.
        Returns the number of events appended."""
        events: list[tuple[str, dict[str, Any]]] = [
            ("entity_created", {"entity": dict(doc)}) for doc in entity_docs
        ]
        events += [("relation_created", {"relation": dict(doc)}) for doc in relation_docs]
        event_ids = self._events.append_many(events)
        # Not routed through commit_steps (this is the bulk-import replay
        # path, documented above as writing docs verbatim/unlogged and then
        # appending their creation events as one separate batch) — so it
        # records its own receipt rather than inheriting commit_steps'.
        receipts.record(event_ids)
        return len(events)

    def _edge_rows(
        self, from_id: str, to_id: str, relation_type: str | None, relation_id: str | None = None
    ) -> list[tuple[int, dict[str, Any]]]:
        """(internal edge id, doc) for directed edges from→to, insertion order.

        ``relation_id`` narrows to one specific edge — the only way to address
        a parallel edge that shares its type with a sibling."""
        edge_type = f":{relation_type}" if relation_type else ""
        id_clause = " WHERE r.id = $rid" if relation_id is not None else ""
        params: dict[str, Any] = {"from": from_id, "to": to_id}
        if relation_id is not None:
            params["rid"] = relation_id
        rows = self._rows(
            f"MATCH (a:_Entity {{id: $from}})-[r{edge_type}]->(b:_Entity {{id: $to}})"
            f"{id_clause} RETURN id(r), r._doc ORDER BY id(r)",
            params,
        )
        return [(int(row[0]), json.loads(row[1])) for row in rows]

    def _target_edge(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None,
        relation_id: str | None,
    ) -> tuple[int, dict[str, Any]]:
        """The single edge a write addresses. Parallel typed edges are
        first-class, so ``relation_id`` selects exactly one; without it the
        oldest match wins (the historical behaviour)."""
        edges = self._edge_rows(from_id, to_id, relation_type, relation_id)
        if not edges:
            raise NotFoundError("Relation not found")
        return edges[0]

    def read_relation(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> Relation | None:
        edges = self._edge_rows(from_id, to_id, relation_type)
        return Relation.model_validate(edges[0][1]) if edges else None

    def read_relations(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> list[Relation]:
        return [
            Relation.model_validate(doc)
            for _, doc in self._edge_rows(from_id, to_id, relation_type)
        ]

    def update_relation(
        self,
        from_id: str,
        to_id: str,
        updates: Mapping[str, Any],
        relation_type: str | None = None,
        relation_id: str | None = None,
    ) -> Relation:
        edge_id, current = self._target_edge(from_id, to_id, relation_type, relation_id)
        now = iso_now()
        merged = {**current, **dict(updates), "updated_at": now}
        for field in _IMMUTABLE_RELATION_FIELDS:
            merged[field] = current[field]
        relation = Relation.model_validate(merged)
        # The invalidate-never-overwrite step, mirroring an entity's
        # ``_swap_doc_step``: snapshot the current incarnation as a closed
        # ``:_RelationVersion``, then swap in the new doc with the live
        # interval reopened at now (``r.tx_from``). An edge that has never
        # been updated carries no ``tx_from`` property — its interval has been
        # open since the doc's own ``created_at``, so the snapshot falls back
        # to that.
        snapshot_clause = (
            "CREATE (:_RelationVersion {relation_id: $eid, _doc: r._doc, "
            "tx_from: coalesce(r.tx_from, $txFrom), tx_to: $now}) "
        )
        params: dict[str, Any] = {
            "rid": edge_id,
            "eid": merged["id"],
            "doc": json.dumps(merged),
            "txFrom": current.get("created_at", now),
            "now": now,
        }
        retyped = merged["relationType"] != current["relationType"]
        redirected = merged["from"] != current["from"] or merged["to"] != current["to"]
        if retyped or redirected:
            # relationType and endpoints are both updatable fields, but
            # Cypher has no SET for either (a relationship's type and its
            # start/end nodes are structural, fixed at creation) -- so
            # either kind of change is applied the same way: delete +
            # recreate, same id/doc, snapshotted first exactly like the
            # SET branch below. A WITH is required between the DELETE and
            # the second MATCH (FalkorDB refuses to introduce a MATCH
            # after an updating clause without one); harmless when only
            # the type changed, since $newFrom/$newTo then equal the
            # current endpoints and the second MATCH just re-finds the
            # same two nodes DELETE didn't touch.
            step = (
                "MATCH ()-[r]->() WHERE id(r) = $rid "
                f"{snapshot_clause}DELETE r "
                "WITH 1 AS _skip "
                "MATCH (a:_Entity {id: $newFrom}), (b:_Entity {id: $newTo}) "
                f"CREATE (a)-[:{merged['relationType']} "
                "{id: $eid, _doc: $doc, tx_from: $now}]->(b)",
                {**params, "newFrom": merged["from"], "newTo": merged["to"]},
            )
        else:
            step = (
                f"MATCH ()-[r]->() WHERE id(r) = $rid {snapshot_clause}"
                "SET r._doc = $doc, r.tx_from = $now",
                params,
            )
        self._commit(step, [("relation_updated", {"relation": merged, "previous": current})])
        return relation

    def invalidate_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None = None,
        relation_id: str | None = None,
    ) -> Relation:
        """Retire an edge bi-temporally: it leaves the live projection and its
        final doc is snapshotted as ``:_RelationVersion`` with ``tx_to`` set.

        The counterpart of an entity's ``superseded`` status. A relation has no
        status field to flip, so its retirement is a closed system-time
        interval instead: ``tx_from`` is the live incarnation's own ``tx_from``
        (the doc's ``created_at`` for an edge never updated) and ``tx_to`` is
        now. History is preserved — the edge is never erased — while every
        read path (which matches live edges) stops seeing it.

        Raises NotFoundError when no such edge exists.
        """
        edge_id, doc = self._target_edge(from_id, to_id, relation_type, relation_id)
        now = iso_now()
        self._commit(
            (
                "MATCH ()-[r]->() WHERE id(r) = $rid "
                "CREATE (:_RelationVersion {relation_id: $eid, _doc: r._doc, "
                "tx_from: coalesce(r.tx_from, $txFrom), tx_to: $now}) "
                "DELETE r",
                {
                    "rid": edge_id,
                    "eid": doc["id"],
                    "txFrom": doc.get("created_at", now),
                    "now": now,
                },
            ),
            [("relation_invalidated", {"relation": doc, "tx_to": now})],
        )
        return Relation.model_validate(doc)

    def delete_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None = None,
        relation_id: str | None = None,
        hard: bool = False,
    ) -> None:
        """Retire the targeted edge, or erase it outright with ``hard=True``.

        The default is ``invalidate_relation`` — deleting an edge in an
        event-sourced store means closing its system-time interval, not
        dropping the only record that it ever existed. ``hard=True`` really
        removes it, taking its history with it."""
        if not hard:
            self.invalidate_relation(from_id, to_id, relation_type, relation_id)
            return
        edge_id, doc = self._target_edge(from_id, to_id, relation_type, relation_id)
        self._commit(
            ("MATCH ()-[r]->() WHERE id(r) = $rid DELETE r", {"rid": edge_id}),
            [("relation_deleted", {"relation": doc})],
        )

    def list_relations(self, filter: RelationFilter | None = None) -> list[Relation]:
        return self._relation_page(filter)[0]

    def _relation_page(
        self, filter: RelationFilter | None
    ) -> tuple[list[Relation], list[dict[str, Any]]]:
        """The one relation read path. from/to/relationType are structural —
        they push into the MATCH pattern exactly (endpoint ids, edge label) —
        and polarity/session are confirmed in Python by filters.py."""
        edge_type = ""
        from_pattern, to_pattern = "(:_Entity)", "(:_Entity)"
        params: dict[str, Any] = {}
        if filter is not None:
            if filter.relation_type is not None:
                edge_type = f":{filter.relation_type.value}"
            if filter.from_ is not None:
                from_pattern = "(a:_Entity {id: $fFrom})"
                params["fFrom"] = filter.from_
            if filter.to is not None:
                to_pattern = "(b:_Entity {id: $fTo})"
                params["fTo"] = filter.to
        rows = self._rows_paged(
            f"MATCH {from_pattern}-[r{edge_type}]->{to_pattern} RETURN r._doc ORDER BY id(r)",
            params,
        )
        docs: list[dict[str, Any]] = [json.loads(row[0]) for row in rows]
        relations = apply_relation_filters([Relation.model_validate(doc) for doc in docs], filter)
        by_id = {doc["id"]: doc for doc in docs}
        return relations, [by_id[r.id] for r in relations]

    def get_relations(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Relation]:
        edge_type = f":{relation_type}" if relation_type else ""
        if direction == "outgoing":
            patterns = [f"(n:_Entity {{id: $id}})-[r{edge_type}]->(:_Entity)"]
        elif direction == "incoming":
            patterns = [f"(:_Entity)-[r{edge_type}]->(n:_Entity {{id: $id}})"]
        else:
            # 'both' returns incoming then outgoing, each in insertion order.
            patterns = [
                f"(:_Entity)-[r{edge_type}]->(n:_Entity {{id: $id}})",
                f"(n:_Entity {{id: $id}})-[r{edge_type}]->(:_Entity)",
            ]
        relations: list[Relation] = []
        for pattern in patterns:
            rows = self._rows_paged(
                f"MATCH {pattern} RETURN r._doc ORDER BY id(r)", {"id": entity_id}
            )
            relations.extend(Relation.model_validate(json.loads(row[0])) for row in rows)
        return relations

    def get_neighbors(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Entity]:
        relations = self.get_relations(entity_id, direction, relation_type)
        neighbors: list[Entity] = []
        for neighbor_id in extract_neighbor_ids(entity_id, relations, direction):
            entity = self.read_entity(neighbor_id)
            if entity is not None:
                neighbors.append(entity)
        return neighbors

    # -- stats + metadata ----------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        entity_distribution = {t.value: 0 for t in ALL_ENTITY_TYPES}
        relation_distribution = {t.value: 0 for t in ALL_RELATION_TYPES}
        entity_rows = self._rows_paged("MATCH (n:_Entity) RETURN n._doc ORDER BY id(n)")
        for row in entity_rows:
            entity_distribution[json.loads(row[0])["entityType"]] += 1
        # Relations aggregate server-side (edge label == relationType): a
        # handful of rows regardless of graph size, so no doc shipping and
        # no RESULTSET_SIZE exposure.
        for relation_type, count in self._rows(
            "MATCH (:_Entity)-[r]->(:_Entity) RETURN type(r), count(r)"
        ):
            relation_distribution[relation_type] += int(count)
        return {
            "entityCount": len(entity_rows),
            "relationCount": sum(relation_distribution.values()),
            "entityTypeDistribution": entity_distribution,
            "relationTypeDistribution": relation_distribution,
        }

    def _metadata_doc(self) -> dict[str, Any]:
        rows = self._rows("MATCH (m:_GraphMeta) RETURN m._doc")
        if not rows:
            return {}
        loaded: dict[str, Any] = json.loads(rows[0][0])
        return loaded

    def get_metadata(self, key: str) -> Any | None:
        return self._metadata_doc().get(key)

    def set_metadata(self, key: str, value: Any) -> None:
        doc = self._metadata_doc()
        doc[key] = value
        self._query("MERGE (m:_GraphMeta) SET m._doc = $doc", {"doc": json.dumps(doc)})

    def set_metadata_doc(self, doc: Mapping[str, Any]) -> None:
        """Replace the whole metadata document (migration path)."""
        if doc:
            self._query("MERGE (m:_GraphMeta) SET m._doc = $doc", {"doc": json.dumps(dict(doc))})

    def delete_graph_data(self) -> None:
        """Drop the underlying FalkorDB graph and the event stream."""
        with contextlib.suppress(Exception):  # a never-written graph has no key
            self._graph.delete()
        self._events.delete()
