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
