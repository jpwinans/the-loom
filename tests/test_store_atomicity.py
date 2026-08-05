"""Store invariant tests: the mutation and its event append are one unit.

Fault injection: make half of a mutation fail and assert there is no
half-state — neither a projection change without its event, nor an event
without its projection change.

Four failure points, one invariant:

- the event append raises while the transaction is being built (nothing has
  reached the server yet);
- the Cypher fails at EXEC, where Redis cannot roll back — so every mutation
  is ONE statement, and a multi-statement batch compensates explicitly;
- the mutation turns out to have been wrong only from the reply (a relation
  batch whose endpoint vanished);
- the XADD is the half that fails at EXEC: the mutation is already applied and
  unrollbackable, so the log is repaired by re-appending, and an unrepairable
  log gap is named in a typed error rather than left silent.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import NotFoundError, OperationError
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


def relation_version_count(store: FalkorGraphStore) -> int:
    """How many edges have been closed out bi-temporally."""
    rows = store._rows("MATCH (v:_RelationVersion) RETURN count(v)")
    return int(rows[0][0]) if rows else 0


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
        store._commit(("MATCH (n:_Entity) RETRN n", {}), [("entity_created", {"entity": {}})])
    assert log.read_all() == []


def test_partial_batch_creates_no_relation_and_no_event(
    store: FalkorGraphStore, log: EventLog
) -> None:
    """One good pair + one missing endpoint: the good edge must not survive.

    Redis has no rollback, so a batch that only reveals its bad row in the
    reply would otherwise leave a live edge with no entry in the log.
    """
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    missing = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(NotFoundError):
        store.create_relations([rel_spec(a.id, b.id), rel_spec(a.id, missing)])
    assert store.list_relations() == []
    assert [event.type for event in log.read_all()] == ["entity_created", "entity_created"]


# =============================================================================
# A multi-part mutation is one query, so a failure leaves no half-state
# =============================================================================


def corrupt_commit_cypher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every subsequent committed query fail server-side, after it was built.

    This is the failure mode Redis cannot roll back: the transaction reaches
    EXEC and the query errors there. A mutation that spans several Cypher
    statements would apply the ones before the failing one; a mutation that is
    a single statement applies nothing.
    """
    original = FalkorGraphStore._commit

    def corrupt(
        self: FalkorGraphStore,
        step: tuple[str, dict[str, Any]],
        events: Any,
    ) -> Any:
        cypher, params = step
        return original(self, (cypher + " RETRN boom", params), events)

    monkeypatch.setattr(FalkorGraphStore, "_commit", corrupt)


