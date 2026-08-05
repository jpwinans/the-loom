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
from collections.abc import Iterable
from typing import Any

from theloom.model import (
    Entity,
    EntityCreate,
    EntityFilter,
)
from theloom.store.filters import apply_entity_filters
from theloom.timeutil import iso_now


class InMemoryGraphStore:
    """A graph held in dictionaries, satisfying ``GraphReadPort``."""

    def __init__(self) -> None:
        # id -> wire doc, in creation order (dicts preserve insertion order,
        # which is exactly what FalkorDB's `ORDER BY id(n)` gives us).
        self._entities: dict[str, dict[str, Any]] = {}

    # -- writes (scene setting; deliberately outside the port) -----------------

    def create_entity(self, spec: EntityCreate) -> Entity:
        """Create an entity, generating id and timestamps like the real store."""
        now = iso_now()
        doc = spec.model_dump(by_alias=True, exclude_unset=True)
        doc.update(id=str(uuid.uuid4()), created_at=now, updated_at=now)
        entity = Entity.model_validate(doc)
        self._entities[entity.id] = doc
        return entity

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
