"""Read-port conformance: one behaviour suite, run against every adapter.

The port (``theloom.store.read_port.GraphReadPort``) is the narrow, typed read
surface production actually uses, in one dialect (model objects — never wire
docs). Every test here runs twice: once against the in-memory adapter (no
docker) and once against the FalkorDB adapter (live docker). A behaviour the
suite pins is a behaviour both adapters owe.

Reads go through ``harness.reader``, which is typed as the port and nothing
wider — a test that passes here passes for any conforming adapter. Seeding is
deliberately *outside* the port (writes are not a read concern) and goes
through the harness helpers.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pytest

from theloom.model import (
    Entity,
    EntityCreate,
    EntityFilter,
    Relation,
    RelationCreate,
    RelationFilter,
)
from theloom.store.read_port import GraphReadPort
from theloom.timeutil import iso_now


@dataclass
class Harness:
    """One adapter under test plus the writes needed to set a scene."""

    name: str
    reader: GraphReadPort
    _store: object

    def entity(self, name: str, **overrides: object) -> Entity:
        return self._store.create_entity(spec(name, **overrides))  # type: ignore[attr-defined]

    def update(self, entity_id: str, updates: dict[str, object]) -> Entity:
        return self._store.update_entity(entity_id, updates)  # type: ignore[attr-defined]

    def relations(self, *specs: RelationCreate) -> list[Relation]:
        return self._store.create_relations(list(specs))  # type: ignore[attr-defined]

    def relation(self, from_id: str, to_id: str, **overrides: object) -> Relation:
        return self.relations(rel_spec(from_id, to_id, **overrides))[0]

    def invalidate(self, from_id: str, to_id: str, relation_type: str | None = None) -> Relation:
        return self._store.invalidate_relation(from_id, to_id, relation_type)  # type: ignore[attr-defined]

    def update_relation(
        self,
        from_id: str,
        to_id: str,
        updates: dict[str, object],
        relation_type: str | None = None,
    ) -> Relation:
        return self._store.update_relation(from_id, to_id, updates, relation_type)  # type: ignore[attr-defined]

    def vector(self, entity_id: str, values: Sequence[float]) -> None:
        self._store.set_entity_vector(entity_id, list(values))  # type: ignore[attr-defined]


@pytest.fixture(params=["memory", "falkor"])
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    """One adapter per param. The memory adapter never touches docker, so the
    falkor fixtures are only resolved on the falkor pass."""
    if request.param == "memory":
        from theloom.store.memory import InMemoryGraphStore

        memory = InMemoryGraphStore()
        yield Harness("memory", memory, memory)
        return
    from theloom.store.falkor import FalkorGraphStore

    store = FalkorGraphStore(
        request.getfixturevalue("db"),
        request.getfixturevalue("redis_client"),
        graph_name=f"{request.getfixturevalue('namespace')}-g",
        key_prefix=request.getfixturevalue("namespace"),
    )
    yield Harness("falkor", store, store)


def spec(name: str, **overrides: object) -> EntityCreate:
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
# read_entity
# =============================================================================


def test_read_entity_returns_the_stored_entity(harness: Harness) -> None:
    created = harness.entity("Feedback Loop")

    found = harness.reader.read_entity(created.id)

    assert found is not None
    assert found.id == created.id
    assert found.name == "Feedback Loop"
    assert found.entity_type.value == "concept"
    assert found.observations == ["observation about Feedback Loop"]


def test_read_entity_returns_none_for_an_unknown_id(harness: Harness) -> None:
    harness.entity("Feedback Loop")

    assert harness.reader.read_entity("no-such-id") is None


# =============================================================================
# read_entities (the bulk form)
# =============================================================================


def test_read_entities_keys_the_requested_entities_by_id(harness: Harness) -> None:
    loop = harness.entity("Feedback Loop")
    delay = harness.entity("Delay")
    harness.entity("Unasked For")

    found = harness.reader.read_entities([loop.id, delay.id])

    assert set(found) == {loop.id, delay.id}
    assert found[loop.id].name == "Feedback Loop"
    assert found[delay.id].name == "Delay"


def test_read_entities_omits_unknown_ids_and_tolerates_duplicates(harness: Harness) -> None:
    loop = harness.entity("Feedback Loop")

    found = harness.reader.read_entities([loop.id, "no-such-id", loop.id])

    assert list(found) == [loop.id]


def test_read_entities_of_nothing_is_empty(harness: Harness) -> None:
    harness.entity("Feedback Loop")

    assert harness.reader.read_entities([]) == {}


# =============================================================================
# list_entities
# =============================================================================


def test_list_entities_returns_active_entities_in_creation_order(harness: Harness) -> None:
    harness.entity("First")
    harness.entity("Second")
    harness.entity("Retired", status="retracted")

    listed = harness.reader.list_entities()

    assert [e.name for e in listed] == ["First", "Second"]


def test_list_entities_honours_an_explicit_status_filter(harness: Harness) -> None:
    harness.entity("First")
    harness.entity("Retired", status="retracted")

    listed = harness.reader.list_entities(
        EntityFilter.model_validate({"statusFilter": ["retracted"]})
    )

    assert [e.name for e in listed] == ["Retired"]


def test_list_entities_matches_name_case_insensitively_and_partially(harness: Harness) -> None:
    harness.entity("Feedback Loop")
    harness.entity("Delay")

    listed = harness.reader.list_entities(EntityFilter.model_validate({"name": "feedback"}))

    assert [e.name for e in listed] == ["Feedback Loop"]


def test_list_entities_query_searches_observations_as_well_as_name(harness: Harness) -> None:
    harness.entity("Delay", observations=["dampens the reinforcing loop"])
    harness.entity("Unrelated", observations=["nothing to see"])

    listed = harness.reader.list_entities(EntityFilter.model_validate({"query": "REINFORCING"}))

    assert [e.name for e in listed] == ["Delay"]


def test_list_entities_narrows_by_entity_type(harness: Harness) -> None:
    harness.entity("Feedback Loop")
    harness.entity("Limits to Growth", entityType="source")

    listed = harness.reader.list_entities(EntityFilter.model_validate({"entityType": "source"}))

    assert [e.name for e in listed] == ["Limits to Growth"]


def test_list_entities_limit_caps_the_window_after_filtering(harness: Harness) -> None:
    harness.entity("First")
    harness.entity("Second")
    harness.entity("Third")

    listed = harness.reader.list_entities(EntityFilter.model_validate({"limit": 2}))

    assert [e.name for e in listed] == ["First", "Second"]


# =============================================================================
# read_relation / read_relations
# =============================================================================


def test_read_relation_returns_the_edge_between_two_entities(harness: Harness) -> None:
    source = harness.entity("Delay")
    target = harness.entity("Feedback Loop")
    created = harness.relation(source.id, target.id, relationType="causes")

    found = harness.reader.read_relation(source.id, target.id)

    assert found is not None
    assert found.id == created.id
    assert found.from_ == source.id
    assert found.to == target.id
    assert found.relation_type.value == "causes"


def test_read_relation_is_directed(harness: Harness) -> None:
    source = harness.entity("Delay")
    target = harness.entity("Feedback Loop")
    harness.relation(source.id, target.id)

    assert harness.reader.read_relation(target.id, source.id) is None


def test_read_relation_narrows_by_relation_type(harness: Harness) -> None:
    source = harness.entity("Delay")
    target = harness.entity("Feedback Loop")
    harness.relation(source.id, target.id, relationType="causes")
    contradicts = harness.relation(source.id, target.id, relationType="contradicts")

    found = harness.reader.read_relation(source.id, target.id, "contradicts")

    assert found is not None
    assert found.id == contradicts.id


def test_read_relations_returns_every_parallel_edge_in_creation_order(harness: Harness) -> None:
    source = harness.entity("Delay")
    target = harness.entity("Feedback Loop")
    first = harness.relation(source.id, target.id, relationType="causes")
    second = harness.relation(source.id, target.id, relationType="contradicts")

    found = harness.reader.read_relations(source.id, target.id)

    assert [r.id for r in found] == [first.id, second.id]


def test_read_relations_is_empty_when_the_pair_is_unconnected(harness: Harness) -> None:
    source = harness.entity("Delay")
    target = harness.entity("Feedback Loop")

    assert harness.reader.read_relations(source.id, target.id) == []


# =============================================================================
# list_relations
# =============================================================================


def test_list_relations_returns_every_edge_in_creation_order(harness: Harness) -> None:
    a = harness.entity("A")
    b = harness.entity("B")
    c = harness.entity("C")
    first = harness.relation(a.id, b.id)
    second = harness.relation(b.id, c.id)

    listed = harness.reader.list_relations()

    assert [r.id for r in listed] == [first.id, second.id]


def test_list_relations_narrows_by_endpoint_and_type(harness: Harness) -> None:
    a = harness.entity("A")
    b = harness.entity("B")
    c = harness.entity("C")
    causes = harness.relation(a.id, b.id, relationType="causes")
    harness.relation(a.id, c.id, relationType="causes")
    harness.relation(a.id, b.id, relationType="contradicts")

    listed = harness.reader.list_relations(
        RelationFilter.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )

    assert [r.id for r in listed] == [causes.id]


def test_list_relations_narrows_by_polarity(harness: Harness) -> None:
    a = harness.entity("A")
    b = harness.entity("B")
    positive = harness.relation(a.id, b.id, relationType="causes", polarity="+")
    harness.relation(a.id, b.id, relationType="causes", polarity="-")

    listed = harness.reader.list_relations(RelationFilter.model_validate({"polarity": "+"}))

    assert [r.id for r in listed] == [positive.id]


# =============================================================================
# get_relations / get_neighbors
# =============================================================================


def test_get_relations_defaults_to_incoming_edges_before_outgoing(harness: Harness) -> None:
    hub = harness.entity("Hub")
    upstream = harness.entity("Upstream")
    downstream = harness.entity("Downstream")
    out = harness.relation(hub.id, downstream.id)
    incoming = harness.relation(upstream.id, hub.id)

    attached = harness.reader.get_relations(hub.id)

    # 'both' is incoming then outgoing — creation order does not reorder it.
    assert [r.id for r in attached] == [incoming.id, out.id]


def test_get_relations_narrows_by_direction(harness: Harness) -> None:
    hub = harness.entity("Hub")
    upstream = harness.entity("Upstream")
    downstream = harness.entity("Downstream")
    out = harness.relation(hub.id, downstream.id)
    incoming = harness.relation(upstream.id, hub.id)

    assert [r.id for r in harness.reader.get_relations(hub.id, "outgoing")] == [out.id]
    assert [r.id for r in harness.reader.get_relations(hub.id, "incoming")] == [incoming.id]


def test_get_relations_narrows_by_relation_type(harness: Harness) -> None:
    hub = harness.entity("Hub")
    other = harness.entity("Other")
    causes = harness.relation(hub.id, other.id, relationType="causes")
    harness.relation(hub.id, other.id, relationType="contradicts")

    attached = harness.reader.get_relations(hub.id, "both", "causes")

    assert [r.id for r in attached] == [causes.id]


def test_get_relations_of_an_absent_entity_is_empty(harness: Harness) -> None:
    harness.entity("Hub")

    assert harness.reader.get_relations("no-such-id") == []


def test_get_neighbors_deduplicates_across_parallel_edges(harness: Harness) -> None:
    hub = harness.entity("Hub")
    other = harness.entity("Other")
    harness.relation(hub.id, other.id, relationType="causes")
    harness.relation(hub.id, other.id, relationType="contradicts")

    neighbors = harness.reader.get_neighbors(hub.id, "outgoing")

    assert [e.name for e in neighbors] == ["Other"]


def test_get_neighbors_follows_both_directions_by_default(harness: Harness) -> None:
    hub = harness.entity("Hub")
    upstream = harness.entity("Upstream")
    downstream = harness.entity("Downstream")
    harness.relation(hub.id, downstream.id)
    harness.relation(upstream.id, hub.id)

    neighbors = harness.reader.get_neighbors(hub.id)

    assert [e.name for e in neighbors] == ["Upstream", "Downstream"]


# =============================================================================
# get_entity_vectors
# =============================================================================


def test_get_entity_vectors_returns_only_embedded_entities(harness: Harness) -> None:
    embedded = harness.entity("Embedded")
    harness.entity("Bare")
    harness.vector(embedded.id, [0.25, -0.5, 1.0])

    vectors = harness.reader.get_entity_vectors()

    assert list(vectors) == [embedded.id]
    assert vectors[embedded.id] == pytest.approx([0.25, -0.5, 1.0])


def test_get_entity_vectors_is_empty_without_embeddings(harness: Harness) -> None:
    harness.entity("Bare")

    assert harness.reader.get_entity_vectors() == {}


# =============================================================================
# list_entities — the two filters that need edges
# =============================================================================


def test_list_entities_sourced_from_keeps_only_entities_sourcing_the_target(
    harness: Harness,
) -> None:
    paper = harness.entity("Limits to Growth", entityType="source")
    claim = harness.entity("Overshoot")
    harness.entity("Unsourced")
    harness.relation(claim.id, paper.id, relationType="sources")

    listed = harness.reader.list_entities(EntityFilter.model_validate({"sourcedFrom": [paper.id]}))

    assert [e.name for e in listed] == ["Overshoot"]


def test_list_entities_exclude_sourced_from_wins_over_sourced_from(harness: Harness) -> None:
    paper = harness.entity("Limits to Growth", entityType="source")
    claim = harness.entity("Overshoot")
    harness.relation(claim.id, paper.id, relationType="sources")

    listed = harness.reader.list_entities(
        EntityFilter.model_validate({"sourcedFrom": [paper.id], "excludeSourcedFrom": [paper.id]})
    )

    assert listed == []


# =============================================================================
# read_graph_as_of (the bi-temporal graph-level read)
# =============================================================================


def test_as_of_omits_an_entity_that_did_not_exist_yet(harness: Harness) -> None:
    harness.entity("Delay")
    time.sleep(0.01)
    pivot = iso_now()  # strictly after Delay, strictly before Feedback Loop
    time.sleep(0.01)
    harness.entity("Feedback Loop")

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert [e.name for e in snapshot.entities] == ["Delay"]


def test_as_of_shows_the_entity_version_current_at_the_bound(harness: Harness) -> None:
    created = harness.entity("Delay", observations=["as first written"])
    time.sleep(0.01)
    pivot = iso_now()  # strictly after the create, strictly before the update
    time.sleep(0.01)
    harness.update(created.id, {"name": "Perception Delay", "observations": ["rewritten"]})

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert [e.name for e in snapshot.entities] == ["Delay"]
    assert snapshot.entities[0].observations == ["as first written"]
    assert harness.reader.read_entity(created.id) is not None
    assert harness.reader.read_entity(created.id).name == "Perception Delay"  # type: ignore[union-attr]


def test_as_of_keeps_only_the_relations_that_had_been_created(harness: Harness) -> None:
    a = harness.entity("Delay")
    b = harness.entity("Feedback Loop")
    already = harness.relation(a.id, b.id)
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    c = harness.entity("Overshoot")
    harness.relation(a.id, c.id)  # both edge and endpoint postdate the bound

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert [r.id for r in snapshot.relations] == [already.id]


def test_as_of_omits_a_relation_retired_before_the_bound(harness: Harness) -> None:
    a = harness.entity("Delay")
    b = harness.entity("Feedback Loop")
    harness.relation(a.id, b.id)
    harness.invalidate(a.id, b.id)
    time.sleep(0.01)
    pivot = iso_now()  # the edge's whole interval is behind the bound

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert {e.name for e in snapshot.entities} == {"Delay", "Feedback Loop"}
    assert snapshot.relations == []


def test_as_of_resurrects_a_relation_retired_after_the_bound(harness: Harness) -> None:
    """The edge's interval is open at the bound even though it is closed now."""
    a = harness.entity("Delay")
    b = harness.entity("Feedback Loop")
    edge = harness.relation(a.id, b.id)
    time.sleep(0.01)
    pivot = iso_now()  # inside [created_at, tx_to)
    time.sleep(0.01)
    harness.invalidate(a.id, b.id)

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert [r.id for r in snapshot.relations] == [edge.id]
    assert harness.reader.list_relations() == []  # and it is gone from today


