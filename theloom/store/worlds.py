"""Branchable belief worlds (desire 12): a world is a named ref over the
same event-sourced store every graph already is — exactly as a git branch is
a ref over the same object store, not a second one.

**Ref shape.** ``theloom.store.refs.RefRegistry`` with ``kind="world"``
(reused unmodified, per its own docstring's forward reference) tracks each
world's lifecycle (``active``/``expired``/``reaped``) and TTL; the world-
specific facts — which graph it forked from, which world (or ``main``) is
its parent, the event id and timestamp it forked at, and its own domain
status (``active``/``merged``/``abandoned``, a different axis than the ref's
own lifecycle status) — live in the ref's ``metadata``, the payload the
registry always leaves to its caller.

**Projection strategy: overlay, not replay-to-materialize.** A world's own
segment is a second, reserved FalkorDB graph (``_world_<id>`` — leading
underscore keeps it out of the multigraph registry the same way ``_chunks``/
``_bridges``/``_refs`` already are), created lazily on first write — forking
appends nothing to it, so ``fork-world`` is the ref registration alone, O(1).
A read against a world composes that segment with a *frozen* view of its
parent: ``WorldGraphStore`` tries the world's own local graph first (a plain,
ordinary ``FalkorGraphStore`` query — no overlay machinery in the common
case), and only on a miss walks the ancestor chain, each ancestor read
through the bi-temporal ``read_entity_as_of``/``read_graph_as_of`` this store
already had (CLAUDE.md invariant 2) at the exact instant the *next* world in
the chain forked away from it. Nothing is ever copied wholesale, however deep
the fork chain: at read time, a shallower "layer" that has an opinion about an
id — present, updated, or deleted — always wins over a deeper one that
doesn't, which is exactly branch-shadowing semantics.

**Write path: copy-on-write through the one commit primitive.** The first
write to an *inherited* id (one a fork has only ever read, never touched)
materializes a verbatim local copy first (``FalkorGraphStore.adopt_entity``/
``adopt_relation`` — through ``commit_steps`` like every other mutation, but
with zero events: the doc is byte-identical to what a reader already saw
through the overlay, so nothing observable changed yet) and then applies the
real mutation on top of that copy through the store's ordinary
``update_entity``/``delete_entity``/etc, which emits the real event exactly
as it would for any other write. ``main``'s own graph is never touched by a
fork: every world write lands in ``_world_<id>``, never in the parent.

**World-param resolution path.** ``theloom.store.worldctx`` is a contextvar
exactly like ``theloom.store.receipts``: ``theloom.cli.registry.run_handler``
opens a scope from the validated ``world`` field (every command's input model
gains it via ``theloom.operations.common.CommandInput``), and
``MultiGraph.get_store`` — the one place any command ever gets a store
instance — reads it and returns a ``WorldGraphStore`` instead of a plain one
when a non-``main`` world is active. No command handler, and no operations
module, needs to know worlds exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from falkordb import FalkorDB
from redis import Redis

from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.model import (
    ALL_ENTITY_TYPES,
    ALL_RELATION_TYPES,
    Entity,
    EntityFilter,
    EntityStatus,
    Relation,
    RelationCreate,
    RelationFilter,
)
from theloom.store.base import Direction
from theloom.store.falkor import FalkorGraphStore
from theloom.store.filters import apply_entity_filters, apply_relation_filters, extract_neighbor_ids
from theloom.store.read_port import GraphSnapshot
from theloom.store.refs import RefRecord
from theloom.timeutil import iso_now

if TYPE_CHECKING:
    from theloom.store.multigraph import MultiGraph

WORLD_KIND = "world"
WORLD_GRAPH_PREFIX = "_world_"

DOMAIN_ACTIVE = "active"
DOMAIN_MERGED = "merged"
DOMAIN_ABANDONED = "abandoned"

_ALL_STATUS_FILTER = EntityFilter.model_validate(
    {"statusFilter": [status.value for status in EntityStatus]}
)


def world_graph_name(world_id: str) -> str:
    """The reserved FalkorDB graph name a world's own segment lives in."""
    return f"{WORLD_GRAPH_PREFIX}{world_id}"


