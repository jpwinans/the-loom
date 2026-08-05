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
from theloom.store.filters import apply_entity_filters, apply_relation_filters
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
        return entities[: filter.limit] if filter.limit is not None else entities

    def list_relations(self, filter: RelationFilter | None = None) -> list[Relation]:
        return apply_relation_filters(
            [Relation.model_validate(doc) for doc in self._relations], filter
        )

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
