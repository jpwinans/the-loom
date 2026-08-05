"""The store operations surface.

One clean abstract interface over one transactional store, with these
semantics:

- The store generates ids (UUID) and timestamps (ISO, ms, Z) on create.
- ``update_*`` merges partial updates and preserves id/created_at; entity
  status changes are validated against the lifecycle transition table.
- ``delete_*`` invalidates by default (entities are retracted, edges have
  their system-time interval closed) and only erases under ``hard=True``;
  ``delete_entity`` returns the resulting record and missing targets raise
  ``NotFoundError``.
- Relations are keyed by (from, to, relationType?) — parallel typed edges
  between the same pair are first-class, and ``relation_id`` addresses one
  specific edge when even the type is shared.
- Batching is a first-class transactional method (``create_relations``),
  never duck-typing.

Bi-temporal: every mutation appends to the event log, updates snapshot the
prior version, and ``read_entity_as_of`` answers "state as of time T".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from theloom.model import (
    Entity,
    EntityCreate,
    EntityFilter,
    Relation,
    RelationCreate,
    RelationFilter,
)

Direction = Literal["outgoing", "incoming", "both"]


class GraphStore(ABC):
    """Abstract operations surface for a single named graph."""

    # -- Entities -------------------------------------------------------------

    @abstractmethod
    def create_entity(self, spec: EntityCreate) -> Entity:
        """Create an entity; the store generates id and timestamps."""

    @abstractmethod
    def read_entity(self, entity_id: str) -> Entity | None:
        """Fetch the current version of an entity, or None if absent."""

    @abstractmethod
    def read_entity_as_of(self, entity_id: str, timestamp: str) -> Entity | None:
        """Bi-temporal read: the entity's state as of the given ISO instant."""

    @abstractmethod
    def update_entity(self, entity_id: str, updates: Mapping[str, Any]) -> Entity:
        """Merge updates (validating status transitions), snapshotting the prior
        version rather than overwriting history. Raises NotFoundError."""

    @abstractmethod
    def delete_entity(self, entity_id: str, hard: bool = False) -> Entity:
        """Retract an entity (status 'retracted', prior incarnation
        snapshotted, attached relations closed out bi-temporally) and return
        the retracted record. ``hard=True`` erases it and its edges instead,
        destroying that history. Raises NotFoundError."""

    @abstractmethod
    def list_entities(self, filter: EntityFilter | None = None) -> list[Entity]:
        """List entities with the filter semantics (status defaults to
        ['active']; order: status → type → name → query → version; then
        sourcedFrom/excludeSourcedFrom with exclude winning), capped at
        ``filter.limit`` when set."""

    # -- Relations ------------------------------------------------------------

    @abstractmethod
    def create_relation(self, spec: RelationCreate) -> Relation:
        """Create a relation; raises NotFoundError if either endpoint is absent."""

    @abstractmethod
    def create_relations(self, specs: Sequence[RelationCreate]) -> list[Relation]:
        """Create a batch of relations in ONE transaction."""

    @abstractmethod
    def read_relation(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> Relation | None:
        """First directed relation from→to (optionally of a type), or None."""

    @abstractmethod
    def read_relations(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> list[Relation]:
        """All directed relations from→to, optionally filtered by type."""

    @abstractmethod
    def update_relation(
        self,
        from_id: str,
        to_id: str,
        updates: Mapping[str, Any],
        relation_type: str | None = None,
        relation_id: str | None = None,
    ) -> Relation:
        """Merge updates into the targeted edge — ``relation_id`` picks one
        specific parallel edge, otherwise the oldest match wins.
        Raises NotFoundError."""

    @abstractmethod
    def delete_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None = None,
        relation_id: str | None = None,
        hard: bool = False,
    ) -> None:
        """Retire the targeted edge bi-temporally (``hard=True`` erases it).
        ``relation_id`` picks one specific parallel edge. Raises NotFoundError."""

    @abstractmethod
    def list_relations(self, filter: RelationFilter | None = None) -> list[Relation]:
        """List relations matching the filter (from/to/relationType/polarity, AND)."""

    @abstractmethod
    def get_relations(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Relation]:
        """Relations attached to an entity; empty when the entity is absent."""

    @abstractmethod
    def get_neighbors(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Entity]:
        """Entities adjacent to an entity, deduplicated."""

    # -- Stats + graph metadata ------------------------------------------------

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """Counts plus zero-filled per-type distributions (graph stats)."""

    @abstractmethod
    def get_metadata(self, key: str) -> Any | None:
        """Read a graph-level metadata value, or None if unset."""

    @abstractmethod
    def set_metadata(self, key: str, value: Any) -> None:
        """Write a graph-level metadata value (JSON-serializable)."""