def new_world_id() -> str:
    return f"world-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class WorldLayer:
    """One ancestor a world's overlay falls through to on a local miss:
    ``store`` is that ancestor's own plain segment (never another
    ``WorldGraphStore`` — the chain is flattened at resolution time, so an
    N-deep fork costs one pass over N plain layers, not N nested overlays),
    ``cutoff`` is the wire-ISO instant the *next* world in the chain forked
    away from it (``None`` only ever appears at the root, meaning "read this
    layer live" — the case where the chain bottoms out at ``main`` with no
    ``asOf`` anywhere in it)."""

    store: FalkorGraphStore
    cutoff: str | None


def require_world(multi: MultiGraph, world_id: str) -> RefRecord:
    """The world ref named ``world_id``, or a typed ``NOT_FOUND`` -- the one
    resolution/validation path every world-aware caller uses (this module's
    own fork/list/abandon, ``theloom.operations.worlds``'s diff/merge, and
    ``theloom.operations.receipts.what_changed``'s world-scoped replay), so
    "no such world" always fails the same way regardless of which command
    hit it."""
    record = multi.refs.get(WORLD_KIND, world_id)
    if record is None:
        raise NotFoundError(f"World '{world_id}' not found. Use list-worlds to see active worlds.")
    return record


def resolve_layers(multi: MultiGraph, world_id: str) -> tuple[list[WorldLayer], str]:
    """The flattened ancestor chain for ``world_id``, nearest parent first,
    ``main`` always last — plus the base graph name every layer ultimately
    answers to. Each layer's ``store`` is a *plain* segment (``main``'s own
    graph, or another world's own ``_world_<id>`` graph read as of a fixed
    cutoff) — never itself world-aware, so reading through it never
    recurses."""
    layers: list[WorldLayer] = []
    record = require_world(multi, world_id)
    base_graph = str(record.metadata["baseGraph"])
    cutoff: str | None = str(record.metadata["forkedAt"])
    parent = record.metadata.get("parentWorld")
    seen = {world_id}
    while parent is not None:
        if parent in seen:
            raise OperationError(f"World ref cycle detected at '{parent}'")
        seen.add(parent)
        parent_record = require_world(multi, parent)
        layers.append(WorldLayer(store=multi.plain_store(world_graph_name(parent)), cutoff=cutoff))
        cutoff = str(parent_record.metadata["forkedAt"])
        parent = parent_record.metadata.get("parentWorld")
    layers.append(WorldLayer(store=multi.plain_store(base_graph), cutoff=cutoff))
    return layers, base_graph


def get_world_store(multi: MultiGraph, world_id: str) -> WorldGraphStore:
    """The one factory for a world's store — used by ``MultiGraph.get_store``
    and by anything (merge, diff, the belief-blast-radius composite) that
    needs to read/write a specific world directly."""
    layers, _base_graph = resolve_layers(multi, world_id)
    return WorldGraphStore(
        multi.db,
        multi.redis,
        world_graph_name(world_id),
        multi.key_prefix,
        world_id=world_id,
        layers=layers,
    )


