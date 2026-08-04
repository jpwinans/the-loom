"""Gap-Fill-Cycle composite: the commitThreshold path must actually commit.

In template mode (no embedding pipeline wired into this composite) semantic
consistency always reports ``{"score": 0, "status": "skipped"}`` — "not
evaluated", not "failed". The commit gate used to require
``semanticConsistency.status == "pass"``, which that value can never satisfy,
so passing ``commitThreshold`` could never create a relation regardless of
input. "skipped" must not veto a commit; only an explicit "fail" may.
"""

from __future__ import annotations

from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.gap_fill_cycle import GapFillCycleInput, gap_fill_cycle
from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


class _StubEmbedder:
    """embed_query returns a fixed vector regardless of text (no real model)."""

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _seed_close_pair(multi: MultiGraph) -> tuple[str, str]:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate(
            {"name": "alpha", "entityType": "concept", "observations": ["alpha obs"]}
        )
    )
    b = store.create_entity(
        EntityCreate.model_validate(
            {"name": "beta", "entityType": "concept", "observations": ["beta obs"]}
        )
    )
    store.set_entity_vector(a.id, [1.0, 0.0, 0.0])
    store.set_entity_vector(b.id, [0.99, 0.14, 0.0])
    return a.id, b.id


def test_commit_threshold_zero_actually_commits(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_id, b_id = _seed_close_pair(multi)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: _StubEmbedder())

    result = gap_fill_cycle(
        GapFillCycleInput.model_validate({"seedEntity": a_id, "commitThreshold": 0}),
        multi,
    )

    assert result["metadata"]["committed"] >= 1
    relations = multi.get_store().list_relations()
    assert any(r.from_ == a_id and r.to == b_id for r in relations)


def test_commit_threshold_does_not_commit_when_structural_gate_fails(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commitThreshold that would otherwise be met must still not commit
    when the structural gate (constraint/invariant) fails — the "skipped"
    fix must not turn the gate into an unconditional pass-through."""
    a_id, b_id = _seed_close_pair(multi)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: _StubEmbedder())

    def _failing_verify_graph(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"pass": False, "tier1": None, "tier2": None, "tier2Skipped": True}

    monkeypatch.setattr("theloom.composites.gap_fill_cycle.verify_graph", _failing_verify_graph)

    result = gap_fill_cycle(
        GapFillCycleInput.model_validate({"seedEntity": a_id, "commitThreshold": 0}),
        multi,
    )

    assert result["metadata"]["committed"] == 0
    relations = multi.get_store().list_relations()
    assert not any(r.from_ == a_id and r.to == b_id for r in relations)
