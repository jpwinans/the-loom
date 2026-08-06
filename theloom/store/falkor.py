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
_IMMUTABLE_RELATION_FIELDS = ("id", "from", "to", "created_at")

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
            edge = f"[:{doc['relationType']} {{id: ${rid}, _doc: ${rdoc}}}]"
            if doc["from"] == primary_id:  # was outgoing from the secondary
                params[other] = doc["to"]
                parts.append(
                    f" WITH p, s MATCH (s)-[r{i}]->(o{i}:_Entity {{id: ${other}}}) "
                    f"WHERE r{i}.id = ${rid} DELETE r{i} CREATE (p)-{edge}->(o{i})"
                )
            else:  # was incoming to the secondary
                params[other] = doc["from"]
                parts.append(
                    f" WITH p, s MATCH (o{i}:_Entity {{id: ${other}}})-[r{i}]->(s) "
                    f"WHERE r{i}.id = ${rid} DELETE r{i} CREATE (o{i})-{edge}->(p)"
                )
            params[rid] = doc["id"]
            params[rdoc] = json.dumps(dict(doc))
        if supersedes_doc is not None:
            parts.append(
                " WITH p, s CREATE (p)-[:supersedes {id: $supersedesId, _doc: $supersedesDoc}]->(s)"
            )
            params["supersedesId"] = supersedes_doc["id"]
            params["supersedesDoc"] = json.dumps(dict(supersedes_doc))
        self._commit(
            ("".join(parts), params),
            [
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
            ],
        )

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
        self._events.append_many(events)
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
        if merged["relationType"] != current["relationType"]:
            # relationType is an updatable field; the edge is
            # retyped structurally (delete + recreate, same id/doc) so Cypher
            # type-filtered traversals stay consistent with the doc.
            step = (
                "MATCH (a:_Entity {id: $from})-[r]->(b:_Entity {id: $to}) "
                f"WHERE id(r) = $rid {snapshot_clause}DELETE r "
                f"CREATE (a)-[:{merged['relationType']} "
                "{id: $eid, _doc: $doc, tx_from: $now}]->(b)",
                {**params, "from": from_id, "to": to_id},
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