class WorldGraphStore(FalkorGraphStore):
    """A ``FalkorGraphStore`` over one world's own segment, overlaid on its
    ancestor chain (see the module docstring for the full design).

    Duck-types as a plain ``FalkorGraphStore`` for every caller that isn't
    world-aware: every method on ``theloom.store.base.GraphStore`` is either
    overridden here with overlay/copy-on-write semantics, or — for the
    handful of concerns this overlay deliberately does not reconstruct —
    inherited unchanged, which means it operates on this world's own local
    segment only, never composed with its ancestors. Two families of state
    fall in that second group, and both are for the same underlying reason:
    they are written outside the event log (``set_entity_vector`` is a bare
    Cypher property SET; ``set_metadata`` targets a singleton ``:_GraphMeta``
    node with no event at all), so copy-on-write — which works by replaying
    events — has nothing to replay for them into a fork: embeddings
    (``vector_knn``/``set_entity_vector``/``get_entity_vectors``/...) and
    graph-level metadata (``get_metadata``/``set_metadata``).

    Every command whose *primary purpose* is one of those two — the embed-*/
    embedding-*/find-clusters/semantic-gaps/resolve-gaps/*-search/
    suggest-relations commands (``theloom.operations.semantic``), the
    metadata-backed checkpoints/queues (``session-changelog``/
    ``postmortem-evaluate`` (``theloom.operations.epistemic``),
    ``trigger-status``/``process-triggers``
    (``theloom.operations.reification``), ``self-model-update``
    (``theloom.operations.extraction``)), and the three composites whose own
    sections read entity vectors directly (``far-analogy-retrieval``'s
    fingerprint section, ``get_entity_vectors``; ``explore-frontier``'s
    CoverageGap signal, ``theloom.exploration.coverage_gap``;
    ``hypothesis-engine``'s ``gaps`` section, which calls
    ``theloom.operations.semantic.semantic_gaps`` directly and so must
    attach its own copy of the notice rather than inherit one that command
    never forwards) — checks ``params.world`` itself and attaches a
    ``WORLD_PROJECTION_PARTIAL`` notice naming exactly what it did not
    reconstruct; this list is a checked inventory (``grep -rn
    'get_entity_vectors\\|set_entity_vector\\|vector_knn\\|has_entity_vectors\\|
    get_metadata\\|set_metadata' theloom/operations theloom/composites
    theloom/extraction theloom/synthesis theloom/exploration
    theloom/semantic``), not a memory of what was wired — re-run any time a
    new command starts touching either family, this docstring's own list is
    the thing that goes stale first. Two known gaps the same grep turns up
    and this build does NOT cover, both verified to be genuinely
    unreachable from any live command rather than merely unfixed: (1) the
    LLM-synthesis pipeline (``theloom.operations.synthesis``'s
    ``anchor_search_for``, behind ``synthesize``/``synthesize-and-ingest``/
    ``plan-synthesis``/``traverse-synthesis``/``verify-fidelity``) probes
    ``has_entity_vectors``/vector search as one of several anchor-finding
    signals, not its primary purpose, and degrades to its keyword/graph
    fallback rather than erroring inside a fork; (2)
    ``theloom.semantic.deduplication_gate.deduplicate_proposals``'s
    ``has_entity_vectors`` branch is dead code from its only call site
    (``hypothesis-engine``'s ``dedup`` section always passes
    ``embedding_manager=None``, so the function returns via its
    name-matching fallback before that branch is ever reached) — nothing to
    notice about a path nothing can take.
    """

    def __init__(
        self,
        db: FalkorDB,
        redis: Redis,
        local_graph_name: str,
        key_prefix: str,
        *,
        world_id: str,
        layers: list[WorldLayer],
    ) -> None:
        super().__init__(db, redis, local_graph_name, key_prefix)
        self.world_id = world_id
        self._layers = layers

    # -- entities: point reads -----------------------------------------------

    def read_entity(self, entity_id: str) -> Entity | None:
        local = super().read_entity(entity_id)
        if local is not None:
            return local
        if super().entity_tombstoned(entity_id):
            return None  # hard-deleted here; never resurrect from an ancestor
        for layer in self._layers:
            found = (
                layer.store.read_entity(entity_id)
                if layer.cutoff is None
                else layer.store.read_entity_as_of(entity_id, layer.cutoff)
            )
            if found is not None:
                return found
            if layer.store.entity_tombstoned(entity_id):
                return None  # hard-deleted in this ancestor; stop here too
        return None

    def read_entity_as_of(self, entity_id: str, timestamp: str) -> Entity | None:
        local = super().read_entity_as_of(entity_id, timestamp)
        if local is not None:
            return local
        if super().entity_tombstoned(entity_id):
            return None
        for layer in self._layers:
            effective = _clamp(timestamp, layer.cutoff)
            found = layer.store.read_entity_as_of(entity_id, effective)
            if found is not None:
                return found
            if layer.store.entity_tombstoned(entity_id):
                return None
        return None

    def read_entity_doc(self, entity_id: str) -> dict[str, Any] | None:
        entity = self.read_entity(entity_id)
        return entity.model_dump(by_alias=True, exclude_unset=True) if entity is not None else None

    def read_entity_docs(self, entity_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for entity_id in dict.fromkeys(entity_ids):
            doc = self.read_entity_doc(entity_id)
            if doc is not None:
                out[entity_id] = doc
        return out

    def read_entities(self, entity_ids: Iterable[str]) -> dict[str, Entity]:
        return {
            entity_id: Entity.model_validate(doc)
            for entity_id, doc in self.read_entity_docs(entity_ids).items()
        }

    # -- entities: listing ----------------------------------------------------

    def _merged_entities(self) -> list[Entity]:
        covered: set[str] = set()
        merged: list[Entity] = []
        for entity in super().list_entities(_ALL_STATUS_FILTER):
            covered.add(entity.id)
            merged.append(entity)
        covered |= super().entity_ids_tombstoned()
        for layer in self._layers:
            candidates = (
                layer.store.list_entities(_ALL_STATUS_FILTER)
                if layer.cutoff is None
                else layer.store.read_graph_as_of(layer.cutoff).entities
            )
            for entity in candidates:
                if entity.id in covered:
                    continue
                covered.add(entity.id)
                merged.append(entity)
            covered |= layer.store.entity_ids_tombstoned()
        merged.sort(key=lambda entity: entity.created_at)
        return merged

    def list_entities(self, filter: EntityFilter | None = None) -> list[Entity]:
        return apply_entity_filters(self._merged_entities(), filter)

    def list_entities_page(self, filter: EntityFilter | None = None) -> tuple[list[Entity], int]:
        entities = self.list_entities(filter)
        total = len(entities)
        limit = filter.limit if filter is not None else None
        if limit is not None:
            entities = entities[:limit]
        return entities, total

    def list_entity_docs(self, filter: EntityFilter | None = None) -> list[dict[str, Any]]:
        return [
            entity.model_dump(by_alias=True, exclude_unset=True)
            for entity in self.list_entities(filter)
        ]

    # -- entities: writes (copy-on-write) --------------------------------------

    def _ensure_local_entity(self, entity_id: str) -> None:
        if super().read_entity(entity_id) is not None:
            return
        inherited = self.read_entity(entity_id)
        if inherited is None:
            raise NotFoundError(f"Entity not found: {entity_id}")
        self.adopt_entity(inherited.model_dump(by_alias=True, exclude_unset=True))

    def update_entity(self, entity_id: str, updates: Mapping[str, Any]) -> Entity:
        self._ensure_local_entity(entity_id)
        return super().update_entity(entity_id, updates)

    def delete_entity(self, entity_id: str, hard: bool = False) -> Entity:
        self._ensure_local_entity(entity_id)
        if not hard:
            return super().delete_entity(entity_id, hard=False)
        return self._hard_delete_entity_with_tombstone(entity_id)

    def _hard_delete_entity_with_tombstone(self, entity_id: str) -> Entity:
        """A world's hard delete: erase the local copy AND leave a durable
        marker (``:_EntityTombstone``) every read path checks before
        falling through to an ancestor. Without it, the overlay cannot
        tell "erased here" apart from "never touched here" once the live
        node -- and, because hard delete destroys history by design, any
        ``:_EntityVersion`` trail -- is gone, and would silently resurrect
        the entity from whichever ancestor still has it (main included).
        One commit, same ``entity_deleted`` event shape a plain hard
        delete emits, so ``diff-worlds``/``what-changed`` see no
        difference.
        """
        doc = self._read_doc(entity_id)
        if doc is None:
            raise NotFoundError("Entity not found")
        self._commit(
            (
                "MATCH (n:_Entity {id: $id}) DETACH DELETE n "
                "CREATE (:_EntityTombstone {id: $id, tx_from: $now})",
                {"id": entity_id, "now": iso_now()},
            ),
            [("entity_deleted", {"entity": doc})],
        )
        return Entity.model_validate(doc)

    # -- entity merge (writes both entities plus every redirected relation) -----

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
        """Copy-on-write for a merge: the base class's single Cypher
        statement MATCHes both entities and every redirected relation by id
        against *this* graph directly (``theloom.store.falkor.
        FalkorGraphStore.apply_entity_merge``'s docstring) -- against an
        unadopted pair, every clause matches zero rows, so the statement
        silently no-ops while the caller's ``entities_merged`` event still
        commits unconditionally: a success receipt for a write that never
        happened. Ensuring both entities, and the *original* (pre-redirect)
        endpoints of every relation being redirected, are locally present
        first makes the inherited statement's own MATCHes find real rows.
        """
        primary_id = str(primary_doc["id"])
        secondary_id = str(secondary_doc["id"])
        self._ensure_local_entity(primary_id)
        self._ensure_local_entity(secondary_id)
        for doc in redirects:
            # Redirect docs already carry their REWRITTEN from/to (one end
            # is the primary); the edge the inherited statement's MATCH
            # still needs to find is the ORIGINAL one, between the
            # secondary and the other party, same id and type.
            if doc["from"] == primary_id:
                original_from, original_to = secondary_id, str(doc["to"])
            else:
                original_from, original_to = str(doc["from"]), secondary_id
            self._ensure_local_relation(
                original_from, original_to, str(doc["relationType"]), str(doc["id"])
            )
        super().apply_entity_merge(
            primary_doc,
            secondary_doc,
            redirects,
            supersedes_doc,
            previous_primary,
            previous_secondary,
            now,
        )

    # -- relations: point reads -------------------------------------------------

    def read_relations(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> list[Relation]:
        covered: set[str] = set()
        merged: list[Relation] = []
        for relation in super().read_relations(from_id, to_id, relation_type):
            covered.add(relation.id)
            merged.append(relation)
        covered |= super().relation_ids_known_live()
        for layer in self._layers:
            if layer.cutoff is None:
                candidates = layer.store.read_relations(from_id, to_id, relation_type)
                known = layer.store.relation_ids_known_live()
            else:
                snapshot = layer.store.read_graph_as_of(layer.cutoff).relations
                candidates = [
                    relation
                    for relation in snapshot
                    if relation.from_ == from_id
                    and relation.to == to_id
                    and (relation_type is None or relation.relation_type.value == relation_type)
                ]
                known = layer.store.relation_ids_known_as_of(layer.cutoff)
            for relation in candidates:
                if relation.id in covered:
                    continue
                covered.add(relation.id)
                merged.append(relation)
            covered |= known
        merged.sort(key=lambda relation: relation.created_at)
        return merged

    def read_relation(
        self, from_id: str, to_id: str, relation_type: str | None = None
    ) -> Relation | None:
        relations = self.read_relations(from_id, to_id, relation_type)
        return relations[0] if relations else None

    def _find_relation_anywhere(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None,
        relation_id: str | None,
    ) -> Relation | None:
        for relation in self.read_relations(from_id, to_id, relation_type):
            if relation_id is None or relation.id == relation_id:
                return relation
        return None

    # -- relations: listing -----------------------------------------------------

    def _merged_relations(self) -> list[Relation]:
        covered: set[str] = set()
        merged: list[Relation] = []
        for relation in super().list_relations():
            covered.add(relation.id)
            merged.append(relation)
        covered |= super().relation_ids_known_live()
        for layer in self._layers:
            if layer.cutoff is None:
                candidates = layer.store.list_relations()
                known = layer.store.relation_ids_known_live()
            else:
                candidates = layer.store.read_graph_as_of(layer.cutoff).relations
                known = layer.store.relation_ids_known_as_of(layer.cutoff)
            for relation in candidates:
                if relation.id in covered:
                    continue
                covered.add(relation.id)
                merged.append(relation)
            covered |= known
        merged.sort(key=lambda relation: relation.created_at)
        return merged

    def list_relations(self, filter: RelationFilter | None = None) -> list[Relation]:
        return apply_relation_filters(self._merged_relations(), filter)

    def list_relation_docs(self, filter: RelationFilter | None = None) -> list[dict[str, Any]]:
        return [
            relation.model_dump(by_alias=True, exclude_unset=True)
            for relation in self.list_relations(filter)
        ]

    def get_relations(
        self,
        entity_id: str,
        direction: Direction = "both",
        relation_type: str | None = None,
    ) -> list[Relation]:
        all_relations = self._merged_relations()

        def touches(relation: Relation, want: str) -> bool:
            if relation_type is not None and relation.relation_type.value != relation_type:
                return False
            if want == "outgoing":
                return relation.from_ == entity_id
            return relation.to == entity_id

        if direction == "outgoing":
            return [r for r in all_relations if touches(r, "outgoing")]
        if direction == "incoming":
            return [r for r in all_relations if touches(r, "incoming")]
        return [r for r in all_relations if touches(r, "incoming")] + [
            r for r in all_relations if touches(r, "outgoing")
        ]

    # -- relations: writes (copy-on-write) ---------------------------------------

    def create_relation(self, spec: RelationCreate) -> Relation:
        self._ensure_local_entity(spec.from_)
        self._ensure_local_entity(spec.to)
        return super().create_relation(spec)

    def create_relations(self, specs: Sequence[RelationCreate]) -> list[Relation]:
        for spec in specs:
            self._ensure_local_entity(spec.from_)
            self._ensure_local_entity(spec.to)
        return super().create_relations(specs)

    def _ensure_local_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None,
        relation_id: str | None,
    ) -> None:
        if super().read_relations(from_id, to_id, relation_type):
            return
        target = self._find_relation_anywhere(from_id, to_id, relation_type, relation_id)
        if target is None:
            raise NotFoundError("Relation not found")
        self._ensure_local_entity(from_id)
        self._ensure_local_entity(to_id)
        self.adopt_relation(target.model_dump(by_alias=True, exclude_unset=True))

    def update_relation(
        self,
        from_id: str,
        to_id: str,
        updates: Any,
        relation_type: str | None = None,
        relation_id: str | None = None,
    ) -> Relation:
        self._ensure_local_relation(from_id, to_id, relation_type, relation_id)
        return super().update_relation(from_id, to_id, updates, relation_type, relation_id)

    def invalidate_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None = None,
        relation_id: str | None = None,
    ) -> Relation:
        """The soft-delete half of ``delete_relation`` — ``delete_relation``
        already ensures local presence itself before delegating to the base
        class, whose ``hard=False`` path calls back into *this* method
        polymorphically, so that route was already correct. This override
        exists because ``invalidate_relation`` is also a public method other
        callers reach directly (``theloom.operations.extraction``,
        ``theloom.extraction.codebasediff``) without going through
        ``delete_relation`` at all — an update-shaped write like any other,
        it must adopt an inherited relation rather than raise NOT_FOUND
        against the empty local segment."""
        self._ensure_local_relation(from_id, to_id, relation_type, relation_id)
        return super().invalidate_relation(from_id, to_id, relation_type, relation_id)

    def delete_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None = None,
        relation_id: str | None = None,
        hard: bool = False,
    ) -> None:
        self._ensure_local_relation(from_id, to_id, relation_type, relation_id)
        if not hard:
            super().delete_relation(from_id, to_id, relation_type, relation_id, hard=False)
            return
        self._hard_delete_relation_with_tombstone(from_id, to_id, relation_type, relation_id)

    def _hard_delete_relation_with_tombstone(
        self,
        from_id: str,
        to_id: str,
        relation_type: str | None,
        relation_id: str | None,
    ) -> None:
        """The relation twin of ``_hard_delete_entity_with_tombstone`` — see
        its docstring for the full rationale. ``relation_ids_known_live``/
        ``relation_ids_known_as_of`` already fold ``:_RelationTombstone``
        ids into "known" (``theloom.store.falkor.FalkorGraphStore``), so no
        further overlay change is needed once the marker exists."""
        edge_id, doc = self._target_edge(from_id, to_id, relation_type, relation_id)
        self._commit(
            (
                "MATCH ()-[r]->() WHERE id(r) = $rid DELETE r "
                "CREATE (:_RelationTombstone {id: $eid, tx_from: $now})",
                {"rid": edge_id, "eid": doc["id"], "now": iso_now()},
            ),
            [("relation_deleted", {"relation": doc})],
        )

    # -- bi-temporal graph-level reads --------------------------------------------

    def read_graph_as_of(self, timestamp: str) -> GraphSnapshot:
        covered: set[str] = set()
        entities: list[Entity] = []
        local_snapshot = super().read_graph_as_of(timestamp)
        for entity in local_snapshot.entities:
            covered.add(entity.id)
            entities.append(entity)
        covered |= super().entity_ids_tombstoned()
        for layer in self._layers:
            effective = _clamp(timestamp, layer.cutoff)
            for entity in layer.store.read_graph_as_of(effective).entities:
                if entity.id in covered:
                    continue
                covered.add(entity.id)
                entities.append(entity)
            covered |= layer.store.entity_ids_tombstoned()
        entities.sort(key=lambda entity: entity.created_at)
        present = {entity.id for entity in entities}

        rcovered: set[str] = set()
        relations: list[Relation] = []
        for relation in local_snapshot.relations:
            rcovered.add(relation.id)
            relations.append(relation)
        rcovered |= super().relation_ids_known_as_of(timestamp)
        for layer in self._layers:
            effective = _clamp(timestamp, layer.cutoff)
            snapshot = layer.store.read_graph_as_of(effective)
            for relation in snapshot.relations:
                if relation.id in rcovered:
                    continue
                rcovered.add(relation.id)
                relations.append(relation)
            rcovered |= layer.store.relation_ids_known_as_of(effective)
        relations = [r for r in relations if r.from_ in present and r.to in present]
        relations.sort(key=lambda relation: relation.created_at)
        return GraphSnapshot(entities=entities, relations=relations)

    # -- stats ---------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        entity_distribution = {t.value: 0 for t in ALL_ENTITY_TYPES}
        relation_distribution = {t.value: 0 for t in ALL_RELATION_TYPES}
        entities = self._merged_entities()
        for entity in entities:
            entity_distribution[entity.entity_type.value] += 1
        relations = self._merged_relations()
        for relation in relations:
            relation_distribution[relation.relation_type.value] += 1
        return {
            "entityCount": len(entities),
            "relationCount": len(relations),
            "entityTypeDistribution": entity_distribution,
            "relationTypeDistribution": relation_distribution,
        }

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


