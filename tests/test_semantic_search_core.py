"""The public vector-search seam (``theloom.semantic.search``).

These tests drive the search core directly — a live store plus an injected
fake embedder — rather than through a command, so the contract that every
caller depends on (score scale, active-only filtering, entity-type filter,
candidate-window growth) is pinned once instead of once per caller.
"""

from __future__ import annotations

import pytest

from theloom.model import EntityCreate
from theloom.semantic.search import search_entities
from theloom.store.multigraph import MultiGraph


class _FixedEmbedder:
    """embed_query returns one fixed vector regardless of the query text."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vector


def _seed(
    multi: MultiGraph, vectors: dict[str, list[float]], entity_type: str = "concept"
) -> dict[str, str]:
    store = multi.get_store()
    ids: dict[str, str] = {}
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate(
                {"name": name, "entityType": entity_type, "observations": []}
            )
        )
        store.set_entity_vector(entity.id, vector)
        ids[name] = entity.id
    return ids


def test_search_scores_are_l2_similarity_not_cosine(multi: MultiGraph) -> None:
    """1/(1+L2) with L2 = sqrt(2-2cos) for unit vectors: a cosine of 1.0 scores
    1.0, and a cosine of 0.6 scores 1/(1+sqrt(0.8)) = 0.52786... — worked out
    by hand, not recomputed the way the code computes it."""
    _seed(multi, {"exact": [1.0, 0.0], "oblique": [0.6, 0.8]})

    hits = search_entities(
        multi.get_store(), "anything", limit=10, embedder=_FixedEmbedder([1.0, 0.0])
    )

    by_name = {hit["metadata"]["name"]: hit for hit in hits}
    assert by_name["exact"]["score"] == pytest.approx(1.0, rel=1e-6)
    assert by_name["oblique"]["score"] == pytest.approx(0.5278640450004206, rel=1e-6)
    assert [hit["metadata"]["name"] for hit in hits] == ["exact", "oblique"]


def test_search_drops_non_active_entities_unless_asked_for_them(multi: MultiGraph) -> None:
    """A superseded entity keeps its vector (updates invalidate, they never
    overwrite), so the index still offers it and the core has to filter. The
    near-duplicate caller genuinely wants those candidates, and says so."""
    store = multi.get_store()
    ids = _seed(multi, {"live": [1.0, 0.0], "old": [0.99, 0.14]})
    store.update_entity(ids["old"], {"status": "superseded"})
    embedder = _FixedEmbedder([1.0, 0.0])

    active_only = search_entities(store, "q", limit=10, embedder=embedder)
    every_status = search_entities(store, "q", limit=10, embedder=embedder, require_active=False)

    assert [hit["metadata"]["name"] for hit in active_only] == ["live"]
    assert [hit["metadata"]["name"] for hit in every_status] == ["live", "old"]


def test_search_grows_the_candidate_window_until_a_rare_type_surfaces(multi: MultiGraph) -> None:
    """The type filter cannot be pushed into the ANN index. A rare type ranked
    far below the requested limit must still be found rather than silently
    reported as absent."""
    store = multi.get_store()
    _seed(multi, {f"concept-{i:02d}": [1.0, 0.0] for i in range(40)})
    rare = store.create_entity(
        EntityCreate.model_validate(
            {"name": "rare-claim", "entityType": "claim", "observations": []}
        )
    )
    store.set_entity_vector(rare.id, [0.2, 0.98])

    hits = search_entities(
        store, "q", limit=1, entity_types=["claim"], embedder=_FixedEmbedder([1.0, 0.0])
    )

    assert [hit["metadata"]["name"] for hit in hits] == ["rare-claim"]
    assert hits[0]["metadata"]["entityType"] == "claim"


def test_search_reports_the_raw_cosine_alongside_the_l2_score(multi: MultiGraph) -> None:
    """Near-duplicate detection states its threshold on the cosine scale, so
    the core carries the cosine through instead of forcing a second scan."""
    _seed(multi, {"oblique": [0.6, 0.8]})

    hits = search_entities(multi.get_store(), "q", limit=1, embedder=_FixedEmbedder([1.0, 0.0]))

    assert hits[0]["cosine"] == pytest.approx(0.6, rel=1e-6)
    assert hits[0]["score"] == pytest.approx(0.5278640450004206, rel=1e-6)
