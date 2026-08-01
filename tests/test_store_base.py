"""Store-interface tests.

The operations surface is one clean abstract interface: relations are keyed by
(from, to, relationType?); batching is a first-class method (`create_relations`),
not duck-typing; point-in-time read is a bi-temporal capability.
"""

from __future__ import annotations

import inspect

import pytest

from theloom.store.base import GraphStore

EXPECTED_OPERATIONS = {
    # entities
    "create_entity",
    "read_entity",
    "read_entity_as_of",
    "update_entity",
    "delete_entity",
    "list_entities",
    # relations (keyed by from/to/relationType)
    "create_relation",
    "create_relations",
    "read_relation",
    "read_relations",
    "update_relation",
    "delete_relation",
    "list_relations",
    "get_relations",
    "get_neighbors",
    # stats + graph metadata
    "get_stats",
    "get_metadata",
    "set_metadata",
}


def test_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        GraphStore()  # type: ignore[abstract]


def test_interface_declares_the_full_operations_surface() -> None:
    abstract = {
        name
        for name, member in inspect.getmembers(GraphStore)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert abstract == EXPECTED_OPERATIONS


def test_complete_subclass_is_instantiable() -> None:
    namespace = {name: (lambda self, *a, **k: None) for name in EXPECTED_OPERATIONS}
    fake_cls = type("FakeStore", (GraphStore,), namespace)
    store = fake_cls()
    assert isinstance(store, GraphStore)
