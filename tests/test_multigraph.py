"""Multi-graph manager + bridge registry tests.

Semantics: name validation, default undeletable, sorted listGraphs; the bridge
registry rejects duplicate (from,to,type) and preserves insertion order.
Bridges auto-create when a relation spans graphs.
"""

from __future__ import annotations

import pytest

from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


def ent(name: str, entity_type: str = "concept") -> EntityCreate:
    return EntityCreate.model_validate(
        {"name": name, "entityType": entity_type, "observations": []}
    )


def rel(from_id: str, to_id: str, relation_type: str = "related_to") -> RelationCreate:
    return RelationCreate.model_validate(
        {
            "from": from_id,
            "to": to_id,
            "relationType": relation_type,
            "polarity": None,
            "strength": "moderate",
            "evidence": None,
        }
    )


# =============================================================================
# Graph management
# =============================================================================


def test_default_graph_always_listed(multi: MultiGraph) -> None:
    infos = multi.list_graphs()
    assert infos == [{"name": "default", "loaded": False}]


def test_create_and_list_sorted(multi: MultiGraph) -> None:
    multi.create_graph("zeta")
    multi.create_graph("alpha")
    assert [g["name"] for g in multi.list_graphs()] == ["alpha", "default", "zeta"]


@pytest.mark.parametrize("bad", ["_hidden", "has space", "has/slash", ""])
def test_invalid_graph_names_rejected(multi: MultiGraph, bad: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        multi.create_graph(bad)
    assert "Invalid graph name" in str(excinfo.value)


def test_duplicate_graph_rejected(multi: MultiGraph) -> None:
    multi.create_graph("research")
    with pytest.raises(OperationError) as excinfo:
        multi.create_graph("research")
    assert "already exists" in str(excinfo.value)


def test_delete_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    multi.get_store("research").create_entity(ent("x"))
    multi.delete_graph("research")
    assert [g["name"] for g in multi.list_graphs()] == ["default"]


def test_default_graph_undeletable(multi: MultiGraph) -> None:
    with pytest.raises(OperationError) as excinfo:
        multi.delete_graph("default")
    assert "Cannot delete the default graph" in str(excinfo.value)


def test_delete_missing_graph_raises_not_found(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError):
        multi.delete_graph("nope")


# =============================================================================
# Ad-hoc graph registration (the registry gap)
#
# A graph created implicitly via a bare `graph` param — never through
# `create_graph` — used to be invisible to `list_graphs`/`delete_graph`;
# cleanup needed redis-cli. `GraphSpace` now SADDs a graph's name into the
# registry set on every write, atomically with the mutation.
# =============================================================================


def test_ad_hoc_graph_is_registered_on_first_write(multi: MultiGraph) -> None:
    assert not multi.has_graph("adhoc")
    multi.get_store("adhoc").create_entity(ent("x"))  # no multi.create_graph call
    assert multi.has_graph("adhoc")
    assert "adhoc" in [g["name"] for g in multi.list_graphs()]


def test_ad_hoc_graph_is_deletable_without_redis_cli(multi: MultiGraph) -> None:
    multi.get_store("adhoc").create_entity(ent("x"))
    multi.delete_graph("adhoc")  # would raise NotFoundError before the fix
    assert not multi.has_graph("adhoc")


def test_ad_hoc_graph_registered_by_a_relation_write_too(multi: MultiGraph) -> None:
    """Not just create_entity — any write on the store (create_relation,
    update, delete) goes through the same commit primitive and registers the
    graph the same way."""
    store = multi.get_store("adhoc-rel")
    a = store.create_entity(ent("A"))
    b = store.create_entity(ent("B"))
    store.create_relation(rel(a.id, b.id))
    assert "adhoc-rel" in multi.graph_names()


def test_reserved_graphs_are_never_auto_registered(multi: MultiGraph) -> None:
    """The chunk store's reserved graph (`_chunks`) writes through the same
    `GraphSpace` machinery but must never appear in list-graphs — reserved
    names are excluded from auto-registration the same way `create_graph`
    already refuses them explicitly."""
    multi.chunk_store().upsert_chunk("c1", {"sourceId": "doc-1", "text": "hi"}, None)
    assert "_chunks" not in multi.graph_names()
    assert not multi.has_graph("_chunks")


# =============================================================================
# Bridge registry
# =============================================================================


def bridge_doc(from_id: str, to_id: str, relation_type: str = "supports") -> dict[str, object]:
    return {
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
        "from_graph": "default",
        "to_graph": "research",
    }


def test_bridge_create_list_preserves_insertion_order(multi: MultiGraph) -> None:
    registry = multi.bridges
    first = registry.create_bridge(bridge_doc("e1", "e2"))
    second = registry.create_bridge(bridge_doc("e3", "e4"))
    assert first["id"] != second["id"]
    assert first["created_at"] == first["updated_at"]
    listed = registry.list_bridges()
    assert [b["from"] for b in listed] == ["e1", "e3"]


def test_duplicate_bridge_rejected(multi: MultiGraph) -> None:
    registry = multi.bridges
    registry.create_bridge(bridge_doc("e1", "e2"))
    with pytest.raises(OperationError) as excinfo:
        registry.create_bridge(bridge_doc("e1", "e2"))
    assert "already exists" in str(excinfo.value)
    # same pair, different type is a distinct bridge
    registry.create_bridge(bridge_doc("e1", "e2", relation_type="related_to"))
    assert len(registry.list_bridges()) == 2


def test_bridge_filters_and_logic(multi: MultiGraph) -> None:
    registry = multi.bridges
    registry.create_bridge(bridge_doc("e1", "e2"))
    other = bridge_doc("e3", "e4")
    other["from_graph"], other["to_graph"] = "research", "systems"
    registry.create_bridge(other)

    assert len(registry.list_bridges({"from_graph": "default"})) == 1
    assert len(registry.list_bridges({"to_graph": "systems"})) == 1
    assert len(registry.list_bridges({"entity_id": "e2"})) == 1  # matches from OR to
    assert len(registry.list_bridges({"from_graph": "default", "entity_id": "e4"})) == 0


def test_delete_bridge(multi: MultiGraph) -> None:
    registry = multi.bridges
    registry.create_bridge(bridge_doc("e1", "e2"))
    registry.delete_bridge("e1", "e2", "supports")
    assert registry.list_bridges() == []
    with pytest.raises(NotFoundError):
        registry.delete_bridge("e1", "e2", "supports")


# =============================================================================
# Bridge auto-creation on cross-graph relation
# =============================================================================


def test_cross_graph_relation_becomes_bridge(multi: MultiGraph) -> None:
    multi.create_graph("research")
    a = multi.get_store("default").create_entity(ent("A"))
    b = multi.get_store("research").create_entity(ent("B", "claim"))
    result = multi.create_relation(rel(a.id, b.id, "supports"))
    assert result["bridgeCreated"] is True
    bridges = multi.bridges.list_bridges()
    assert len(bridges) == 1
    assert bridges[0]["from_graph"] == "default"
    assert bridges[0]["to_graph"] == "research"
    # same-graph relation stays an ordinary edge
    c = multi.get_store("default").create_entity(ent("C"))
    same = multi.create_relation(rel(a.id, c.id))
    assert same["bridgeCreated"] is False
    assert len(multi.bridges.list_bridges()) == 1


def test_find_entity_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    a = multi.get_store("research").create_entity(ent("A"))
    assert multi.find_entity_graph(a.id) == "research"
    assert multi.find_entity_graph("00000000-0000-4000-8000-000000000000") is None


# =============================================================================
# Session workspaces (desire 2)
# =============================================================================


def test_begin_session_returns_a_fresh_namespace_and_ttl(multi: MultiGraph) -> None:
    session = multi.begin_session("scratch work", 3600)
    assert session["sessionId"].startswith("sess-")
    assert session["namespace"] == f"{session['sessionId']}-"
    assert session["name"] == "scratch work"
    assert session["status"] == "active"
    assert session["ttlSeconds"] == 3600
    assert session["expiresAt"] is not None
    assert session["expired"] is False
    assert session["graphs"] == []
    assert session["graphCount"] == 0


def test_two_sessions_get_distinct_namespaces(multi: MultiGraph) -> None:
    first = multi.begin_session(None, None)
    second = multi.begin_session(None, None)
    assert first["sessionId"] != second["sessionId"]
    assert first["namespace"] != second["namespace"]


def test_graphs_created_under_the_namespace_are_tracked_without_any_extra_call(
    multi: MultiGraph,
) -> None:
    """No "join this session" step: naming a graph under the namespace is
    enough, whether via create_graph or an ad-hoc bare `graph` write."""
    session = multi.begin_session(None, None)
    namespace = session["namespace"]
    multi.create_graph(f"{namespace}explicit")
    multi.get_store(f"{namespace}adhoc").create_entity(ent("x"))
    multi.create_graph("not-in-the-session")

    listed = multi.list_sessions()
    assert len(listed) == 1
    assert sorted(listed[0]["graphs"]) == [f"{namespace}adhoc", f"{namespace}explicit"]
    assert listed[0]["graphCount"] == 2


def test_end_session_deletes_every_member_graph_in_one_call(multi: MultiGraph) -> None:
    session = multi.begin_session(None, None)
    namespace = session["namespace"]
    multi.create_graph(f"{namespace}a")
    multi.get_store(f"{namespace}b").create_entity(ent("x"))
    multi.create_graph("untouched")

    result = multi.end_session(session["sessionId"])
    assert sorted(result["reapedGraphs"]) == [f"{namespace}a", f"{namespace}b"]
    assert result["status"] == "reaped"
    assert result["alreadyReaped"] is False
    assert not multi.has_graph(f"{namespace}a")
    assert not multi.has_graph(f"{namespace}b")
    assert multi.has_graph("untouched")  # never touched — outside the namespace


def test_end_session_on_an_already_reaped_session_is_a_truthful_no_op(multi: MultiGraph) -> None:
    session = multi.begin_session(None, None)
    multi.create_graph(f"{session['namespace']}a")
    first = multi.end_session(session["sessionId"])
    assert first["alreadyReaped"] is False
    assert first["reapedGraphs"] == [f"{session['namespace']}a"]

    second = multi.end_session(session["sessionId"])
    assert second["alreadyReaped"] is True
    assert second["reapedGraphs"] == []


def test_end_session_missing_session_raises_not_found(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError):
        multi.end_session("sess-does-not-exist")


def test_list_sessions_is_oldest_first(multi: MultiGraph) -> None:
    first = multi.begin_session("first", None)
    second = multi.begin_session("second", None)
    listed = multi.list_sessions()
    assert [s["sessionId"] for s in listed] == [first["sessionId"], second["sessionId"]]


# =============================================================================
# World-ref lifecycle on graph deletion (the "worldrefs" leak fix): a world
# ref forked from a graph must not survive that graph's own deletion, however
# the graph was deleted (end-session's per-member reap, or delete-graph
# directly) -- it is permanently dangling the moment its baseGraph is gone.
# =============================================================================


def _world_ids(multi: MultiGraph) -> set[str]:
    return {w["worldId"] for w in multi.list_worlds(include_reaped=True)}


def test_end_session_purges_world_refs_forked_from_reaped_graphs(multi: MultiGraph) -> None:
    session = multi.begin_session(None, None)
    namespace = session["namespace"]
    scratch = f"{namespace}scratch"
    multi.get_store(scratch).create_entity(ent("x"))
    doomed_world = multi.fork_world(
        name="doomed", graph=scratch, from_world=None, as_of=None, ttl_seconds=None
    )

    multi.create_graph("kept")
    kept_world = multi.fork_world(
        name="kept-fork", graph="kept", from_world=None, as_of=None, ttl_seconds=None
    )

    result = multi.end_session(session["sessionId"])
    assert result["reapedGraphs"] == [scratch]
    # The purge is disclosed, not silent: end-session names the worlds it
    # destroyed along with the graphs they were forked from.
    assert result["reapedWorlds"] == [doomed_world["worldId"]]

    remaining = _world_ids(multi)
    assert doomed_world["worldId"] not in remaining, (
        "a world ref forked from a graph end-session just reaped must not survive it"
    )
    assert kept_world["worldId"] in remaining, (
        "a world forked from an untouched graph is not ours to touch"
    )
    assert multi.has_graph("kept")


def test_end_session_twice_purges_world_refs_only_once_no_phantom_events(
    multi: MultiGraph,
) -> None:
    session = multi.begin_session(None, None)
    scratch = f"{session['namespace']}scratch"
    multi.get_store(scratch).create_entity(ent("x"))
    world = multi.fork_world(
        name="doomed", graph=scratch, from_world=None, as_of=None, ttl_seconds=None
    )

    first = multi.end_session(session["sessionId"])
    assert first["alreadyReaped"] is False
    assert world["worldId"] not in _world_ids(multi)

    second = multi.end_session(session["sessionId"])
    assert second["alreadyReaped"] is True
    assert second["reapedGraphs"] == []
    # Nothing left to prune the second time either -- the ref is already
    # gone, not re-touched, and no event is fabricated for either action.
    assert world["worldId"] not in _world_ids(multi)


def test_delete_graph_directly_also_purges_world_refs(multi: MultiGraph) -> None:
    multi.create_graph("standalone")
    world = multi.fork_world(
        name="scratch", graph="standalone", from_world=None, as_of=None, ttl_seconds=None
    )
    multi.delete_graph("standalone")
    assert world["worldId"] not in _world_ids(multi)
