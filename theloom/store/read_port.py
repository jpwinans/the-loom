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
  port, and nothing here returns a raw doc. Whether the doc twins should
  survive at all is a separate decision, to be taken once their callers are
  typed against this port — not by widening the port to accommodate them.
- **Only what production reads.** The members below are the read methods with
  real callers outside ``theloom/store/``; nothing is declared here on
  speculation.

It is a ``Protocol``, so conformance is structural: ``FalkorGraphStore``
already satisfies it without inheriting anything, and so does the in-memory
adapter in ``theloom/store/memory.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from theloom.model import Entity, EntityFilter, Relation, RelationFilter
from theloom.store.base import Direction

__all__ = ["Direction", "GraphReadPort", "GraphSnapshot"]


class GraphSnapshot(NamedTuple):
    """One graph as it stood at a system time — the answer to "state as of T".

    Not a domain shape (nothing is stored in this form): it is what a
    bi-temporal read returns, so the two halves of a point-in-time view travel
    together and are consistent with each other.
    """

    entities: list[Entity]
    relations: list[Relation]


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

    def read_graph_as_of(self, timestamp: str) -> GraphSnapshot:
        """The whole graph as it stood at an ISO system time.

        Entities are the incarnation current at ``timestamp`` — an entity
        updated since comes back as it read then, one not yet created is
        absent, and status is whatever it was then (no active-only default;
        this is history, not a listing) — in creation order.

        Relations are the edges whose system-time interval was open at
        ``timestamp``: created at/before it and not yet retired *by* it. An
        edge retired since is therefore present, and one already retired then
        is absent — the point of the read, and the reason a caller cannot get
        this right from ``list_relations`` alone, which only ever sees today's
        live edges. Both ends must be present in ``entities``, so the snapshot
        is internally consistent: no edge dangles off an entity that did not
        exist yet. Live edges come first, then the resurrected ones, each group
        in creation order.

        Two edges of the erasure: a ``hard`` delete destroys history, so
        anything erased that way is absent from every snapshot; and a relation
        *updated* since the bound carries its current payload, because edge
        updates overwrite in place rather than snapshotting.
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

    def get_relations(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Relation]:
        """Edges attached to one entity; empty when the entity does not exist.

        ``direction='both'`` returns the incoming edges first, then the
        outgoing ones — each group in creation order.
        """
        ...

    def get_neighbors(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Entity]:
        """Entities adjacent to one entity, deduplicated, in the order their
        first connecting edge appears in ``get_relations``."""
        ...

    # -- vectors ---------------------------------------------------------------

    def get_entity_vectors(self) -> dict[str, list[float]]:
        """Every embedded entity's vector, keyed by entity id, in entity
        creation order. Entities without an embedding are absent."""
        ...


if TYPE_CHECKING:
    # Conformance, checked by the typechecker rather than asserted in prose:
    # both adapters are assignable to the port. A signature drifting on either
    # side fails `mypy --strict` here, at the definition, instead of at some
    # far-away call site.
    from theloom.store.falkor import FalkorGraphStore
    from theloom.store.memory import InMemoryGraphStore

    def _adapters_conform(falkor: FalkorGraphStore, memory: InMemoryGraphStore) -> None:
        _live: GraphReadPort = falkor
        _fake: GraphReadPort = memory
