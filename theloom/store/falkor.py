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
- Metadata = singleton node ``:_GraphMeta {_doc}``.

Every mutation is a single atomic Cypher query, followed by an event append
to the graph's Redis stream (see events.py for the ordering guarantee).
Status changes are validated against the lifecycle transition table here in
the store.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from falkordb import FalkorDB
from redis import Redis

from theloom.errors import NotFoundError, ValidationError
from theloom.model import (
    ALL_ENTITY_TYPES,
    ALL_RELATION_TYPES,
    Entity,
    EntityCreate,
    EntityFilter,
    Relation,
    RelationCreate,
    RelationFilter,
    is_valid_transition,
)
from theloom.store.base import Direction, GraphStore
from theloom.store.events import EventLog
from theloom.store.filters import (
    apply_entity_filters,
    apply_relation_filters,
    extract_neighbor_ids,
)
from theloom.store.paging import PAGE_SIZE, fetch_all_rows
from theloom.timeutil import iso_now

_IMMUTABLE_ENTITY_FIELDS = ("id", "created_at")
_IMMUTABLE_RELATION_FIELDS = ("id", "from", "to", "created_at")

# The derived read-index properties, in wire-doc projection order. Named
# without the leading underscore here; the node property is "_" + field.
_INDEX_FIELDS = ("status", "type", "name", "search")


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
        "search": "\n".join([name, *(str(obs) for obs in observations)]).lower(),
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
    """True when the Cypher prefilter alone decides membership — i.e. no
    filter field is left for the Python pass. Only then may LIMIT/count run
    server-side."""
    if filter is None:
        return True
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


