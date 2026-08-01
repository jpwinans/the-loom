"""Temporal section tests: events appear in order with ISO timestamps."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.temporal import assemble_temporal


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_event_log_accessor(multi: MultiGraph, namespace: str) -> None:
    log = multi.event_log()
    assert log.key == f"{namespace}:default:events"


def test_temporal_section_replays_mutations(multi: MultiGraph) -> None:
    store = multi.get_store()
    entity = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    store.update_entity(entity.id, {"name": "a2"})
    section = assemble_temporal(None, multi)
    types = [event.type for event in section.events]
    assert types == ["entity_created", "entity_updated"]
    assert section.events[0].at.endswith("+00:00")
    assert section.events[0].payload["entity"]["id"] == entity.id
