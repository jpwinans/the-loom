"""Importer tests.

migrate.import_folder reads a source graph folder (per-graph *.json plus
_bridges.json) into FalkorDB. The exit criterion: the `small` seed round-trips
with identical entity/relation counts — and the imported docs are byte-identical
to the source (ids, timestamps, key presence all preserved), because imports
must serve exactly what was stored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.migrate import import_folder
from theloom.store.events import EventLog
from theloom.store.multigraph import MultiGraph

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_small_seed_round_trips_counts(multi: MultiGraph) -> None:
    reference = json.loads((FIXTURES / "small" / "default.json").read_text())
    import_folder(FIXTURES / "small", multi)
    stats = multi.get_store("default").get_stats()
    assert stats["entityCount"] == len(reference["nodes"]) == 30
    assert stats["relationCount"] == len(reference["edges"]) == 20


def test_imported_docs_are_byte_identical(multi: MultiGraph) -> None:
    reference = json.loads((FIXTURES / "small" / "default.json").read_text())
    import_folder(FIXTURES / "small", multi)
    store = multi.get_store("default")
    for node in reference["nodes"]:
        entity = store.read_entity(node["id"])
        assert entity is not None
        assert entity.model_dump(by_alias=True, exclude_unset=True) == node
    by_id = {edge["id"]: edge for edge in reference["edges"]}
    listed = store.list_relations()
    assert len(listed) == len(by_id)
    for relation in listed:
        assert relation.model_dump(by_alias=True, exclude_unset=True) == by_id[relation.id]


def test_multi_seed_imports_graphs_and_bridges(multi: MultiGraph) -> None:
    import_folder(FIXTURES / "multi", multi)
    assert [g["name"] for g in multi.list_graphs()] == ["default", "research", "systems"]
    reference = json.loads((FIXTURES / "multi" / "_bridges.json").read_text())["bridges"]
    imported = multi.bridges.list_bridges()
    assert imported == reference  # exact docs, insertion order preserved


def test_import_is_idempotent_wipe_first(multi: MultiGraph) -> None:
    import_folder(FIXTURES / "multi", multi)
    import_folder(FIXTURES / "multi", multi)  # re-import must not duplicate
    assert [g["name"] for g in multi.list_graphs()] == ["default", "research", "systems"]
    assert len(multi.bridges.list_bridges()) == 2
    stats = multi.get_store("default").get_stats()
    assert stats["entityCount"] == 2


def test_empty_seed(multi: MultiGraph) -> None:
    import_folder(FIXTURES / "empty", multi)
    assert [g["name"] for g in multi.list_graphs()] == ["default"]
    assert multi.get_store("default").get_stats()["entityCount"] == 0


def test_default_import_appends_no_events(
    multi: MultiGraph, redis_client: Redis, namespace: str
) -> None:
    """Seeding stays verbatim: imported docs are historical state."""
    import_folder(FIXTURES / "small", multi)
    log = EventLog(redis_client, graph_name="default", key_prefix=namespace)
    assert log.read_all() == []


def test_replay_events_mode_appends_creation_events(
    multi: MultiGraph, redis_client: Redis, namespace: str
) -> None:
    """Replay imported docs as creation events so history starts clean — one
    entity_created/relation_created per doc, exact payloads, entities before
    relations, source order preserved."""
    reference = json.loads((FIXTURES / "small" / "default.json").read_text())
    summary = import_folder(FIXTURES / "small", multi, replay_events=True)
    log = EventLog(redis_client, graph_name="default", key_prefix=namespace)
    events = log.read_all()

    entity_events = [e for e in events if e.type == "entity_created"]
    relation_events = [e for e in events if e.type == "relation_created"]
    assert len(entity_events) == len(reference["nodes"]) == 30
    assert len(relation_events) == len(reference["edges"]) == 20
    assert [e.payload["entity"] for e in entity_events] == reference["nodes"]
    assert [e.payload["relation"] for e in relation_events] == reference["edges"]
    # Entities replay before relations (relations reference entity ids).
    last_entity = max(i for i, e in enumerate(events) if e.type == "entity_created")
    first_relation = min(i for i, e in enumerate(events) if e.type == "relation_created")
    assert last_entity < first_relation
    assert summary["events"] == 50


def test_replay_events_covers_every_graph(
    multi: MultiGraph, redis_client: Redis, namespace: str
) -> None:
    import_folder(FIXTURES / "multi", multi, replay_events=True)
    for name, expected in (("default", 3), ("research", 3), ("systems", 1)):
        log = EventLog(redis_client, graph_name=name, key_prefix=namespace)
        assert len(log.read_all()) == expected, name
