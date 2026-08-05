"""Test doubles that let a unit test exercise the read port without docker.

``theloom.store.memory.InMemoryGraphStore`` is the supported in-memory adapter
for ``theloom.store.read_port.GraphReadPort``; this module is where tests reach
for it, plus one seeding helper so a scene is a literal rather than a ritual.

    from tests.fakes import seeded_memory_store

    store = seeded_memory_store(
        entities=[EntityCreate.model_validate({...})],
        relations=[(0, 1, "causes")],   # indices into `entities`
    )

Anything typed against ``GraphReadPort`` can then be handed ``store`` directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from theloom.model import EntityCreate, RelationCreate
from theloom.store.memory import InMemoryGraphStore

__all__ = ["InMemoryGraphStore", "seeded_memory_store"]


def seeded_memory_store(
    entities: Sequence[EntityCreate] = (),
    relations: Sequence[tuple[int, int, str]] = (),
) -> InMemoryGraphStore:
    """An in-memory store holding ``entities``, wired by ``relations``.

    Relations are ``(from_index, to_index, relation_type)`` over the created
    entities, so a scene can be written without juggling generated ids.
    """
    store = InMemoryGraphStore()
    created = [store.create_entity(spec) for spec in entities]
    if relations:
        store.create_relations(
            [
                RelationCreate.model_validate(
                    {
                        "from": created[from_index].id,
                        "to": created[to_index].id,
                        "relationType": relation_type,
                    }
                )
                for from_index, to_index, relation_type in relations
            ]
        )
    return store
