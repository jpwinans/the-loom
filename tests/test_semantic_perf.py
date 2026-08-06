"""Semantic layer performance fixes.

find-clusters/semantic-gaps used to re-fetch every stored vector and re-list
every entity per candidate comparison (O(n) work repeated per sampled
entity). These tests pin the vector-index-backed replacement: `_search_similar`
never full-scans, `semantic_gaps` samples spread across store order instead of
the first N, `embed_entities` skips unchanged content, and the embedder's
model cache directory is configurable so the cold-start download lands in a
stable place. No wall-clock assertions — timing is reported in the shipping
notes, not gated here."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from redis.exceptions import ResponseError

from tests.fakes import FakeEmbedder
from theloom import config as config_module
from theloom.model import EntityCreate
from theloom.operations.semantic import (
    EmbedEntitiesInput,
    SemanticGapsInput,
    SemanticSearchInput,
    WarmEmbedderInput,
    _spread_sample,
    embed_entities,
    semantic_gaps,
    semantic_search,
    warm_embedder,
)
from theloom.semantic import embed as embed_module
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph


def _seed_vectors(multi: MultiGraph, vectors: dict[str, list[float]]) -> dict[str, str]:
    store = multi.get_store()
    ids: dict[str, str] = {}
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
        store.set_entity_vector(entity.id, vector)
        ids[name] = entity.id
    return ids


# =============================================================================
# _search_similar routes through the vector index, not a full scan
# =============================================================================


def test_search_similar_never_fetches_every_vector(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_vectors(
        multi,
        {
            "close-a": [1.0, 0.0, 0.0],
            "close-b": [0.99, 0.14, 0.0],
            "far-c": [0.0, 0.0, 1.0],
        },
    )
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0, 0.0])
    )

    def _boom(self: FalkorGraphStore) -> dict[str, list[float]]:
        raise AssertionError("full vector scan should not run")

    monkeypatch.setattr(FalkorGraphStore, "get_entity_vectors", _boom)

    results = semantic_search(SemanticSearchInput(query="q", limit=2), multi)
    assert [r["name"] for r in results] == ["close-a", "close-b"]
    assert results[0]["score"] > results[1]["score"]


def test_search_similar_respects_entity_type_filter_via_overfetch(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    ids = _seed_vectors(
        multi,
        {
            "concept-a": [1.0, 0.0],
            "concept-b": [0.99, 0.1],
        },
    )
    claim = store.create_entity(
        EntityCreate.model_validate({"name": "claim-c", "entityType": "claim", "observations": []})
    )
    store.set_entity_vector(claim.id, [1.0, 0.0])
    ids["claim-c"] = claim.id
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
    )

    results = semantic_search(
        SemanticSearchInput.model_validate({"query": "q", "limit": 5, "entityType": "concept"}),
        multi,
    )
    assert {r["name"] for r in results} == {"concept-a", "concept-b"}


def test_search_similar_escalates_candidates_until_a_rare_type_is_found(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A type the ANN index ranks far below the requested limit must still be
    found: the candidate window grows until the type filter is satisfied or the
    index is exhausted — a fixed overfetch multiple would report zero hits."""
    store = multi.get_store()
    _seed_vectors(multi, {f"concept-{i:02d}": [1.0, 0.0] for i in range(40)})
    claim = store.create_entity(
        EntityCreate.model_validate(
            {"name": "rare-claim", "entityType": "claim", "observations": []}
        )
    )
    store.set_entity_vector(claim.id, [0.2, 0.98])
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
    )

    results = semantic_search(
        SemanticSearchInput.model_validate({"query": "q", "limit": 1, "entityType": "claim"}),
        multi,
    )
    assert [r["name"] for r in results] == ["rare-claim"]


def test_search_similar_excludes_non_active_entities(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retraction removes an entity from every default read. Its embedding is
    left in the index (nothing overwrites in place), so the search itself has
    to drop non-active candidates."""
    store = multi.get_store()
    ids = _seed_vectors(multi, {"live": [1.0, 0.0], "gone": [0.99, 0.1]})
    store.delete_entity(ids["gone"])
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
    )

    results = semantic_search(SemanticSearchInput(query="q", limit=10), multi)
    assert [r["name"] for r in results] == ["live"]


def test_search_similar_min_score_stops_at_first_below_threshold(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidates arrive in descending similarity order, so the first one under
    the threshold ends the scan — nothing past it is even resolved."""
    _seed_vectors(
        multi,
        {
            "near": [1.0, 0.0],
            "far": [0.0, 1.0],
        },
    )
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
    )
    reads: list[str] = []
    read_entity = FalkorGraphStore.read_entity

    def _counting_read(self: FalkorGraphStore, entity_id: str) -> Any:
        reads.append(entity_id)
        return read_entity(self, entity_id)

    monkeypatch.setattr(FalkorGraphStore, "read_entity", _counting_read)

    results = semantic_search(
        SemanticSearchInput.model_validate({"query": "q", "limit": 10, "minScore": 0.9}),
        multi,
    )
    assert [r["name"] for r in results] == ["near"]
    assert len(reads) == 1  # the sub-threshold candidate was never resolved


