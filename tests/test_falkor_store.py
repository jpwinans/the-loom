"""FalkorDB store tests — CRUD and history coverage.

CRUD semantics: store generates UUID + ISO timestamps; update merges and
preserves id/created_at; delete returns the deleted entity; relations are keyed
by (from, to, relationType?) with parallel edges. Plus event-log append per
mutation and bi-temporal point-in-time reads.
"""

from __future__ import annotations

import itertools
import re
import time
from collections.abc import Iterator

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import LoomError, NotFoundError
from theloom.model import (
    EntityCreate,
    EntityFilter,
    RelationCreate,
    RelationFilter,
)
from theloom.store.events import EventLog
from theloom.store.falkor import FalkorGraphStore
from theloom.timeutil import iso_now

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.fixture()
def store(db: FalkorDB, redis_client: Redis, namespace: str) -> FalkorGraphStore:
    return FalkorGraphStore(db, redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)


def spec(name: str = "Systems Thinking", **overrides: object) -> EntityCreate:
    base: dict[str, object] = {
        "name": name,
        "entityType": "concept",
        "observations": [f"observation about {name}"],
    }
    base.update(overrides)
    return EntityCreate.model_validate(base)


def rel_spec(from_id: str, to_id: str, **overrides: object) -> RelationCreate:
    base: dict[str, object] = {
        "from": from_id,
        "to": to_id,
        "relationType": "related_to",
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
    }
    base.update(overrides)
    return RelationCreate.model_validate(base)


# =============================================================================
# Entity CRUD
# =============================================================================


def test_create_entity_generates_id_and_timestamps(store: FalkorGraphStore) -> None:
    entity = store.create_entity(spec())
    assert UUID_RE.match(entity.id)
    assert ISO_RE.match(entity.created_at)
    assert entity.created_at == entity.updated_at
    assert entity.name == "Systems Thinking"


def test_read_entity_round_trips(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec())
    read = store.read_entity(created.id)
    assert read == created


def test_read_missing_entity_returns_none(store: FalkorGraphStore) -> None:
    assert store.read_entity("00000000-0000-4000-8000-000000000000") is None


def test_update_entity_merges_and_preserves_immutables(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec())
    time.sleep(0.002)
    updated = store.update_entity(created.id, {"observations": ["changed"]})
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.observations == ["changed"]
    assert updated.name == created.name  # untouched fields survive
    assert updated.updated_at > created.updated_at


def test_update_missing_entity_raises_not_found(store: FalkorGraphStore) -> None:
    with pytest.raises(NotFoundError):
        store.update_entity("00000000-0000-4000-8000-000000000000", {"name": "x"})


def test_delete_entity_retracts_it_and_returns_the_retracted_record(
    store: FalkorGraphStore,
) -> None:
    created = store.create_entity(spec())
    retracted = store.delete_entity(created.id)
    assert retracted.id == created.id
    assert retracted.status is not None and retracted.status.value == "retracted"
    # gone from the default (active) projection, but still readable by id
    assert store.list_entities() == []
    read = store.read_entity(created.id)
    assert read is not None and read.status is not None
    assert read.status.value == "retracted"
    # idempotent: retracting an already-retracted entity is a no-op
    assert store.delete_entity(created.id).id == created.id
    with pytest.raises(NotFoundError):
        store.delete_entity("00000000-0000-4000-8000-000000000000")


def test_delete_entity_keeps_the_prior_incarnation_readable_as_of(
    store: FalkorGraphStore,
) -> None:
    created = store.create_entity(spec())
    time.sleep(0.002)
    store.delete_entity(created.id)
    assert store.read_entity_as_of(created.id, created.created_at) == created


def test_hard_delete_entity_removes_it(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec())
    deleted = store.delete_entity(created.id, hard=True)
    assert deleted.id == created.id
    assert store.read_entity(created.id) is None
    with pytest.raises(NotFoundError):
        store.delete_entity(created.id, hard=True)


