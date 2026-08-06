"""CoverageGap exploration signal (embedding-ranked).

Identifies embedding-space density voids by comparing a region's internal
pairwise distances to its external distances to all other regions:

    CoverageGap = avg_external_distance / avg_internal_distance   (clamped to 1)

A high score means a region is internally dense but surrounded by distant
entities — unexplored territory between it and its neighbors.

Why this one does *not* go through :mod:`theloom.semantic.search`: the search
core answers "what is nearest to this vector", and every distance here is
all-pairs *within* and *between* known regions — there is no query and no
top-k. It shares the one cosine function, and nothing else.

Availability note: this signal degrades to a ``failedSection`` when the
embedding pipeline / vector store is unavailable. Entity vectors live in the
store (``get_entity_vectors``); when there are no embeddings the CALLER should
degrade. Use :func:`embeddings_available` to decide and
:func:`coverage_gap_unavailable` (or :data:`COVERAGE_GAP_UNAVAILABLE_MESSAGE`)
to build the failed section — that keeps the composite's ``embedding-unavailable``
anti-pattern warning firing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from theloom.composites.framework import SectionResult, failed_section, time_section
from theloom.semantic.embed import cosine_similarity

# Maximum entities per region before sampling (O(n^2) pairwise cost).
MAX_ENTITIES_PER_REGION = 500

# Message the caller uses when embeddings are unavailable (drives the composite's
# 'embedding-unavailable' warning).
COVERAGE_GAP_UNAVAILABLE_MESSAGE = "Embedding pipeline not available -- cannot compute CoverageGap"


class SupportsEntityVectors(Protocol):
    """Structural type for any store exposing entity embedding vectors."""

    def get_entity_vectors(self) -> dict[str, list[float]]:
        """Return every embedded entity's vector keyed by entity id."""
        ...


@dataclass(frozen=True)
class CoverageGapResult:
    """Per-region coverage gap output."""

    entity_ids: list[str]
    """Entity IDs belonging to this region."""
    score: float
    """Coverage gap score normalized to [0, 1]."""


def _avg_pairwise_distance(vectors: list[list[float]]) -> float:
    """Average pairwise cosine distance within a set of vectors (0 if < 2)."""
    if len(vectors) < 2:
        return 0.0
    total_distance = 0.0
    pair_count = 0
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            total_distance += 1 - cosine_similarity(vectors[i], vectors[j])
            pair_count += 1
    return total_distance / pair_count if pair_count > 0 else 0.0


def _avg_external_distance(
    internal_vectors: list[list[float]], external_vectors: list[list[float]]
) -> float:
    """Average cosine distance from internal vectors to external vectors."""
    if len(internal_vectors) == 0 or len(external_vectors) == 0:
        return 0.0
    total_distance = 0.0
    pair_count = 0
    for internal in internal_vectors:
        for external in external_vectors:
            total_distance += 1 - cosine_similarity(internal, external)
            pair_count += 1
    return total_distance / pair_count if pair_count > 0 else 0.0


def embeddings_available(store: SupportsEntityVectors) -> bool:
    """True when the store has at least one embedded entity vector."""
    return bool(store.get_entity_vectors())


def coverage_gap_unavailable() -> SectionResult:
    """A failed CoverageGap section for when embeddings are unavailable."""
    return failed_section(COVERAGE_GAP_UNAVAILABLE_MESSAGE)


def compute_coverage_gap(
    components: list[list[str]],
    store: SupportsEntityVectors,
) -> SectionResult:
    """Compute the CoverageGap signal for a set of entity regions.

    :param components: Entity-ID lists, one per region (from detect_components).
    :param store: Any store exposing ``get_entity_vectors()``.
    :returns: SectionResult ``{data: list[CoverageGapResult], durationMs, error}``.

    Regions with fewer than 2 embedded entities score 0. When a region's
    internal distance is 0 (all vectors identical), it scores 1 if any external
    vector exists else 0.
    """

    def _run() -> list[CoverageGapResult]:
        vector_map = store.get_entity_vectors()

        # Collect (optionally sampled) vectors per region.
        all_region_vectors: list[list[list[float]]] = []
        for entity_ids in components:
            vectors: list[list[float]] = []
            for entity_id in entity_ids:
                vector = vector_map.get(entity_id)
                if vector is not None and len(vectors) < MAX_ENTITIES_PER_REGION:
                    vectors.append(vector)
            all_region_vectors.append(vectors)

        results: list[CoverageGapResult] = []
        for i, entity_ids in enumerate(components):
            region_vectors = all_region_vectors[i]

            # Regions with < 2 vectors get score 0.
            if len(region_vectors) < 2:
                results.append(CoverageGapResult(entity_ids, 0.0))
                continue

            internal_dist = _avg_pairwise_distance(region_vectors)

            if internal_dist == 0:
                external_vectors: list[list[float]] = []
                for j in range(len(components)):
                    if j != i:
                        external_vectors.extend(all_region_vectors[j])
                results.append(
                    CoverageGapResult(entity_ids, 1.0 if len(external_vectors) > 0 else 0.0)
                )
                continue

            external_vectors = []
            for j in range(len(components)):
                if j != i:
                    external_vectors.extend(all_region_vectors[j])

            if len(external_vectors) == 0:
                results.append(CoverageGapResult(entity_ids, 0.0))
                continue

            external_dist = _avg_external_distance(region_vectors, external_vectors)
            raw_gap = external_dist / internal_dist
            results.append(CoverageGapResult(entity_ids, min(raw_gap, 1.0)))

        return results

    return time_section(_run)