def test_as_of_shows_the_relation_version_current_at_the_bound(harness: Harness) -> None:
    """An update invalidates, never overwrites: the doc that was live at the
    bound must come back, not the doc that replaced it."""
    a = harness.entity("Delay")
    b = harness.entity("Feedback Loop")
    edge = harness.relation(a.id, b.id, relationType="supports", strength="moderate")
    time.sleep(0.01)
    pivot = iso_now()  # strictly after the create, strictly before the update
    time.sleep(0.01)
    harness.update_relation(a.id, b.id, {"strength": "strong"}, "supports")

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert [r.id for r in snapshot.relations] == [edge.id]
    assert snapshot.relations[0].strength == "moderate"
    current = harness.reader.read_relation(a.id, b.id, "supports")
    assert current is not None and current.strength == "strong"


def test_as_of_shows_the_pre_retype_relation_at_the_bound(harness: Harness) -> None:
    """Retyping is structurally delete + recreate, but bi-temporally it is an
    update like any other: the pre-retype incarnation stays readable."""
    a = harness.entity("Delay")
    b = harness.entity("Feedback Loop")
    edge = harness.relation(a.id, b.id, relationType="related_to")
    time.sleep(0.01)
    pivot = iso_now()  # strictly after the create, strictly before the retype
    time.sleep(0.01)
    harness.update_relation(a.id, b.id, {"relationType": "supports"}, "related_to")

    snapshot = harness.reader.read_graph_as_of(pivot)

    assert [r.id for r in snapshot.relations] == [edge.id]
    assert snapshot.relations[0].relation_type == "related_to"
    current = harness.reader.read_relation(a.id, b.id, "supports")
    assert current is not None and current.id == edge.id


# =============================================================================
# The fake as a test helper
# =============================================================================


def test_both_adapters_are_recognised_as_read_ports(harness: Harness) -> None:
    assert isinstance(harness.reader, GraphReadPort)


def test_the_fake_is_reachable_from_the_shared_test_helpers() -> None:
    """Later work must be able to unit-test against the port without docker."""
    from tests.fakes import seeded_memory_store

    store = seeded_memory_store(
        entities=[spec("Delay"), spec("Feedback Loop")],
        relations=[(0, 1, "causes")],
    )

    listed = store.list_entities()
    assert [e.name for e in listed] == ["Delay", "Feedback Loop"]
    edge = store.read_relation(listed[0].id, listed[1].id)
    assert edge is not None
    assert edge.relation_type.value == "causes"


def test_the_memory_store_fixture_is_a_read_port(memory_store: GraphReadPort) -> None:
    assert isinstance(memory_store, GraphReadPort)
    assert memory_store.list_entities() == []
