"""The read port: the narrow, typed read surface production depends on.

Why this exists: ``theloom/store/base.py`` describes the *whole* operations
surface, and consumers that only read still had to name the concrete
``FalkorGraphStore`` (or, worse, ``store: Any``) to get at it. This port is the
read half, stated once, so a reader can be typed by what it needs.

Two rules keep it narrow:

- **One dialect.** Every method speaks model objects — ``Entity`` and
  ``Relation``. The store also carries wire-doc twins of several of these
  reads (``read_entity_doc``, ``list_entity_docs``, ``list_relation_docs``,
  ``read_entity_docs``), which exist because a few commands must preserve
  verbatim key order. Those stay where they are; they are not part of the
  port, and nothing here returns a raw doc.
- **Only what production reads.** The members below are the read methods with
  real callers outside ``theloom/store/``; nothing is declared here on
  speculation.

It is a ``Protocol``, so conformance is structural: ``FalkorGraphStore``
already satisfies it without inheriting anything, and so does the in-memory
adapter in ``theloom/store/memory.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from theloom.model import Entity, EntityFilter, Relation, RelationFilter
from theloom.store.base import Direction

__all__ = ["Direction", "GraphReadPort"]


@runtime_checkable
class GraphReadPort(Protocol):
    """Read-only view of one named graph.

    Ordering is part of the contract: entity and relation listings come back in
    creation order, and ``get_relations(direction='both')`` returns the
    incoming edges before the outgoing ones.
    """

    # -- entities -------------------------------------------------------------

    def read_entity(self, entity_id: str) -> Entity | None:
        """The current version of an entity, or None if there is no such id.

        Returns the entity whatever its status — retracted entities are still
        readable by id; it is *listing* that defaults to active only.
        """
        ...

    def read_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        """Many entities at once, keyed by id; ids with no entity are absent.

        This is the form to reach for when hydrating a neighbourhood — a
        per-id loop costs the backing store a scan each time. Duplicate ids
        collapse; an empty request is an empty answer.
        """
        ...

    def list_entities(self, filter: EntityFilter | None = None) -> list[Entity]:
        """Entities matching the filter, in creation order.

        Semantics are ``theloom/store/filters.py`` — status (defaulting to
        active alone) → entityType → name → query → version → session — plus
        the two that need edges, ``sourcedFrom`` / ``excludeSourcedFrom``,
        where exclude wins. ``filter.limit`` caps the window after filtering.
        """
        ...

    # -- relations ------------------------------------------------------------

    def read_relation(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> Relation | None:
        """The first directed edge from→to (optionally of a type), or None.

        Direction is meant literally: an edge to→from is not a match. Where
        parallel edges exist, "first" is the oldest.
        """
        ...

    def read_relations(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> list[Relation]:
        """Every directed edge from→to, oldest first, optionally of a type."""
        ...

    def list_relations(self, filter: RelationFilter | None = None) -> list[Relation]:
        """Relations matching the filter, in creation order.

        Semantics are ``theloom/store/filters.py``: from / to / relationType /
        polarity / session, ANDed. A null polarity in the filter is no filter
        at all — only an explicit value narrows.
        """
        ...