def _clamp(timestamp: str, cutoff: str | None) -> str:
    """The effective as-of bound for a layer: never later than the layer's
    own fork-point cutoff, whatever bound the caller asked for (``cutoff is
    None`` — only the chain's own live top — imposes none)."""
    if cutoff is None:
        return timestamp
    return timestamp if timestamp < cutoff else cutoff


# =============================================================================
# fork / list / abandon — the ref-lifecycle half (diff/merge live in
# theloom.operations.worlds, which also needs notices/envelope wiring)
# =============================================================================


def fork_world(
    multi: MultiGraph,
    *,
    name: str | None,
    graph: str | None,
    from_world: str | None,
    as_of: str | None,
    ttl_seconds: int | None,
) -> RefRecord:
    """Fork a new world at ``from_world``'s (default ``main``) current tip,
    or at a historical moment via ``as_of``. Writes no entity data — a
    ``RefRegistry.register`` call and nothing else, so this is O(1) in the
    size of the graph being forked."""
    parent_world = None if from_world in (None, "main") else from_world
    if parent_world is None:
        base_graph = graph or multi.default_graph
        events_log = multi.plain_store(base_graph).events
    else:
        parent_record = require_world(multi, parent_world)
        base_graph = str(parent_record.metadata["baseGraph"])
        parent_forked_at = str(parent_record.metadata["forkedAt"])
        if as_of is not None and as_of < parent_forked_at:
            raise ValidationError(
                f"asOf '{as_of}' predates parent world '{parent_world}''s own fork point "
                f"('{parent_forked_at}') — fork from an ancestor that already existed then."
            )
        events_log = multi.plain_store(world_graph_name(parent_world)).events
    if as_of is not None:
        forked_at = as_of
        forked_at_event_id = events_log.last_id_before(as_of)
    else:
        forked_at = iso_now()
        forked_at_event_id = events_log.last_id()
    world_id = new_world_id()
    return multi.refs.register(
        WORLD_KIND,
        ref_id=world_id,
        name=name,
        ttl_seconds=ttl_seconds,
        metadata={
            "baseGraph": base_graph,
            "parentWorld": parent_world,
            "forkedAtEventId": forked_at_event_id,
            "forkedAt": forked_at,
            "domainStatus": DOMAIN_ACTIVE,
        },
    )


