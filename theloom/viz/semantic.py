"""Semantic section — 2D projection of entity embedding vectors plus semantic
clusters.

The projection is numpy PCA by default; UMAP is an optional upgrade (the
`viz-umap` extra) used when installed and the graph has enough vectors.
Clusters reuse the existing find-clusters operation over the same graph, so
the map's hulls match what `loom find-clusters` reports."""

from __future__ import annotations

from collections import Counter

import numpy as np

from theloom.operations.semantic import FindClustersInput, find_clusters
from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import SemanticCluster, SemanticSection

_MIN_VECTORS = 3
_UMAP_MIN_VECTORS = 10  # UMAP needs a non-trivial neighbourhood; below this PCA is more faithful
_UMAP_SEED = 42


def _assemble_clusters(graph: str | None, multi: MultiGraph) -> list[SemanticCluster] | None:
    result = find_clusters(FindClustersInput(graph=graph), multi)
    clusters: list[SemanticCluster] = []
    for cluster in result["clusters"]:
        members = cluster["entities"]
        label = Counter(m["entityType"] for m in members).most_common(1)[0][0]
        clusters.append(
            SemanticCluster(
                id=int(cluster["id"]),
                label=label,
                entityIds=[m["id"] for m in members],
                size=int(cluster["size"]),
            )
        )
    return clusters or None


def _pca_project(matrix: np.ndarray) -> np.ndarray:
    centered = matrix - matrix.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    return np.asarray(centered @ vt[:2].T, dtype=np.float64)


def _umap_project(matrix: np.ndarray) -> np.ndarray | None:
    """Seeded 2D UMAP, or None when umap-learn is not installed."""
    try:
        import umap
    except ImportError:
        return None
    n = matrix.shape[0]
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=min(15, n - 1),
        min_dist=0.1,
        metric="cosine",
        random_state=_UMAP_SEED,
    )
    return np.asarray(reducer.fit_transform(matrix), dtype=np.float64)


def assemble_semantic(graph: str | None, multi: MultiGraph) -> SemanticSection | None:
    vectors = multi.get_store(graph).get_entity_vectors()
    if len(vectors) < _MIN_VECTORS:
        return None
    ids = list(vectors.keys())
    matrix = np.array([vectors[entity_id] for entity_id in ids], dtype=np.float64)

    coords: np.ndarray | None = None
    method = "pca"
    if len(vectors) >= _UMAP_MIN_VECTORS:
        coords = _umap_project(matrix)
        if coords is not None:
            method = "umap"
    if coords is None:
        coords = _pca_project(matrix)

    projection = {
        entity_id: [round(float(x), 4), round(float(y), 4)]
        for entity_id, (x, y) in zip(ids, coords, strict=True)
    }
    clusters = _assemble_clusters(graph, multi)
    return SemanticSection(method=method, projection=projection, clusters=clusters)
