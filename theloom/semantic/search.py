"""Vector search over entity embeddings — the one retrieval core.

Every semantic read in the system funnels through :func:`search_entities` (text
query) or :func:`search_by_vector` (caller already holds a vector). Both share
one score scale, one status rule and one candidate-window policy, so a caller
cannot accidentally invent a *second* notion of "similar" — which is exactly
what happened while this code sat private inside ``operations/semantic.py``.

Score semantics: ``1/(1+L2)`` where ``L2 = sqrt(2-2cos)`` for L2-normalized
vectors. FalkorDB's ANN index reports cosine; the conversion happens here and
nowhere else. Hits also carry the raw ``cosine`` for callers whose thresholds
are stated on the cosine scale (near-duplicate detection).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from theloom.model import Entity, EntityStatus

Hit = dict[str, Any]

# Neither the status nor the entityType filter can be pushed into the ANN
# index, so a filtered call reads a candidate window and widens it by this
# factor whenever the window ran out before `limit` was met.
CANDIDATE_GROWTH = 4


class SupportsQueryEmbedding(Protocol):
    """The slice of the embedder the search core needs."""

    def embed_query(self, text: str) -> list[float]: ...


class SupportsVectorSearch(Protocol):
    """The slice of the store the search core needs."""

    def vector_knn(self, query_vector: list[float], k: int) -> list[tuple[str, float]]: ...

    def read_entity(self, entity_id: str) -> Entity | None: ...


@dataclass(frozen=True)
class EntityMeta:
    """What the core needs to know about a candidate to rank and filter it."""

    name: str
    entity_type: str
    active: bool


MetaResolver = Callable[[str], EntityMeta | None]


def l2_similarity(cosine: float) -> float:
    """L2-distance similarity: 1/(1+L2), with L2 = sqrt(2-2cos) for unit vectors.

    Strictly decreasing in the L2 distance, so it is order-preserving with
    respect to cosine — a threshold stated on one scale converts to the other
    by applying this function.
    """
    return 1.0 / (1.0 + math.sqrt(max(0.0, 2.0 - 2.0 * cosine)))


def store_meta_resolver(store: SupportsVectorSearch) -> MetaResolver:
    """Resolve candidate metadata by point-reading the entity, memoized per
    call site so a growing candidate window never re-reads a node."""
    cache: dict[str, EntityMeta | None] = {}

    def resolve(entity_id: str) -> EntityMeta | None:
        if entity_id not in cache:
            entity = store.read_entity(entity_id)
            cache[entity_id] = (
                None
                if entity is None
                else EntityMeta(
                    name=entity.name,
                    entity_type=entity.entity_type.value,
                    active=entity.effective_status == EntityStatus.ACTIVE,
                )
            )
        return cache[entity_id]

    return resolve


def search_by_vector(
    store: SupportsVectorSearch,
    query_vector: list[float],
    limit: int,
    min_score: float | None = None,
    entity_types: Sequence[str] | None = None,
    *,
    require_active: bool = True,
    resolve_meta: MetaResolver | None = None,
) -> list[Hit]:
    """Nearest embedded entities to ``query_vector``, scored, filtered, truncated.

    Only active entities are returned by default. A superseded or deprecated
    entity keeps its embedding (mutations invalidate, they never overwrite), so
    the index still offers it and the filter has to happen here — the same
    filter every other default read applies. Retraction is the exception: it
    drops the vector outright, so a retracted entity is not even a candidate.
    ``require_active=False`` is for the one caller that genuinely wants every
    status (near-duplicate detection compares against superseded entities too).

    No per-call full vector or entity scan. Entity metadata is a point lookup
    per candidate. The ANN window is approximate: its *scores* are exact but
    its membership and ordering are best-effort (observed on the emulated
    linux/amd64 build: a k-window holding the farthest nodes while the true
    nearest sat outside it), so candidates are re-sorted by our own computed
    score, and the only thing treated as proof of exhaustion is the index
    returning fewer than ``k`` rows. Filters the index can't answer (status,
    entityType, ``min_score`` over an unlucky window) can starve a fixed-size
    window, so the window *grows* until ``limit`` is met or the index is
    exhausted: a rare type stays findable instead of silently returning fewer
    hits than exist.
    """
    resolve = resolve_meta if resolve_meta is not None else store_meta_resolver(store)
    k = max(limit, 1)
    while True:
        candidates = store.vector_knn(query_vector, k)
        results: list[Hit] = []
        exhausted = len(candidates) < k
        scored = sorted(
            ((entity_id, cosine, l2_similarity(cosine)) for entity_id, cosine in candidates),
            key=lambda triple: -triple[2],
        )
        for entity_id, cosine, score in scored:
            if min_score is not None and score < min_score:
                break  # sorted: the rest of THIS window is below; growth decides the rest
            meta = resolve(entity_id)
            if meta is None or (require_active and not meta.active):
                continue
            if entity_types and meta.entity_type not in entity_types:
                continue
            results.append(
                {
                    "id": entity_id,
                    "score": score,
                    "cosine": cosine,
                    "metadata": {
                        "name": meta.name,
                        "entityType": meta.entity_type,
                        "entryType": "entity",
                    },
                }
            )
            if len(results) >= limit:
                break
        if exhausted or len(results) >= limit:
            return results
        k *= CANDIDATE_GROWTH


def search_entities(
    store: SupportsVectorSearch,
    query_text: str,
    limit: int,
    min_score: float | None = None,
    entity_types: Sequence[str] | None = None,
    *,
    embedder: SupportsQueryEmbedding | None = None,
    require_active: bool = True,
) -> list[Hit]:
    """Similarity search for a text query: embeds it with the QUERY prefix and
    delegates to :func:`search_by_vector`. Pass ``embedder`` to search with
    something other than the process-wide embedder."""
    resolved_embedder = embedder if embedder is not None else _default_embedder()
    return search_by_vector(
        store,
        resolved_embedder.embed_query(query_text),
        limit,
        min_score,
        entity_types,
        require_active=require_active,
    )


def _default_embedder() -> SupportsQueryEmbedding:
    from theloom.semantic.embed import get_embedder

    return get_embedder()