def world_doc(record: RefRecord) -> dict[str, Any]:
    """A world ref rendered for the CLI: the ref's own lifecycle fields plus
    the fork-specific metadata, camelCased and flattened into one doc."""
    return {
        "worldId": record.id,
        "name": record.name,
        "baseGraph": record.metadata.get("baseGraph"),
        "parentWorld": record.metadata.get("parentWorld") or "main",
        "forkedAtEventId": record.metadata.get("forkedAtEventId"),
        "forkedAt": record.metadata.get("forkedAt"),
        "status": record.metadata.get("domainStatus", DOMAIN_ACTIVE),
        "refStatus": record.status,
        "createdAt": record.created_at,
        "expiresAt": record.expires_at,
        "ttlSeconds": record.ttl_seconds,
        "expired": record.expired,
    }


def list_worlds(multi: MultiGraph, *, include_reaped: bool = True) -> list[dict[str, Any]]:
    """Every world ref, oldest first. ``include_reaped=False`` (the CLI
    command's own default — see ``theloom.operations.worlds.list_worlds``)
    hides ``abandoned``/``merged`` worlds, so a build that forks and
    abandons/merges worlds routinely does not have its default view grow
    monotonically; they are never gone (a reaped ref stays listable as
    history, same as a reaped session), just opt-in via
    ``includeReaped: true``.

    Checks both axes, not just one: ``record.status`` (the ref registry's
    own ``active``/``expired``/``reaped`` lifecycle, which ``abandon_world``
    sets to ``reaped``) and ``record.metadata["domainStatus"]`` (this kind's
    own ``active``/``merged``/``abandoned`` axis, which ``theloom.operations.
    worlds.merge_world`` sets to ``"merged"``) — because ``merge_world``
    deliberately does NOT reap the ref (``theloom.store.refs.RefRegistry.
    update_metadata``'s own docstring: the two axes are independent by
    design, so a merged world's segment stays fully readable — diff-worlds,
    what-changed, a repeat-merge's own NOT_FOUND-style guard — the same way
    it was before merging). Checking ``record.status`` alone therefore never
    hid a merged world; only an abandoned one, whose ref happens to be
    reaped for the unrelated reason that ``abandon_world`` deletes its
    segment outright.
    """
    records = multi.refs.list(WORLD_KIND)
    if not include_reaped:
        records = [
            record
            for record in records
            if record.status != "reaped" and record.metadata.get("domainStatus") != DOMAIN_MERGED
        ]
    return [world_doc(record) for record in records]