# =============================================================================
# The entity vector index takes its shape from stored vectors, not from callers
# =============================================================================


def test_vector_knn_indexes_the_stored_dimension_not_the_query_dimension(
    multi: MultiGraph,
) -> None:
    store = multi.get_store()
    ids = _seed_vectors(multi, {"a": [1.0, 0.0, 0.0]})
    with pytest.raises(ResponseError, match="dimension mismatch"):
        store.vector_knn([1.0, 0.0], 5)  # a wrong-width query must not shape the index
    assert store.vector_index_dimension() == 3
    assert [entity_id for entity_id, _ in store.vector_knn([1.0, 0.0, 0.0], 5)] == [ids["a"]]


def test_vector_knn_without_any_embeddings_creates_no_index(multi: MultiGraph) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate({"name": "bare", "entityType": "concept", "observations": []})
    )
    assert store.vector_knn([1.0, 0.0], 5) == []
    assert store.vector_index_dimension() is None


def test_ensure_vector_index_surfaces_a_real_failure(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create that fails for any reason other than the index already being
    there is raised, not swallowed — a silently missing index means every
    embedding written afterwards is unsearchable."""
    store = multi.get_store()
    query = store._query

    def _boom(cypher: str, params: dict[str, Any] | None = None) -> Any:
        if cypher.startswith("CREATE VECTOR INDEX"):
            raise RuntimeError("index create failed")
        return query(cypher, params)

    monkeypatch.setattr(store, "_query", _boom)
    with pytest.raises(RuntimeError, match="index create failed"):
        store.ensure_vector_index()
    assert store.vector_index_dimension() is None


# =============================================================================
# semantic_gaps: deterministic spread sample, not first-N
# =============================================================================


def test_spread_sample_is_not_a_prefix() -> None:
    items = list(range(20))
    sample = _spread_sample(items, 5)
    assert len(sample) == 5
    assert sample != items[:5]
    assert min(sample) < 10 <= max(sample)  # spans the full range


def test_spread_sample_returns_everything_when_under_the_cap() -> None:
    items = list(range(4))
    assert _spread_sample(items, 10) == items


def test_spread_sample_seed_is_reproducible_and_shifts_phase() -> None:
    items = list(range(20))
    first = _spread_sample(items, 5, seed=1)
    again = _spread_sample(items, 5, seed=1)
    other = _spread_sample(items, 5, seed=2)
    assert first == again
    assert len(first) == 5 == len(set(first))
    assert first != other


def test_semantic_gaps_samples_spread_across_store_order(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    names = [f"entity-{i:02d}" for i in range(20)]
    for name in names:
        store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )

    seen: list[str] = []

    def _fake_search_similar(
        store: Any,
        query_text: str,
        limit: int,
        min_score: float | None = None,
        entity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        seen.append(query_text.split()[0])
        return []

    monkeypatch.setattr("theloom.operations.semantic._search_similar", _fake_search_similar)
    semantic_gaps(SemanticGapsInput.model_validate({"maxEntities": 5}), multi)

    assert len(seen) == 5
    assert seen != names[:5]
    indices = sorted(names.index(name) for name in seen)
    assert indices[0] < 10 <= indices[-1]


def test_semantic_gaps_reports_partners_outside_the_sample(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sample decides which entities are *probed*, not which may be the
    other end of a gap. A sampled entity's nearest neighbour usually sits in an
    unsampled stride slot, so requiring both ends in the sample throws the
    result away."""
    vectors = {
        "pair-a": [1.0, 0.0],
        "pair-b": [0.999, 0.045],
        "other-c": [0.0, 1.0],
        "other-d": [0.045, 0.999],
    }
    ids = _seed_vectors(multi, vectors)
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder",
        lambda: FakeEmbedder(vectors),
    )

    gaps = semantic_gaps(SemanticGapsInput.model_validate({"maxEntities": 2}), multi)

    pairs = {frozenset([g["entityA"]["id"], g["entityB"]["id"]]) for g in gaps}
    assert frozenset([ids["pair-a"], ids["pair-b"]]) in pairs
    for gap in gaps:
        for side in ("entityA", "entityB"):
            assert gap[side]["name"] in vectors
            assert gap[side]["entityType"] == "concept"


# =============================================================================
# embed_entities: content-hash skip (already implemented — pinned here)
# =============================================================================


