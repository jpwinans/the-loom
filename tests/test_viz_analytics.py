"""Analytics section assembly tests. Seeds a 3-node line graph with one causal
loop (a->b->a) and asserts each analytics field is populated with the shapes
the operations already emit. Also covers the Phase 5 guardrails (betweenness
omission and loop-skip above an injected threshold) — always exercised on a
small seeded graph with the module constant monkeypatched low, never by
building a large graph."""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz import analytics
from theloom.viz.analytics import assemble_analytics


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_assembles_all_fields(multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": b.id, "to": a.id, "relationType": "inhibits"})
    )
    section = assemble_analytics(None, multi)
    assert set(section.centrality.keys()) == {"degree", "betweenness", "pagerank"}
    assert a.id in section.centrality["degree"]
    assert any({a.id, b.id} <= set(component) for component in section.components)
    assert len(section.loops) >= 1  # a->b->a is a feedback loop
    assert section.leverage_points == []
    assert section.bridges == []


def _seed_causal_pair(multi: MultiGraph) -> tuple[str, str]:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": b.id, "to": a.id, "relationType": "inhibits"})
    )
    return a.id, b.id


def test_betweenness_omitted_above_threshold(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analytics, "BETWEENNESS_MAX_NODES", 1)
    _seed_causal_pair(multi)  # 2 entities > the injected threshold of 1
    section = assemble_analytics(None, multi)
    assert set(section.centrality.keys()) == {"degree", "pagerank"}
    assert "betweenness" not in section.centrality


def test_betweenness_present_at_or_under_threshold(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analytics, "BETWEENNESS_MAX_NODES", 2)
    _seed_causal_pair(multi)  # 2 entities == the injected threshold
    section = assemble_analytics(None, multi)
    assert set(section.centrality.keys()) == {"degree", "betweenness", "pagerank"}


def test_loops_skipped_above_threshold(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analytics, "LOOP_MAX_NODES", 1)
    _seed_causal_pair(multi)  # a->b->a is a loop, but 2 entities > threshold of 1
    section = assemble_analytics(None, multi)
    assert section.loops == []


def test_loops_present_at_or_under_threshold(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analytics, "LOOP_MAX_NODES", 2)
    _seed_causal_pair(multi)
    section = assemble_analytics(None, multi)
    assert len(section.loops) >= 1


def test_centrality_ship_limit_trims_scores(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analytics, "CENTRALITY_SHIP_LIMIT", 1)
    _seed_causal_pair(multi)  # 2 entities, limit trims each algorithm to 1
    section = assemble_analytics(None, multi)
    assert len(section.centrality["degree"]) == 1
    assert len(section.centrality["pagerank"]) == 1