def test_delete_entity_invalidates_attached_relations(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    relation = store.create_relation(rel_spec(a.id, b.id))
    store.delete_entity(a.id)
    assert store.list_relations() == []
    versions = store._rows(
        "MATCH (v:_RelationVersion {relation_id: $id}) RETURN v.tx_to", {"id": relation.id}
    )
    assert len(versions) == 1 and versions[0][0] is not None


def test_hard_delete_entity_drops_attached_relations(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_relation(rel_spec(a.id, b.id))
    store.delete_entity(a.id, hard=True)
    assert store.list_relations() == []


# =============================================================================
# Status lifecycle enforcement
# =============================================================================


def test_status_transition_enforced_on_update(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec())
    store.update_entity(created.id, {"status": "retracted"})
    with pytest.raises(LoomError) as excinfo:
        store.update_entity(created.id, {"status": "active"})
    assert excinfo.value.code == "VALIDATION_ERROR"
    assert "Invalid status transition" in str(excinfo.value)


def test_investigating_can_return_to_active(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec())
    store.update_entity(created.id, {"status": "investigating"})
    updated = store.update_entity(created.id, {"status": "active"})
    assert updated.status == "active"


# =============================================================================
# Filters
# =============================================================================


def test_list_entities_defaults_to_active_only(store: FalkorGraphStore) -> None:
    active = store.create_entity(spec("Active One"))
    other = store.create_entity(spec("Deprecated One"))
    store.update_entity(other.id, {"status": "deprecated"})
    listed = store.list_entities()
    assert [e.id for e in listed] == [active.id]
    listed_all = store.list_entities(
        EntityFilter.model_validate({"statusFilter": ["active", "deprecated"]})
    )
    assert {e.id for e in listed_all} == {active.id, other.id}


def test_list_entities_filters_type_name_query(store: FalkorGraphStore) -> None:
    concept = store.create_entity(spec("Feedback Loops"))
    claim = store.create_entity(
        spec("Delays destabilize", entityType="claim", observations=["long feedback delays"])
    )
    by_type = store.list_entities(EntityFilter.model_validate({"entityType": "claim"}))
    assert [e.id for e in by_type] == [claim.id]
    by_name = store.list_entities(EntityFilter.model_validate({"name": "feedback lo"}))
    assert [e.id for e in by_name] == [concept.id]  # case-insensitive partial
    by_query = store.list_entities(EntityFilter.model_validate({"query": "FEEDBACK DELAYS"}))
    assert [e.id for e in by_query] == [claim.id]  # matches observations too


def test_sourced_from_filters_and_exclude_wins(store: FalkorGraphStore) -> None:
    source_a = store.create_entity(spec("Source A", entityType="source"))
    source_b = store.create_entity(spec("Source B", entityType="source"))
    claim_a = store.create_entity(spec("Claim A", entityType="claim"))
    claim_both = store.create_entity(spec("Claim Both", entityType="claim"))
    store.create_relation(rel_spec(claim_a.id, source_a.id, relationType="sources"))
    store.create_relation(rel_spec(claim_both.id, source_a.id, relationType="sources"))
    store.create_relation(rel_spec(claim_both.id, source_b.id, relationType="sources"))

    sourced = store.list_entities(EntityFilter.model_validate({"sourcedFrom": [source_a.id]}))
    assert {e.id for e in sourced} == {claim_a.id, claim_both.id}

    # exclude wins when an entity matches both include and exclude
    both = store.list_entities(
        EntityFilter.model_validate(
            {"sourcedFrom": [source_a.id], "excludeSourcedFrom": [source_b.id]}
        )
    )
    assert {e.id for e in both} == {claim_a.id}


# =============================================================================
# Relation CRUD (keyed by from/to/relationType, parallel edges)
# =============================================================================


def test_create_and_read_relation(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    created = store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    assert UUID_RE.match(created.id)
    read = store.read_relation(a.id, b.id, "supports")
    assert read == created
    assert store.read_relation(b.id, a.id) is None  # directed: reverse not found
    assert store.read_relation(a.id, b.id, "contradicts") is None


def test_parallel_edges_are_kept_distinct(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    store.create_relation(rel_spec(a.id, b.id, relationType="questions"))
    both = store.read_relations(a.id, b.id)
    assert {r.relation_type for r in both} == {"supports", "questions"}
    only = store.read_relations(a.id, b.id, "supports")
    assert len(only) == 1


def test_create_relation_missing_endpoint_raises(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    with pytest.raises(NotFoundError):
        store.create_relation(rel_spec(a.id, "00000000-0000-4000-8000-000000000000"))


def test_create_relations_batch_is_transactional(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    c = store.create_entity(spec("C"))
    created = store.create_relations(
        [
            rel_spec(a.id, b.id, relationType="supports"),
            rel_spec(b.id, c.id, relationType="causes", polarity="+"),
            rel_spec(c.id, a.id, relationType="dampens", polarity="-"),
        ]
    )
    assert len(created) == 3
    assert len(store.list_relations()) == 3


def test_update_relation_merges_and_preserves_immutables(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    created = store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    time.sleep(0.002)
    updated = store.update_relation(a.id, b.id, {"strength": "strong"}, "supports")
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.strength == "strong"
    assert updated.updated_at > created.updated_at


def test_update_missing_relation_raises(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    with pytest.raises(NotFoundError):
        store.update_relation(a.id, b.id, {"strength": "weak"})


def test_update_relation_can_change_relation_type(store: FalkorGraphStore) -> None:
    # update-relation can retype an edge (relationType is an updatable field,
    # not a selector). The edge must be retyped structurally so Cypher
    # type-filtered traversals stay consistent with the doc.
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    created = store.create_relation(rel_spec(a.id, b.id, relationType="related_to"))
    updated = store.update_relation(a.id, b.id, {"relationType": "supports"})
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.relation_type == "supports"
    assert store.read_relations(a.id, b.id, "related_to") == []
    retyped = store.get_relations(a.id, relation_type="supports")
    assert [r.id for r in retyped] == [created.id]


def test_create_relations_batch_returns_specs_in_order(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    created = store.create_relations(
        [
            rel_spec(a.id, b.id, relationType="supports"),
            rel_spec(a.id, b.id, relationType="questions"),
            rel_spec(b.id, a.id, relationType="related_to"),
        ]
    )
    assert [r.relation_type for r in created] == ["supports", "questions", "related_to"]
    assert [(r.from_, r.to) for r in created] == [(a.id, b.id), (a.id, b.id), (b.id, a.id)]


def test_delete_relation_targets_parallel_edge(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    store.create_relation(rel_spec(a.id, b.id, relationType="questions"))
    store.delete_relation(a.id, b.id, "supports")
    remaining = store.read_relations(a.id, b.id)
    assert [r.relation_type for r in remaining] == ["questions"]


def test_delete_relation_targets_a_same_typed_parallel_edge_by_id(
    store: FalkorGraphStore,
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    first = store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    second = store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    store.delete_relation(a.id, b.id, relation_id=second.id)
    assert [r.id for r in store.read_relations(a.id, b.id)] == [first.id]


def test_update_relation_targets_a_same_typed_parallel_edge_by_id(
    store: FalkorGraphStore,
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    first = store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    second = store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    updated = store.update_relation(a.id, b.id, {"evidence": "second"}, relation_id=second.id)
    assert updated.id == second.id
    by_id = {r.id: r for r in store.read_relations(a.id, b.id)}
    assert by_id[second.id].evidence == "second"
    assert by_id[first.id].evidence is None


def test_relation_id_that_does_not_match_the_pair_raises_not_found(
    store: FalkorGraphStore,
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_relation(rel_spec(a.id, b.id))
    with pytest.raises(NotFoundError):
        store.delete_relation(a.id, b.id, relation_id="00000000-0000-4000-8000-000000000000")


def test_delete_relation_retires_it_bi_temporally(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    relation = store.create_relation(rel_spec(a.id, b.id))
    store.delete_relation(a.id, b.id)
    assert store.read_relations(a.id, b.id) == []
    versions = store._rows(
        "MATCH (v:_RelationVersion {relation_id: $id}) RETURN v.tx_to", {"id": relation.id}
    )
    assert len(versions) == 1 and versions[0][0] is not None


def test_hard_delete_relation_leaves_no_version(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    relation = store.create_relation(rel_spec(a.id, b.id))
    store.delete_relation(a.id, b.id, hard=True)
    assert store.read_relations(a.id, b.id) == []
    assert (
        store._rows(
            "MATCH (v:_RelationVersion {relation_id: $id}) RETURN v.tx_to", {"id": relation.id}
        )
        == []
    )


def test_list_relations_filters(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    c = store.create_entity(spec("C"))
    store.create_relation(rel_spec(a.id, b.id, relationType="causes", polarity="+"))
    store.create_relation(rel_spec(b.id, c.id, relationType="causes", polarity="-"))
    store.create_relation(rel_spec(a.id, c.id, relationType="supports"))
    by_from = store.list_relations(RelationFilter.model_validate({"from": a.id}))
    assert len(by_from) == 2
    by_polarity = store.list_relations(
        RelationFilter.model_validate({"relationType": "causes", "polarity": "-"})
    )
    assert len(by_polarity) == 1
    assert by_polarity[0].from_ == b.id


def test_get_relations_and_neighbors_direction(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    c = store.create_entity(spec("C"))
    store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    store.create_relation(rel_spec(c.id, a.id, relationType="questions"))

    outgoing = store.get_relations(a.id, direction="outgoing")
    assert [r.to for r in outgoing] == [b.id]
    incoming = store.get_relations(a.id, direction="incoming")
    assert [r.from_ for r in incoming] == [c.id]
    both = store.get_relations(a.id)
    assert len(both) == 2
    typed = store.get_relations(a.id, relation_type="supports")
    assert len(typed) == 1

    neighbor_ids = {e.id for e in store.get_neighbors(a.id)}
    assert neighbor_ids == {b.id, c.id}
    out_ids = {e.id for e in store.get_neighbors(a.id, direction="outgoing")}
    assert out_ids == {b.id}


def test_get_relations_missing_entity_is_empty(store: FalkorGraphStore) -> None:
    assert store.get_relations("00000000-0000-4000-8000-000000000000") == []


# =============================================================================
# Stats + metadata
# =============================================================================


def test_get_stats_counts_and_distributions(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B", entityType="claim"))
    store.create_relation(rel_spec(a.id, b.id, relationType="supports"))
    stats = store.get_stats()
    assert stats["entityCount"] == 2
    assert stats["relationCount"] == 1
    assert stats["entityTypeDistribution"]["concept"] == 1
    assert stats["entityTypeDistribution"]["claim"] == 1
    assert stats["entityTypeDistribution"]["loop"] == 0  # zero-filled, all 19 keys
    assert len(stats["entityTypeDistribution"]) == 19
    assert stats["relationTypeDistribution"]["supports"] == 1
    assert stats["relationTypeDistribution"]["calls"] == 0  # zero-filled, all 17 keys
    assert stats["relationTypeDistribution"]["references"] == 0
    assert len(stats["relationTypeDistribution"]) == 17


def test_metadata_round_trips(store: FalkorGraphStore) -> None:
    assert store.get_metadata("nothing") is None
    store.set_metadata("postmortem.history", [{"run": 1}])
    assert store.get_metadata("postmortem.history") == [{"run": 1}]


def test_reads_survive_server_resultset_cap(
    store: FalkorGraphStore, small_resultset_cap: int
) -> None:
    """Full-scan reads must return complete data on graphs larger than the
    server's RESULTSET_SIZE cap, which silently truncates result sets."""
    total = small_resultset_cap + 20
    entities = [store.create_entity(spec(f"E{i:03d}")) for i in range(total)]
    hub = entities[0]
    for other in entities[1:]:
        store.create_relation(rel_spec(hub.id, other.id))
        store.set_entity_vector(other.id, [1.0, 0.0, 0.0])

    stats = store.get_stats()
    assert stats["entityCount"] == total
    assert stats["relationCount"] == total - 1
    assert stats["entityTypeDistribution"]["concept"] == total
    assert stats["relationTypeDistribution"]["related_to"] == total - 1
    assert len(store.list_entities()) == total
    assert len(store.list_relations()) == total - 1
    assert len(store.list_entity_docs()) == total
    assert len(store.list_relation_docs()) == total - 1
    assert len(store.get_relations(hub.id, direction="outgoing")) == total - 1
    assert len(store.get_entity_vectors()) == total - 1


# =============================================================================
# Event log (new capability: every mutation appends an event)
# =============================================================================


def test_mutations_append_events(
    store: FalkorGraphStore, redis_client: Redis, namespace: str
) -> None:
    entity = store.create_entity(spec())
    store.update_entity(entity.id, {"status": "investigating"})
    store.delete_entity(entity.id)
    doomed = store.create_entity(spec("Gone"))
    store.delete_entity(doomed.id, hard=True)
    log = EventLog(redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)
    types = [event.type for event in log.read_all()]
    assert types == [
        "entity_created",
        "entity_status_changed",
        "entity_retracted",
        "entity_created",
        "entity_deleted",
    ]


def test_relation_events_and_plain_update_event(
    store: FalkorGraphStore, redis_client: Redis, namespace: str
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_relation(rel_spec(a.id, b.id))
    store.update_entity(a.id, {"observations": ["new"]})
    store.delete_relation(a.id, b.id)
    log = EventLog(redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)
    types = [event.type for event in log.read_all()]
    assert types == [
        "entity_created",
        "entity_created",
        "relation_created",
        "entity_updated",
        "relation_invalidated",
    ]


def test_event_payload_carries_the_entity(
    store: FalkorGraphStore, redis_client: Redis, namespace: str
) -> None:
    entity = store.create_entity(spec())
    log = EventLog(redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)
    event = log.read_all()[0]
    assert event.payload["entity"]["id"] == entity.id
    assert event.payload["entity"]["name"] == "Systems Thinking"


# =============================================================================
# Bi-temporal: point-in-time read (new capability)
# =============================================================================


def test_read_entity_as_of_returns_historical_state(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec(observations=["v1"]))
    time.sleep(0.01)
    updated = store.update_entity(created.id, {"observations": ["v2"]})
    time.sleep(0.01)
    store.update_entity(created.id, {"observations": ["v3"]})

    as_of_create = store.read_entity_as_of(created.id, created.created_at)
    assert as_of_create is not None and as_of_create.observations == ["v1"]

    as_of_update = store.read_entity_as_of(created.id, updated.updated_at)
    assert as_of_update is not None and as_of_update.observations == ["v2"]

    current = store.read_entity(created.id)
    assert current is not None and current.observations == ["v3"]


def test_read_entity_as_of_before_creation_is_none(store: FalkorGraphStore) -> None:
    created = store.create_entity(spec())
    assert store.read_entity_as_of(created.id, "2000-01-01T00:00:00.000Z") is None


def test_read_graph_as_of_restores_a_retraction_and_the_edges_it_closed_out(
    store: FalkorGraphStore,
) -> None:
    """Retraction closes out every attached edge, so the edges *and* the
    entity's pre-retraction incarnation must come back from a bound before it —
    the one write path where an as-of read that only sees live edges silently
    loses a whole neighbourhood."""
    a = store.create_entity(spec("Population"))
    b = store.create_entity(spec("Resources"))
    edge = store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    store.delete_entity(b.id)

    snapshot = store.read_graph_as_of(pivot)

    assert [e.name for e in snapshot.entities] == ["Population", "Resources"]
    assert [e.status for e in snapshot.entities] == [None, None]  # not yet retracted
    assert [r.id for r in snapshot.relations] == [edge.id]
    # ...and today the edge is gone and the entity is retracted.
    assert store.list_relations() == []
    retracted = store.read_entity(b.id)
    assert retracted is not None and retracted.status is not None
    assert retracted.status.value == "retracted"


# =============================================================================
# Vector index readiness
# =============================================================================
# CREATE VECTOR INDEX returns while FalkorDB constructs the index in the
# background, and queryNodes against an index still under construction is
# rejected ("Invalid arguments for procedure 'db.idx.vector.queryNodes'" —
# observed on the linux/amd64 build with k=1 once ~30 vectors are stored).
# ensure_vector_index therefore barriers on OPERATIONAL, and vector_knn
# recovers once from exactly that rejection.


def _index_status_rows(status: str) -> list[list[object]]:
    return [["_Entity", {"_embedding": ["VECTOR"]}, status]]


def _index_probe(statuses: Iterator[str], polls: dict[str, int]):  # type: ignore[no-untyped-def]
    """Answer the two ``db.indexes()`` probes ensure_vector_index makes: the
    width probe (no index yet, so the CREATE really runs) and the readiness
    poll behind it, which walks ``statuses``."""

    def probe(query: str, params: object = None) -> list[list[object]]:
        assert "db.indexes" in query
        if "options" in query:
            return []
        polls["count"] += 1
        return _index_status_rows(next(statuses))

    return probe


def test_ensure_vector_index_waits_for_construction_to_finish(
    store: FalkorGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses = iter(["UNDER CONSTRUCTION", "UNDER CONSTRUCTION", "OPERATIONAL"])
    polls = {"count": 0}
    monkeypatch.setattr(store, "_rows", _index_probe(statuses, polls))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    store.ensure_vector_index(dimension=2)

    assert polls["count"] == 3


def test_ensure_vector_index_raises_when_construction_never_finishes(
    store: FalkorGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    polls = {"count": 0}
    monkeypatch.setattr(store, "_rows", _index_probe(itertools.repeat("UNDER CONSTRUCTION"), polls))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    # A clock that runs a minute per reading, so the 30s barrier expires on the
    # second poll instead of really blocking the suite for half a minute.
    clock = itertools.count(0.0, 60.0)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))

    with pytest.raises(LoomError) as excinfo:
        store.ensure_vector_index(dimension=2)

    assert "operational" in str(excinfo.value).lower()


def test_vector_knn_recovers_once_from_under_construction_rejection(
    store: FalkorGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = store.create_entity(spec())
    store.set_entity_vector(created.id, [1.0, 0.0])
    store.ensure_vector_index(dimension=2)

    real_rows = store._rows
    state = {"rejected": False, "waited": False}

    def flaky_rows(query: str, params: object = None) -> list[list[object]]:
        if "queryNodes" in query and not state["rejected"]:
            state["rejected"] = True
            raise RuntimeError("Invalid arguments for procedure 'db.idx.vector.queryNodes'")
        return real_rows(query, params) if params is not None else real_rows(query)

    monkeypatch.setattr(store, "_rows", flaky_rows)
    monkeypatch.setattr(
        store,
        "_wait_vector_index_operational",
        lambda timeout=30.0: state.__setitem__("waited", True),
    )
    results = store.vector_knn([1.0, 0.0], 1)
    assert state["rejected"] and state["waited"]
    assert [entity_id for entity_id, _ in results] == [created.id]


def test_vector_knn_propagates_unrelated_errors_without_retry(
    store: FalkorGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = store.create_entity(spec())
    store.set_entity_vector(created.id, [1.0, 0.0])
    store.ensure_vector_index(dimension=2)

    def broken_rows(query: str, params: object = None) -> list[list[object]]:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(store, "_rows", broken_rows)
    with pytest.raises(RuntimeError, match="connection reset"):
        store.vector_knn([1.0, 0.0], 1)