def test_embed_entities_skips_unchanged_content_hash(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate(
            {"name": "stable", "entityType": "concept", "observations": ["obs"]}
        )
    )
    embedder = FakeEmbedder([1.0, 0.0, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)

    first = embed_entities(EmbedEntitiesInput(), multi)
    assert first["completed"] == 1
    assert first["skipped"] == 0
    assert embedder.document_calls == 1

    second = embed_entities(EmbedEntitiesInput(), multi)
    assert second["completed"] == 0
    assert second["skipped"] == 1
    assert embedder.document_calls == 1  # no redundant re-embed


def test_embed_entities_reembeds_after_force_flag(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate(
            {"name": "stable", "entityType": "concept", "observations": ["obs"]}
        )
    )
    embedder = FakeEmbedder([1.0, 0.0, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)

    embed_entities(EmbedEntitiesInput(), multi)
    forced = embed_entities(EmbedEntitiesInput.model_validate({"forceReembed": True}), multi)
    assert forced["completed"] == 1
    assert embedder.document_calls == 2


# =============================================================================
# warm-embedder
# =============================================================================


def test_warm_embedder_runs_one_query_and_reports_model(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = FakeEmbedder([1.0, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)
    result = warm_embedder(WarmEmbedderInput(), multi)
    assert embedder.query_calls == 1
    assert result["warm"] is True
    assert result["model"] == embed_module.EMBEDDING_VERSION
    assert result["dimensions"] == embed_module.EMBEDDING_DIMENSIONS
    assert isinstance(result["cacheDir"], str) and result["cacheDir"]


def test_warm_embedder_registered_in_cli() -> None:
    from theloom.cli.registry import COMMANDS

    descriptor = next(c for c in COMMANDS if c.name == "warm-embedder")
    assert descriptor.handler is warm_embedder
    assert descriptor.allow_empty is True


# =============================================================================
# Embedder model cache directory (config-driven)
# =============================================================================


def test_get_embedder_passes_configured_cache_dir_to_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # get_embedder() dispatches to the lru_cached real embedder builder
    # (_default_embedder) once no override is installed; clear its cache so
    # this test's config patch is what actually builds the next instance.
    embed_module._default_embedder.cache_clear()
    monkeypatch.setattr(
        embed_module,
        "load_config",
        lambda: SimpleNamespace(model_cache_dir="/tmp/loom-model-cache-test"),
    )
    captured: dict[str, Any] = {}

    class _FakeModel:
        def __init__(self, model_name: str, cache_dir: str | None = None, **kwargs: Any) -> None:
            captured["model_name"] = model_name
            captured["cache_dir"] = cache_dir

        def embed(self, texts: list[str]) -> list[np.ndarray]:
            return [np.ones(3, dtype=np.float32) for _ in texts]

    monkeypatch.setattr("fastembed.TextEmbedding", _FakeModel)
    try:
        embedder = embed_module.get_embedder()
        embedder.embed_query("hello")
        assert captured["cache_dir"] == "/tmp/loom-model-cache-test"
        assert captured["model_name"] == embed_module.MODEL_ID
    finally:
        embed_module._default_embedder.cache_clear()


# =============================================================================
# Embedder injection: one config-level override every call site defers to
# =============================================================================


def test_get_embedder_prefers_the_config_installed_override() -> None:
    fake = FakeEmbedder([1.0, 0.0])
    config_module.set_embedder_override(fake)
    try:
        assert embed_module.get_embedder() is fake
    finally:
        config_module.set_embedder_override(None)
    assert config_module.get_embedder_override() is None


# =============================================================================
# The ANN window is approximate: membership and ordering are best-effort
# =============================================================================
# Observed on the emulated linux/amd64 FalkorDB build: a k-window can hold the
# FARTHEST nodes (correct scores, inverted membership) while the true nearest
# sit outside it. _search_similar must therefore rank by its own computed
# score and keep growing the window rather than trusting engine order, and
# must not treat a window full of below-threshold candidates as exhaustion.


def test_search_similar_recovers_when_the_knn_window_is_inverted(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_vectors(
        multi,
        {
            "far-a": [1.0, 0.0, 0.0, 0.0],
            "far-b": [1.0, 0.0, 0.0, 0.0],
            "near-c": [0.0, 1.0, 0.0, 0.0],
            "near-d": [0.0, 1.0, 0.0, 0.0],
        },
    )
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder",
        lambda: FakeEmbedder([0.0, 1.0, 0.0, 0.0]),
    )

    # vector_knn returns (id, cosine similarity). Similarity to the query:
    # the near pair is identical (1.0), the far pair orthogonal (0.0).
    similarity_by_id = {
        ids["far-a"]: 0.0,
        ids["far-b"]: 0.0,
        ids["near-c"]: 1.0,
        ids["near-d"]: 1.0,
    }
    windows: list[int] = []

    def inverted_window(
        self: FalkorGraphStore, query_vector: list[float], k: int
    ) -> list[tuple[str, float]]:
        # Farthest-first membership with EXACT similarities — the pathology as
        # measured. A window smaller than the population never contains the
        # true nearest; only the full-population window does.
        windows.append(k)
        ranked = sorted(similarity_by_id.items(), key=lambda pair: pair[1])
        if k < len(ranked):
            return ranked[:k]
        return sorted(similarity_by_id.items(), key=lambda pair: -pair[1])

    # Class-level: MultiGraph.get_store returns a fresh instance per call, so
    # an instance-level patch would never reach the store the search uses.
    monkeypatch.setattr(FalkorGraphStore, "vector_knn", inverted_window)
    results = semantic_search(
        SemanticSearchInput.model_validate({"query": "q", "limit": 2, "minScore": 0.5}),
        multi,
    )
    assert sorted(r["name"] for r in results) == ["near-c", "near-d"]
    assert len(windows) > 1, "the window must have grown past the inverted first read"