def abandon_world(multi: MultiGraph, world_id: str) -> dict[str, Any]:
    """Mark a world's ref dead and delete its own segment (graph + event
    stream) — the TTL-reaper machinery session workspaces already have
    (``RefRegistry.reap`` + deleting the namespace's graphs), reused
    verbatim for a world's one segment instead of a session's many."""
    record = require_world(multi, world_id)
    already_reaped = record.status == "reaped"
    if not already_reaped:
        multi.plain_store(world_graph_name(world_id)).delete_graph_data()
        record = multi.refs.reap(WORLD_KIND, world_id)
        record = multi.refs.update_metadata(
            WORLD_KIND, world_id, {"domainStatus": DOMAIN_ABANDONED}
        )
    doc = world_doc(record)
    doc["alreadyReaped"] = already_reaped
    return doc


def purge_world(multi: MultiGraph, world_id: str) -> None:
    """Delete a world's segment AND erase its ref record outright — the
    ephemeral counterpart to ``abandon_world``'s reap-and-keep-as-history,
    for a caller (``belief-blast-radius``) that owns a world's whole
    lifecycle end-to-end within one call and never wants it to surface in
    ``list-worlds`` at all, not even as a reaped entry. Idempotent: purging
    an already-purged (or never-existed) world is a silent no-op."""
    record = multi.refs.get(WORLD_KIND, world_id)
    if record is None:
        return
    if record.status != "reaped":
        multi.plain_store(world_graph_name(world_id)).delete_graph_data()
    multi.refs.purge(WORLD_KIND, world_id)
