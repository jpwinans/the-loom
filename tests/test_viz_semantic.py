"""Semantic projection + cluster tests using synthetic vectors (no fastembed).

find_clusters embeds each entity's text as a query; a deterministic stub maps
that query text back to the entity's seeded vector, so cluster membership is a
pure function of the seeded geometry — CI never downloads the model."""

from __future__ import annotations

import numpy as np
import pytest

from tests.fakes import FakeEmbedder
from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.semantic import assemble_semantic


def _seed(multi: MultiGraph, vectors: dict[str, list[float]]) -> dict[str, str]:
    store = multi.get_store()
    ids: dict[str, str] = {}
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
        store.set_entity_vector(entity.id, vector)
        ids[name] = entity.id
    return ids


def _install_stub(monkeypatch: pytest.MonkeyPatch, vectors: dict[str, list[float]]) -> None:
    stub = FakeEmbedder(vectors)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: stub)


def test_none_when_too_few_vectors(multi: MultiGraph) -> None:
    assert assemble_semantic(None, multi) is None


def test_pca_projection_shape_and_no_clusters_when_dissimilar(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    vectors = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": [0.0, 1.0, 0.0, 0.0],
        "c": [0.0, 0.0, 1.0, 0.0],
        "d": [1.0, 1.0, 0.0, 0.0],
    }
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "pca"  # 4 vectors < UMAP threshold
    assert len(section.projection) == 4
    assert all(len(point) == 2 for point in section.projection.values())
    assert section.clusters is None  # nothing is similar enough to group


def test_clusters_from_similar_vectors(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two tight pairs, mutually orthogonal: {a,b} and {c,d}.
    vectors = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": [1.0, 0.0, 0.0, 0.0],
        "c": [0.0, 1.0, 0.0, 0.0],
        "d": [0.0, 1.0, 0.0, 0.0],
    }
    _install_stub(monkeypatch, vectors)
    ids = _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.clusters is not None
    grouped = {frozenset(cluster.entity_ids) for cluster in section.clusters}
    assert grouped == {
        frozenset({ids["a"], ids["b"]}),
        frozenset({ids["c"], ids["d"]}),
    }
    for cluster in section.clusters:
        assert cluster.size == 2
        assert cluster.label == "concept"  # dominant entity type


def _seeded_vectors(count: int, dims: int = 16) -> dict[str, list[float]]:
    rng = np.random.default_rng(7)  # deterministic synthetic embeddings
    return {f"e{i}": rng.standard_normal(dims).tolist() for i in range(count)}


def test_pca_below_umap_threshold(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    vectors = _seeded_vectors(6)
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "pca"  # 6 < _UMAP_MIN_VECTORS, PCA even if umap installed


def test_umap_when_available(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("umap")  # skipped in CI (viz-umap not installed)
    vectors = _seeded_vectors(12)
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    section = assemble_semantic(None, multi)
    assert section is not None
    assert section.method == "umap"
    assert len(section.projection) == 12
    assert all(len(point) == 2 for point in section.projection.values())


def test_umap_is_deterministic(multi: MultiGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("umap")
    vectors = _seeded_vectors(12)
    _install_stub(monkeypatch, vectors)
    _seed(multi, vectors)
    first = assemble_semantic(None, multi)
    second = assemble_semantic(None, multi)
    assert first is not None and second is not None
    assert first.projection == second.projection
