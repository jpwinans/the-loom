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
from falkordb import FalkorDB
from redis import Redis

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


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


class _StubEmbedder:
    """embed_query returns a fixed vector regardless of text."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.query_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vector


class _CountingDocumentEmbedder:
    """embed_document counts calls, so a redundant re-embed is visible."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.document_calls = 0

    def embed_document(self, text: str) -> list[float]:
        self.document_calls += 1
        return self._vector

    def embed_query(self, text: str) -> list[float]:
        return self._vector


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
        "theloom.operations.semantic.get_embedder", lambda: _StubEmbedder([1.0, 0.0, 0.0])
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
        "theloom.operations.semantic.get_embedder", lambda: _StubEmbedder([1.0, 0.0])
    )

    results = semantic_search(
        SemanticSearchInput.model_validate({"query": "q", "limit": 5, "entityType": "concept"}),
        multi,
    )
    assert {r["name"] for r in results} == {"concept-a", "concept-b"}


def test_search_similar_min_score_stops_at_first_below_threshold(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_vectors(
        multi,
        {
            "near": [1.0, 0.0],
            "far": [0.0, 1.0],
        },
    )
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: _StubEmbedder([1.0, 0.0])
    )
    results = semantic_search(
        SemanticSearchInput.model_validate({"query": "q", "limit": 10, "minScore": 0.9}),
        multi,
    )
    assert [r["name"] for r in results] == ["near"]


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
    embedder = _CountingDocumentEmbedder([1.0, 0.0, 0.0])
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
    embedder = _CountingDocumentEmbedder([1.0, 0.0, 0.0])
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
    embedder = _StubEmbedder([1.0, 0.0])
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
    embed_module.get_embedder.cache_clear()
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
        embed_module.get_embedder.cache_clear()
