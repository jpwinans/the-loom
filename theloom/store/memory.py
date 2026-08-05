"""In-memory adapter for the read port.

A supported, non-throwaway implementation of
``theloom.store.read_port.GraphReadPort`` that keeps its graph in Python
objects. It exists so anything typed against the port can be exercised without
docker, and so the port's contract has a second implementation holding it
honest.

Two things make it a fake rather than a mock:

- It stores what FalkorDB stores: the exact wire doc per record, validated
  back into a model on read. Nothing is memoised as a model object, so the
  same round-trip that catches a serialization bug in the real store catches
  one here.
- Filter semantics are not reimplemented. ``theloom/store/filters.py`` is the
  semantics oracle for both adapters; this module calls it.

It is not a second store: nothing persists, nothing is transactional, and no
production code path constructs one. Writes exist only to set a scene.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from theloom.errors import NotFoundError
from theloom.model import (
    Entity,
    EntityCreate,
    EntityFilter,
    Relation,
    RelationCreate,
    RelationFilter,
)
from theloom.store.base import Direction
from theloom.store.filters import (
    apply_entity_filters,
    apply_relation_filters,
    extract_neighbor_ids,
)
from theloom.timeutil import iso_now


class InMemoryGraphStore:
    """A graph held in dictionaries, satisfying ``GraphReadPort``."""

    def __init__(self) -> None:
        # id -> wire doc, in creation order (dicts preserve insertion order,
        # which is exactly what FalkorDB's `ORDER BY id(n)` gives us).
        self._entities: dict[str, dict[str, Any]] = {}
        # Relation wire docs in creation order; parallel edges between the same
        # pair are as first-class here as they are in the graph.
        self._relations: list[dict[str, Any]] = []
        # entity id -> embedding, for the entities that have one.
        self._vectors: dict[str, list[float]] = {}

    # -- writes (scene setting; deliberately outside the port) -----------------

    def create_entity(self, spec: EntityCreate) -> Entity:
        """Create an entity, generating id and timestamps like the real store."""
        now = iso_now()
        doc = spec.model_dump(by_alias=True, exclude_unset=True)
        doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
        entity = Entity.model_validate(doc)
        self._entities[entity.id] = doc
        return entity

    def create_relation(self, spec: RelationCreate) -> Relation:
        return self.create_relations([spec])[0]

    def create_relations(self, specs: Sequence[RelationCreate]) -> list[Relation]:
        """Create edges, all of them or none — a missing endpoint raises
        ``NotFoundError`` before anything is stored, as in the real store."""
        now = iso_now()
        docs: list[dict[str, Any]] = []
        for spec in specs:
            doc = spec.model_dump(by_alias=True, exclude_unset=True)
            doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
            docs.append(doc)
        missing = sorted(
            {str(doc[end]) for doc in docs for end in ("from", "to")} - set(self._entities)
        )
        if missing:
            raise NotFoundError(
                f"Entity not found: relation endpoints must exist (missing {', '.join(missing)})"
            )
        self._relations.extend(docs)
        return [Relation.model_validate(doc) for doc in docs]

    def set_entity_vector(self, entity_id: str, vector: list[float]) -> None:
        """Attach an embedding to an entity (same store, as in FalkorDB)."""
        self._vectors[entity_id] = [float(x) for x in vector]

    # -- reads (the port) ------------------------------------------------------

    def read_entity(self, entity_id: str) -> Entity | None:
        doc = self._entities.get(entity_id)
        return Entity.model_validate(doc) if doc is not None else None

    def read_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        return {
            entity_id: Entity.model_validate(self._entities[entity_id])
            for entity_id in dict.fromkeys(entity_ids)
            if entity_id in self._entities
        }

    def list_entities(self, filter: EntityFilter | None = None) -> list[Entity]:
        entities = [Entity.model_validate(doc) for doc in self._entities.values()]
        entities = apply_entity_filters(entities, filter)
        if filter is None:
            return entities
        # sourcedFrom / excludeSourcedFrom need edge access, so filters.py
        # leaves them to the adapter. Exclude wins over include.
        included = self._sources_of(filter.sourced_from)
        excluded = self._sources_of(filter.exclude_sourced_from)
        if included is not None:
            entities = [e for e in entities if e.id in included]
        if excluded is not None:
            entities = [e for e in entities if e.id not in excluded]
        return entities[: filter.limit] if filter.limit is not None else entities

    def _sources_of(self, target_ids: list[str] | None) -> set[str] | None:
        """Ids of entities holding a 'sources' relation TO any of the targets."""
        if not target_ids:
            return None
        targets = set(target_ids)
        return {
            str(doc["from"])
            for doc in self._relations
            if doc["relationType"] == "sources" and doc["to"] in targets
        }

    def list_relations(self, filter: RelationFilter | None = None) -> list[Relation]:
        return apply_relation_filters(
            [Relation.model_validate(doc) for doc in self._relations], filter
        )

    def get_entity_vectors(self) -> dict[str, list[float]]:
        # Keyed in entity creation order, matching the graph's `ORDER BY id(n)`.
        return {
            entity_id: list(self._vectors[entity_id])
            for entity_id in self._entities
            if entity_id in self._vectors
        }

    def get_relations(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Relation]:
        def attached(end: str) -> list[Relation]:
            return [
                Relation.model_validate(doc)
                for doc in self._relations
                if doc[end] == entity_id
                and (relation_type is None or doc["relationType"] == relation_type)
            ]

        if direction == "outgoing":
            return attached("from")
        if direction == "incoming":
            return attached("to")
        # 'both' is incoming then outgoing, each in creation order.
        return attached("to") + attached("from")

    def get_neighbors(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Entity]:
        relations = self.get_relations(entity_id, direction, relation_type)
        neighbor_ids = extract_neighbor_ids(entity_id, relations, direction)
        found = self.read_entities(neighbor_ids)
        return [found[nid] for nid in neighbor_ids if nid in found]

    def read_relation(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> Relation | None:
        edges = self.read_relations(from_id, to_id, relation_type)
        return edges[0] if edges else None

    def read_relations(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> list[Relation]:
        return [
            Relation.model_validate(doc)
            for doc in self._relations
            if doc["from"] == from_id
            and doc["to"] == to_id
            and (relation_type is None or doc["relationType"] == relation_type)
        ]
