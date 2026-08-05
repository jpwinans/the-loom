"""Store invariant tests: the mutation and its event append are one unit.

Fault injection: make the *second* step of a mutation fail and assert there is
no half-state — neither a projection change without its event, nor an event
without its projection change.
"""

from __future__ import annotations

from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import NotFoundError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.events import EventLog
from theloom.store.falkor import FalkorGraphStore


@pytest.fixture()
def store(db: FalkorDB, redis_client: Redis, namespace: str) -> FalkorGraphStore:
    return FalkorGraphStore(db, redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)


@pytest.fixture()
def log(redis_client: Redis, namespace: str) -> EventLog:
    return EventLog(redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)


def spec(name: str = "Systems Thinking") -> EntityCreate:
    return EntityCreate.model_validate(
        {"name": name, "entityType": "concept", "observations": [f"about {name}"]}
    )


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


class Boom(RuntimeError):
    """Injected failure at the event-append step."""


@pytest.fixture()
def break_event_append(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make queueing the event raise, i.e. fail the second half of a mutation."""

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise Boom("event append failed")

    monkeypatch.setattr(EventLog, "queue", explode)


# =============================================================================
# Nothing is written when the event append fails
# =============================================================================


def test_create_entity_writes_nothing_when_the_event_append_fails(
    store: FalkorGraphStore, log: EventLog, break_event_append: None
) -> None:
    with pytest.raises(Boom):
        store.create_entity(spec())
    assert store.list_entities() == []
    assert log.read_all() == []


def test_update_entity_writes_nothing_when_the_event_append_fails(
    store: FalkorGraphStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = store.create_entity(spec())

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise Boom("event append failed")

    monkeypatch.setattr(EventLog, "queue", explode)
    with pytest.raises(Boom):
        store.update_entity(created.id, {"observations": ["changed"]})

    assert store.read_entity(created.id) == created  # doc untouched
    assert [event.type for event in log.read_all()] == ["entity_created"]
    # no orphan version snapshot either
    assert store.read_entity_as_of(created.id, created.created_at) == created


def test_create_relations_writes_nothing_when_the_event_append_fails(
    store: FalkorGraphStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise Boom("event append failed")

    monkeypatch.setattr(EventLog, "queue", explode)
    with pytest.raises(Boom):
        store.create_relations([rel_spec(a.id, b.id), rel_spec(b.id, a.id)])

    assert store.list_relations() == []
    assert [event.type for event in log.read_all()] == ["entity_created", "entity_created"]


def test_delete_entity_writes_nothing_when_the_event_append_fails(
    store: FalkorGraphStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_relation(rel_spec(a.id, b.id))

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise Boom("event append failed")

    monkeypatch.setattr(EventLog, "queue", explode)
    with pytest.raises(Boom):
        store.delete_entity(a.id)

    assert store.read_entity(a.id) == a
    assert len(store.list_relations()) == 1
    assert [event.type for event in log.read_all()][-1] == "relation_created"


# =============================================================================
# No event survives a mutation that failed
# =============================================================================


def test_missing_endpoint_appends_no_relation_event(store: FalkorGraphStore, log: EventLog) -> None:
    a = store.create_entity(spec("A"))
    missing = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(NotFoundError):
        store.create_relations([rel_spec(a.id, missing)])
    assert [event.type for event in log.read_all()] == ["entity_created"]


def test_a_failing_cypher_step_appends_no_event(store: FalkorGraphStore, log: EventLog) -> None:
    """A server-side query error rolls the event back out of the log."""
    with pytest.raises(Exception, match="Invalid input"):
        store._commit([("MATCH (n:_Entity) RETRN n", {})], [("entity_created", {"entity": {}})])
    assert log.read_all() == []
