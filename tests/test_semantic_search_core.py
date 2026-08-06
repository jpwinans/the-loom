"""The public vector-search seam (``theloom.semantic.search``).

These tests drive the search core directly — a live store plus an injected
fake embedder — rather than through a command, so the contract that every
caller depends on (score scale, active-only filtering, entity-type filter,
candidate-window growth) is pinned once instead of once per caller.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeEmbedder
from theloom.model import EntityCreate, RelationCreate
from theloom.operations.semantic import HybridSearchInput, hybrid_search
from theloom.semantic.search import search_entities
from theloom.store.multigraph import MultiGraph


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
        multi.get_store(), "anything", limit=10, embedder=FakeEmbedder([1.0, 0.0])
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
    embedder = FakeEmbedder([1.0, 0.0])

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
        store, "q", limit=1, entity_types=["claim"], embedder=FakeEmbedder([1.0, 0.0])
    )

    assert [hit["metadata"]["name"] for hit in hits] == ["rare-claim"]
    assert hits[0]["metadata"]["entityType"] == "claim"


def test_search_reports_the_raw_cosine_alongside_the_l2_score(multi: MultiGraph) -> None:
    """Near-duplicate detection states its threshold on the cosine scale, so
    the core carries the cosine through instead of forcing a second scan."""
    _seed(multi, {"oblique": [0.6, 0.8]})

    hits = search_entities(multi.get_store(), "q", limit=1, embedder=FakeEmbedder([1.0, 0.0]))

    assert hits[0]["cosine"] == pytest.approx(0.6, rel=1e-6)
    assert hits[0]["score"] == pytest.approx(0.5278640450004206, rel=1e-6)


# =============================================================================
# hybrid-search end to end: fetch through the core, then the ranking stages
# =============================================================================


def test_hybrid_search_fuses_vector_keyword_and_graph_signals(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One worked example through the whole command.

    Vector: 'alpha signal' at cosine 1.0 scores 1.0, 'beta noise' at cosine 0.6
    scores 1/(1+sqrt(0.8)) = 0.527864. Keyword: the query term 'alpha' matches
    1 of 1 term in 'alpha signal' only. Seeds: ceil(2*0.5) = 1, so only
    'alpha signal' expands, reaching 'gamma neighbour' at hop 1 (graph 1.0).
    Fused at 0.6/0.25/0.15: 0.6+0.25 = 0.85, 0.6*0.527864 = 0.316718, 0.15.
    Gaps 0.5333/0.1667 average 0.35, so the 'similar' threshold is 0.7875 and
    nothing splits: one quality group holding all three.
    """
    store = multi.get_store()
    ids = _seed(multi, {"alpha signal": [1.0, 0.0], "beta noise": [0.6, 0.8]})
    gamma = store.create_entity(
        EntityCreate.model_validate(
            {"name": "gamma neighbour", "entityType": "concept", "observations": []}
        )
    )
    store.create_relation(
        RelationCreate.model_validate(
            {"from": ids["alpha signal"], "to": gamma.id, "relationType": "related_to"}
        )
    )
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
    )

    result = hybrid_search(HybridSearchInput.model_validate({"query": "alpha"}), multi)

    assert [row["name"] for row in result["results"]] == [
        "alpha signal",
        "beta noise",
        "gamma neighbour",
    ]
    assert [row["score"] for row in result["results"]] == [
        pytest.approx(0.85, rel=1e-6),
        pytest.approx(0.31671842700025236, rel=1e-6),
        pytest.approx(0.15),
    ]
    assert [row["matchSource"] for row in result["results"]] == [
        "semantic+keyword",
        "semantic",
        "graph",
    ]
    assert result["results"][0]["matchedTerms"] == ["alpha"]
    assert result["results"][2]["hopDistance"] == 1
    assert result["results"][2]["expandedFrom"] == ids["alpha signal"]
    assert {row["qualityGroup"] for row in result["results"]} == {1}
    assert result["qualityGroups"] == 1
    assert result["totalCandidates"] == 3
    assert result["query"] == {
        "text": "alpha",
        "weights": {"vector": 0.6, "keyword": 0.25, "graph": 0.15},
        "graphHops": 1,
        "qualityGrouping": True,
    }
