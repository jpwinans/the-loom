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

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pytest

from theloom.model import Entity, EntityCreate, Relation, RelationCreate
from theloom.store.read_port import GraphReadPort


@dataclass
class Harness:
    """One adapter under test plus the writes needed to set a scene."""

    name: str
    reader: GraphReadPort
    _store: object

    def entity(self, name: str, **overrides: object) -> Entity:
        return self._store.create_entity(spec(name, **overrides))  # type: ignore[attr-defined]

    def relations(self, *specs: RelationCreate) -> list[Relation]:
        return self._store.create_relations(list(specs))  # type: ignore[attr-defined]

    def relation(self, from_id: str, to_id: str, **overrides: object) -> Relation:
        return self.relations(rel_spec(from_id, to_id, **overrides))[0]

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