class FalkorGraphStore(GraphStore):
    """GraphStore over one FalkorDB graph + one event stream."""

    def __init__(
        self, db: FalkorDB, redis: Redis, graph_name: str, key_prefix: str = "loom"
    ) -> None:
        self._graph = db.select_graph(f"{key_prefix}:graph:{graph_name}")
        self._events = EventLog(redis, graph_name, key_prefix)

    # -- query helpers ----------------------------------------------------------

    def _query(self, cypher: str, params: dict[str, Any] | None = None) -> Any:
        return self._graph.query(cypher, params or {})

    def _rows(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        result = self._query(cypher, params)
        rows: list[list[Any]] = result.result_set or []
        return rows

    def _rows_paged(
        self, cypher: str, params: dict[str, Any] | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        """All rows of an ORDER BY-carrying query, immune to RESULTSET_SIZE.
        ``limit`` caps the window server-side (the paging loop stops there)."""
        return fetch_all_rows(self._rows, cypher, params, limit)

    # -- entities ----------------------------------------------------------------

    def create_entity(self, spec: EntityCreate) -> Entity:
        now = iso_now()
        doc = spec.model_dump(by_alias=True, exclude_unset=True)
        doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
        entity = Entity.model_validate(doc)
        self._query(
            f"CREATE (n:_Entity {{id: $id, _doc: $doc, tx_from: $now, {_index_literal('ix')}}})",
            {"id": doc["id"], "doc": json.dumps(doc), "now": now, **_index_params(doc, "ix")},
        )
        self._events.append("entity_created", {"entity": doc})
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

    def ensure_vector_index(self, dimension: int = 768) -> None:
        """Create the entity vector index (idempotent)."""
        with contextlib.suppress(Exception):  # already exists
            self._query(
                "CREATE VECTOR INDEX FOR (e:_Entity) ON (e._embedding) "
                f"OPTIONS {{dimension: {dimension}, similarityFunction: 'cosine'}}"
            )

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

    def vector_knn(self, query_vector: list[float], k: int) -> list[tuple[str, float]]:
        """(entity id, cosine similarity) for the k nearest embedded entities.
        FalkorDB returns cosine *distance*; similarity = 1 - distance.

        The index dimension follows the query vector's own length (real
        embeddings are always 768-dim; tests may seed lower-dimensional
        synthetic vectors), so ``ensure_vector_index`` is idempotent whether
        this is the first vector op on the graph or a later one."""
        self.ensure_vector_index(dimension=len(query_vector))
        rows = self._rows(
            "CALL db.idx.vector.queryNodes('_Entity', '_embedding', $k, vecf32($q)) "
            "YIELD node, score RETURN node.id, score",
            {"k": k, "q": query_vector},
        )
        return [(row[0], 1.0 - float(row[1])) for row in rows]

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
        self._query(
            "MATCH (n:_Entity {id: $id}) "
            "CREATE (:_EntityVersion {entity_id: $id, _doc: n._doc, "
            "tx_from: n.tx_from, tx_to: $now}) "
            f"SET n._doc = $doc, n.tx_from = $now, {_index_assignments('n', 'ix')}",
            {
                "id": entity_id,
                "doc": json.dumps(merged),
                "now": now,
                **_index_params(merged, "ix"),
            },
        )
        event_type = "entity_status_changed" if status_changed else "entity_updated"
        self._events.append(event_type, {"entity": merged, "previous": current})
        return entity

    def delete_entity(self, entity_id: str) -> Entity:
        doc = self._read_doc(entity_id)
        if doc is None:
            raise NotFoundError("Entity not found")
        self._query("MATCH (n:_Entity {id: $id}) DETACH DELETE n", {"id": entity_id})
        self._events.append("entity_deleted", {"entity": doc})
        return Entity.model_validate(doc)

    def read_entity_doc(self, entity_id: str) -> dict[str, Any] | None:
        """The verbatim wire doc — key order preserved (synthesis `raw` output
        serializes docs into text, where JS object key order is contract)."""
        return self._read_doc(entity_id)

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
        self._query("".join(parts), params)
        self._events.append(
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

        ``limit`` and the total are computed server-side whenever the prefilter
        alone decides membership; when a filter field has no server-side
        counterpart (version/session/sourcedFrom) the candidate window is read
        whole and sliced after the Python pass, so semantics never depend on
        which path ran.
        """
        where, params = _entity_prefilter(filter)
        cypher = f"MATCH (n:_Entity) WHERE {where} RETURN n._doc, n._status ORDER BY id(n)"
        limit = filter.limit if filter is not None else None
        server_limit = limit if limit is not None and _pushdown_is_exact(filter) else None

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
        docs: list[dict[str, Any]] = []
        now = iso_now()
        for spec in specs:
            doc = spec.model_dump(by_alias=True, exclude_unset=True)
            doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
            docs.append(doc)

        # One transactional query for the whole batch, grouped by relation type
        # because edge types cannot be parametrized.
        # One UNWIND query per relation type (edge types cannot be parametrized)
        # — a 10k-item batch is ≤15 queries + one pipelined event append.
        by_type: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            by_type.setdefault(doc["relationType"], []).append(doc)
        created = 0
        for relation_type, type_docs in by_type.items():
            rows = [
                {"from": d["from"], "to": d["to"], "id": d["id"], "doc": json.dumps(d)}
                for d in type_docs
            ]
            result = self._query(
                "UNWIND $rows AS row "
                "MATCH (a:_Entity {id: row.from}), (b:_Entity {id: row.to}) "
                f"CREATE (a)-[:{relation_type} {{id: row.id, _doc: row.doc}}]->(b)",
                {"rows": rows},
            )
            created += int(result.relationships_created)
        if created != len(docs):
            raise NotFoundError(
                f"Entity not found: relation endpoints must exist (created {created}/{len(docs)})"
            )
        self._events.append_many([("relation_created", {"relation": doc}) for doc in docs])
        return [Relation.model_validate(doc) for doc in docs]

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
        self, from_id: str, to_id: str, relation_type: str | None
    ) -> list[tuple[int, dict[str, Any]]]:
        """(internal edge id, doc) for directed edges from→to, insertion order."""
        edge_type = f":{relation_type}" if relation_type else ""
        rows = self._rows(
            f"MATCH (a:_Entity {{id: $from}})-[r{edge_type}]->(b:_Entity {{id: $to}}) "
            "RETURN id(r), r._doc ORDER BY id(r)",
            {"from": from_id, "to": to_id},
        )
        return [(int(row[0]), json.loads(row[1])) for row in rows]

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
    ) -> Relation:
        edges = self._edge_rows(from_id, to_id, relation_type)
        if not edges:
            raise NotFoundError("Relation not found")
        edge_id, current = edges[0]
        merged = {**current, **dict(updates), "updated_at": iso_now()}
        for field in _IMMUTABLE_RELATION_FIELDS:
            merged[field] = current[field]
        relation = Relation.model_validate(merged)
        if merged["relationType"] != current["relationType"]:
            # relationType is an updatable field; the edge is
            # retyped structurally (delete + recreate, same id/doc) so Cypher
            # type-filtered traversals stay consistent with the doc.
            self._query(
                "MATCH (a:_Entity {id: $from})-[r]->(b:_Entity {id: $to}) "
                "WHERE id(r) = $rid DELETE r "
                f"CREATE (a)-[:{merged['relationType']} {{id: $eid, _doc: $doc}}]->(b)",
                {
                    "from": from_id,
                    "to": to_id,
                    "rid": edge_id,
                    "eid": merged["id"],
                    "doc": json.dumps(merged),
                },
            )
        else:
            self._query(
                "MATCH ()-[r]->() WHERE id(r) = $rid SET r._doc = $doc",
                {"rid": edge_id, "doc": json.dumps(merged)},
            )
        self._events.append("relation_updated", {"relation": merged, "previous": current})
        return relation

    def delete_relation(self, from_id: str, to_id: str, relation_type: str | None = None) -> None:
        edges = self._edge_rows(from_id, to_id, relation_type)
        if not edges:
            raise NotFoundError("Relation not found")
        edge_id, doc = edges[0]
        self._query("MATCH ()-[r]->() WHERE id(r) = $rid DELETE r", {"rid": edge_id})
        self._events.append("relation_deleted", {"relation": doc})

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
