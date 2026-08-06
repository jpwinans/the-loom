"""The deduplication gate's embedding path against a live store.

Near-duplicate detection is a nearest-neighbour question, so it goes through
the shared search core rather than pulling every stored vector and comparing
them in Python. Its threshold stays on the cosine scale, and unlike every
other search it deliberately compares against entities of *every* status —
a proposal that duplicates a superseded entity is still a duplicate.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.model import EntityCreate
from theloom.semantic.deduplication_gate import deduplicate_proposals, proposal_to_text
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]


class _MappedEmbeddingManager:
    """generate_embedding returns the vector registered for a given text."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def generate_embedding(self, text: str) -> list[float]:
        return self._vectors[text]


def _proposal(name: str, entity_type: str) -> Doc:
    return {
        "entity": {"name": name, "entityType": entity_type, "observations": ["obs"]},
        "relations": [],
        "rationale": "because",
        "confidence": 0.8,
        "strategy": "pattern_completion",
    }


def _seed(store: FalkorGraphStore, name: str, entity_type: str, vector: list[float]) -> str:
    entity = store.create_entity(
        EntityCreate.model_validate({"name": name, "entityType": entity_type, "observations": []})
    )
    store.set_entity_vector(entity.id, vector)
    return entity.id


def test_dedup_uses_the_vector_index_not_a_full_vector_scan(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    store.ensure_vector_index(dimension=2)
    widget = _seed(store, "Existing Widget", "concept", [1.0, 0.0])
    _seed(store, "Unrelated Claim", "claim", [1.0, 0.0])

    def _boom(self: FalkorGraphStore) -> dict[str, list[float]]:
        raise AssertionError("full vector scan should not run")

    monkeypatch.setattr(FalkorGraphStore, "get_entity_vectors", _boom)

    proposal = _proposal("Fresh Name", "concept")
    manager = _MappedEmbeddingManager({proposal_to_text(proposal): [1.0, 0.0]})

    result = deduplicate_proposals([proposal], manager, store)

    assert result["accepted"] == []
    assert len(result["rejected"]) == 1
    assert result["matches"][0]["existingEntityId"] == widget
    assert result["matches"][0]["similarity"] == pytest.approx(1.0, rel=1e-6)


def test_dedup_still_matches_against_invalidated_entities(multi: MultiGraph) -> None:
    """Deliberate exception to the active-only rule: proposing something that
    duplicates a superseded entity is still a duplicate proposal."""
    store = multi.get_store()
    store.ensure_vector_index(dimension=2)
    old = _seed(store, "Superseded Widget", "concept", [1.0, 0.0])
    store.update_entity(old, {"status": "superseded"})

    proposal = _proposal("Fresh Name", "concept")
    manager = _MappedEmbeddingManager({proposal_to_text(proposal): [1.0, 0.0]})

    result = deduplicate_proposals([proposal], manager, store)

    assert result["matches"][0]["existingEntityId"] == old
    assert len(result["rejected"]) == 1