def test_a_failing_retraction_changes_nothing(
    store: FalkorGraphStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    relation = store.create_relation(rel_spec(a.id, b.id))
    before = [event.type for event in log.read_all()]
    corrupt_commit_cypher(monkeypatch)

    with pytest.raises(Exception, match="Invalid input"):
        store.delete_entity(a.id)

    assert store.read_entity(a.id) == a  # doc never flipped to 'retracted'
    assert [r.id for r in store.list_relations()] == [relation.id]  # edge still live
    assert store.read_entity_as_of(a.id, a.created_at) == a  # no orphan version
    assert relation_version_count(store) == 0  # no orphan close-out snapshot
    assert [event.type for event in log.read_all()] == before


def test_a_failing_relation_invalidation_changes_nothing(
    store: FalkorGraphStore, log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    relation = store.create_relation(rel_spec(a.id, b.id))
    before = [event.type for event in log.read_all()]
    corrupt_commit_cypher(monkeypatch)

    with pytest.raises(Exception, match="Invalid input"):
        store.invalidate_relation(a.id, b.id)

    assert [r.id for r in store.list_relations()] == [relation.id]
    assert relation_version_count(store) == 0  # no orphan close-out snapshot
    assert [event.type for event in log.read_all()] == before


# =============================================================================
# The XADD is the half that fails at EXEC: the log is repaired, never silent
# =============================================================================
#
# The one failure Redis genuinely cannot roll back the other way round. A
# *queue*-time rejection (unknown command, bad arity, OOM) makes Redis abort
# the whole transaction, so neither half runs. A *runtime* rejection of the
# XADD — the stream key holding a non-stream value, a server-side refusal of
# the write — comes back as an error value inside the EXEC reply while the
# GRAPH.QUERY beside it has already applied. There is no inverse statement to
# undo the mutation with, so the compensation runs the other way: the mutation
# is real, therefore its event is true, therefore append it again.


@pytest.fixture()
def poisoned_key(redis_client: Redis, namespace: str) -> str:
    """A key holding a non-stream value — XADD against it fails at EXEC."""
    key = f"{namespace}:not-a-stream"
    redis_client.set(key, "not a stream")
    return key


def break_queued_append(
    monkeypatch: pytest.MonkeyPatch, poisoned_key: str, only_call: int | None = None
) -> None:
    """Aim the *in-transaction* XADD at a non-stream key, leaving the retry good.

    Models a runtime rejection that does not outlive the transaction: the
    queued append errors at EXEC, and ``EventLog.append`` — the repair path —
    still writes to the real stream. ``only_call`` poisons just the nth queued
    event, so a multi-event commit fails in the middle.
    """
    calls = itertools.count(1)

    def queue(self: EventLog, pipe: Any, event_type: str, payload: dict[str, Any]) -> None:
        nth = next(calls)
        key = poisoned_key if only_call is None or only_call == nth else self.key
        pipe.xadd(key, {"type": event_type, "payload": json.dumps(payload)})

    monkeypatch.setattr(EventLog, "queue", queue)


def test_a_failed_event_append_is_repaired_after_the_mutation_commits(
    store: FalkorGraphStore,
    log: EventLog,
    poisoned_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mutation stands and the log catches up — the caller sees success."""
    break_queued_append(monkeypatch, poisoned_key)

    created = store.create_entity(spec())

    assert store.read_entity(created.id) == created
    events = log.read_all()
    assert [event.type for event in events] == ["entity_created"]
    assert events[0].payload["entity"]["id"] == created.id


def test_a_repaired_event_lands_in_batch_order(
    store: FalkorGraphStore,
    log: EventLog,
    poisoned_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the second of two events fails: the repair appends it behind the
    first, so the batch keeps the order it was committed in."""
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    break_queued_append(monkeypatch, poisoned_key, only_call=2)

    relations = store.create_relations([rel_spec(a.id, b.id), rel_spec(b.id, a.id)])

    events = log.read_all()
    assert [event.type for event in events] == [
        "entity_created",
        "entity_created",
        "relation_created",
        "relation_created",
    ]
    assert [event.payload["relation"]["id"] for event in events[2:]] == [
        relation.id for relation in relations
    ]


def test_an_unrepairable_event_append_names_the_gap(
    store: FalkorGraphStore, log: EventLog, redis_client: Redis
) -> None:
    """When the retry fails too, the caller gets a typed error that says which
    events the log is missing — never a silent short log."""
    redis_client.set(log.key, "not a stream")  # every XADD to the log fails

    with pytest.raises(OperationError) as raised:
        store.create_entity(spec())

    assert "entity_created" in str(raised.value)
    assert raised.value.code == "OPERATION_ERROR"
    assert raised.value.__cause__ is not None
    # Redis cannot roll the mutation back, and the error does not pretend it did.
    assert len(store.list_entities()) == 1


def test_a_failing_query_beats_a_failing_event_append(
    store: FalkorGraphStore,
    log: EventLog,
    poisoned_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves fail: the mutation never happened, so there is nothing to
    repair — the query error propagates and the log stays untouched."""
    break_queued_append(monkeypatch, poisoned_key)

    with pytest.raises(Exception, match="Invalid input"):
        store._commit(("MATCH (n:_Entity) RETRN n", {}), [("entity_created", {"entity": {}})])

    assert log.read_all() == []


# =============================================================================
# Retraction leaves no live trace: the entity's vector goes with it
# =============================================================================


def test_retraction_drops_the_entity_vector(store: FalkorGraphStore) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.set_entity_vector(a.id, [1.0, 0.0, 0.0])
    store.set_entity_vector(b.id, [0.0, 1.0, 0.0])

    store.delete_entity(a.id)

    assert set(store.get_entity_vectors()) == {b.id}
    assert [entity_id for entity_id, _ in store.vector_knn([1.0, 0.0, 0.0], 5)] == [b.id]
