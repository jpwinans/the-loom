"""Write-receipts (desire 1): theloom.store.receipts's collector/attach
mechanism, and its two integration points — commit_steps recording ids into
an active scope, and EventLog stamping causedBy from the same scope — proved
against a real FalkorDB store rather than mocked, since the whole point is
that the ids are the store's own.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate, RelationCreate
from theloom.store import receipts
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


# =============================================================================
# The bare collector/attach mechanism, no store involved
# =============================================================================


def test_record_is_a_no_op_outside_a_collecting_scope() -> None:
    receipts.record(["1-0"])  # must not raise, must not leak anywhere observable


def test_collecting_yields_ids_recorded_during_the_block() -> None:
    with receipts.collecting("create-entity") as ids:
        receipts.record(["1-0"])
        receipts.record(["2-0", "3-0"])
    assert ids == ["1-0", "2-0", "3-0"]


def test_collecting_scopes_reset_command_and_ids_on_exit() -> None:
    assert receipts.current_command() is None
    with receipts.collecting("create-entity"):
        assert receipts.current_command() == "create-entity"
    assert receipts.current_command() is None
    receipts.record(["stray"])  # outside the scope again: swallowed, not leaked


def test_nested_collecting_scopes_do_not_bleed_into_each_other() -> None:
    with receipts.collecting("outer") as outer_ids:
        receipts.record(["outer-1"])
        with receipts.collecting("inner") as inner_ids:
            receipts.record(["inner-1"])
            assert receipts.current_command() == "inner"
        assert receipts.current_command() == "outer"
        receipts.record(["outer-2"])
    assert outer_ids == ["outer-1", "outer-2"]
    assert inner_ids == ["inner-1"]


def test_forget_removes_only_the_named_ids() -> None:
    with receipts.collecting("create-relations") as ids:
        receipts.record(["a", "b", "c"])
        receipts.forget(["b"])
    assert ids == ["a", "c"]


def test_forget_outside_a_scope_is_a_no_op() -> None:
    receipts.forget(["whatever"])  # must not raise


def test_attach_is_a_no_op_when_nothing_was_collected() -> None:
    assert receipts.attach({"count": 1}, []) == {"count": 1}
    assert receipts.attach("a message", []) == "a message"


def test_attach_adds_event_ids_to_a_dict_result_without_mutating_it() -> None:
    result = {"id": "x"}
    out = receipts.attach(result, ["1-0", "2-0"])
    assert out == {"id": "x", "eventIds": ["1-0", "2-0"]}
    assert result == {"id": "x"}  # the caller's dict is untouched


def test_attach_promotes_a_plain_string_result_to_a_message_object() -> None:
    out = receipts.attach("Relation retracted successfully.", ["1-0"])
    assert out == {"message": "Relation retracted successfully.", "eventIds": ["1-0"]}


# =============================================================================
# Integration: commit_steps records into whatever scope is active
# =============================================================================


def test_a_store_commit_outside_any_scope_records_nothing(store: FalkorGraphStore) -> None:
    store.create_entity(spec())  # no collecting() open — must not raise


def test_a_store_commit_records_its_event_id_into_the_active_scope(
    store: FalkorGraphStore, log: EventLog
) -> None:
    with receipts.collecting("create-entity") as ids:
        entity = store.create_entity(spec())
    events = log.read_all()
    assert [event.id for event in events] == ids
    assert events[0].payload["entity"]["id"] == entity.id


def test_every_commit_inside_one_scope_accumulates(store: FalkorGraphStore, log: EventLog) -> None:
    with receipts.collecting("create-relation") as ids:
        a = store.create_entity(spec("A"))
        b = store.create_entity(spec("B"))
        store.create_relation(rel_spec(a.id, b.id))
    assert [event.type for event in log.read_all()] == [
        "entity_created",
        "entity_created",
        "relation_created",
    ]
    assert len(ids) == 3


def test_a_batch_that_gets_rejected_records_no_phantom_ids(
    store: FalkorGraphStore, log: EventLog
) -> None:
    """create_relations discards its events when the reply disagrees with the
    request (a concurrently-deleted endpoint) — the ids commit_steps recorded
    for that batch must be scrubbed too, or a receipt would name events that
    were never actually earned."""
    from theloom.errors import NotFoundError

    a = store.create_entity(spec("A"))
    missing = "00000000-0000-4000-8000-000000000000"
    with receipts.collecting("create-relations") as ids:
        with pytest.raises(NotFoundError):
            store.create_relations([rel_spec(a.id, missing)])
    assert ids == []
    assert [event.type for event in log.read_all()] == ["entity_created"]


# =============================================================================
# Integration: EventLog stamps causedBy from the same scope
# =============================================================================


def test_events_carry_no_caused_by_outside_a_scope(store: FalkorGraphStore, log: EventLog) -> None:
    store.create_entity(spec())
    assert log.read_all()[0].caused_by is None


def test_events_are_stamped_with_the_active_command_name(
    store: FalkorGraphStore, log: EventLog
) -> None:
    with receipts.collecting("create-entity"):
        store.create_entity(spec())
    assert log.read_all()[0].caused_by == "create-entity"


def test_every_event_in_one_command_gets_the_same_caused_by(
    store: FalkorGraphStore, log: EventLog
) -> None:
    with receipts.collecting("update-entity"):
        a = store.create_entity(spec("A"))
        b = store.create_entity(spec("B"))
        store.create_relation(rel_spec(a.id, b.id))
    assert [event.caused_by for event in log.read_all()] == ["update-entity"] * 3


def test_bulk_replay_events_are_stamped_and_recorded(
    store: FalkorGraphStore, log: EventLog
) -> None:
    doc = {
        **spec("Imported").model_dump(by_alias=True, exclude_unset=True),
        "id": "10000000-0000-4000-8000-000000000000",
        "created_at": "2026-01-01T00:00:00.000Z",
        "updated_at": "2026-01-01T00:00:00.000Z",
    }
    store.import_entity_doc(doc)
    with receipts.collecting("bulk-import") as ids:
        count = store.replay_creation_events([doc], [])
    assert count == 1
    assert len(ids) == 1
    events = log.read_all()
    assert events[0].caused_by == "bulk-import"
    assert events[0].id == ids[0]


# =============================================================================
# EventLog.read_ids / read_range: the span primitive what-changed replays
# =============================================================================


def test_read_range_is_inclusive_of_both_bounds(store: FalkorGraphStore, log: EventLog) -> None:
    a = store.create_entity(spec("A"))
    b = store.create_entity(spec("B"))
    store.create_entity(spec("C"))
    all_events = log.read_all()
    start, end = all_events[0].id, all_events[1].id
    ranged = log.read_range(start, end)
    assert [e.id for e in ranged] == [all_events[0].id, all_events[1].id]
    assert {e.payload["entity"]["id"] for e in ranged} == {a.id, b.id}


def test_read_range_defaults_to_the_whole_stream(store: FalkorGraphStore, log: EventLog) -> None:
    store.create_entity(spec("A"))
    store.create_entity(spec("B"))
    assert len(log.read_range()) == 2


def test_read_ids_returns_exactly_the_named_events_in_request_order(
    store: FalkorGraphStore, log: EventLog
) -> None:
    store.create_entity(spec("A"))
    store.create_entity(spec("B"))
    store.create_entity(spec("C"))
    all_events = log.read_all()
    wanted = [all_events[2].id, all_events[0].id]
    result = log.read_ids(wanted)
    assert [e.id for e in result] == wanted


def test_read_ids_silently_skips_ids_not_in_the_stream(
    store: FalkorGraphStore, log: EventLog
) -> None:
    store.create_entity(spec("A"))
    real_id = log.read_all()[0].id
    result = log.read_ids([real_id, "0-0"])
    assert [e.id for e in result] == [real_id]


def test_read_ids_of_empty_list_is_empty(log: EventLog) -> None:
    assert log.read_ids([]) == []
