"""Synthesis anchor search rides the one search core.

Anchor selection used to rebuild vector search by hand: raw cosine (a score
scale nobody else uses) and no status filter, so an invalidated entity could
still anchor a synthesis. These tests pin the migrated behaviour.
"""

from __future__ import annotations

import pytest

from theloom.model import EntityCreate
from theloom.operations.synthesis import anchor_search_for
from theloom.store.multigraph import MultiGraph


class _FixedEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vector


def _seed(multi: MultiGraph, vectors: dict[str, list[float]]) -> dict[str, str]:
    store = multi.get_store()
    store.ensure_vector_index(dimension=len(next(iter(vectors.values()))))
    ids: dict[str, str] = {}
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
        store.set_entity_vector(entity.id, vector)
        ids[name] = entity.id
    return ids


def test_anchor_search_excludes_invalidated_entities(multi: MultiGraph) -> None:
    """A superseded entity keeps its embedding, so only a status filter keeps
    it from anchoring a synthesis."""
    store = multi.get_store()
    ids = _seed(multi, {"live": [1.0, 0.0], "old": [1.0, 0.0]})
    store.update_entity(ids["old"], {"status": "superseded"})

    search = anchor_search_for([store], embedder=_FixedEmbedder([1.0, 0.0]))
    hits = search("anything", 10)

    assert [hit["entityId"] for hit in hits] == [ids["live"]]


def test_anchor_search_scores_on_the_shared_scale(multi: MultiGraph) -> None:
    """1/(1+L2), the scale every other search reports — not raw cosine. A
    cosine of 0.6 is 1/(1+sqrt(0.8)) = 0.52786...; a cosine of 1.0 is 1.0."""
    ids = _seed(multi, {"exact": [1.0, 0.0], "oblique": [0.6, 0.8]})

    search = anchor_search_for([multi.get_store()], embedder=_FixedEmbedder([1.0, 0.0]))
    hits = search("anything", 10)

    by_id = {hit["entityId"]: hit["score"] for hit in hits}
    assert by_id[ids["exact"]] == pytest.approx(1.0, rel=1e-6)
    assert by_id[ids["oblique"]] == pytest.approx(0.5278640450004206, rel=1e-6)


def test_anchor_search_is_empty_without_embeddings_and_never_embeds(multi: MultiGraph) -> None:
    """A vectorless graph must deterministically fall through to the keyword
    anchor path — and must not pay for a query embedding to learn that."""
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate(
            {"name": "unembedded", "entityType": "concept", "observations": []}
        )
    )
    embedder = _FixedEmbedder([1.0, 0.0])

    hits = anchor_search_for([store], embedder=embedder)("anything", 10)

    assert hits == []
    assert embedder.calls == 0
