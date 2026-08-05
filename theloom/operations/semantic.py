"""Semantic operations.

Score semantics: every similarity that
flows through vector search is ``1/(1+L2distance)`` — the vector store returns L2, and
for L2-normalized vectors L2 = sqrt(2 - 2*cos) — NOT plain cosine. Query texts
for discovery are ``name obs…`` (no [type] prefix) embedded with the QUERY
prefix. Hybrid fusion uses raw (un-normalized) weights .6/.25/.15 over vector/
keyword/graph signals, recency decays by half-life before MMR, and quality
groups split on score gaps (mean*1.5*strategy, min 0.05).

The one-shot CLI never accumulates queue state, so flush/retry/dead-letters/
pipelineStatus report empty-queue shapes.

Tier note: document vectors are stored verbatim, but live query vectors come
from fastembed — so ranked outputs are embedding-ranked, while
embed-*/status shapes are fully deterministic.
"""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import Field

from theloom.config import load_config
from theloom.errors import NotFoundError
from theloom.graph.metadata import coerce_observation
from theloom.model import ALL_RELATION_TYPES, EntityFilter, EntityStatus, EntityType
from theloom.operations.common import CommandInput, UuidStr
from theloom.semantic.embed import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_VERSION,
    build_embedding_text,
    compute_content_hash,
    get_embedder,
)
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]

DEFAULT_WEIGHTS = {"vector": 0.6, "keyword": 0.25, "graph": 0.15}
STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "not",
        "no",
        "this",
        "that",
        "it",
        "its",
        "as",
        "if",
        "so",
        "than",
        "can",
        "will",
        "may",
    }
)
EMPTY_PROGRESS = {
    "total": 0,
    "completed": 0,
    "skipped": 0,
    "failed": 0,
    "currentBatch": 0,
    "totalBatches": 1,
}


def _empty_pipeline_status() -> dict[str, Any]:
    return {
        "queueStats": {
            "pending": 0,
            "deadLetterCount": 0,
            "totalProcessed": 0,
            "totalFailed": 0,
        },
        "isProcessing": False,
        "lastProcessedAt": None,
        "metadataUpdateFailures": 0,
        "backgroundProcessingFailures": 0,
    }


def _lance_score(cos: float) -> float:
    """L2-distance similarity: 1/(1+L2), with L2 = sqrt(2-2cos) for unit vectors."""
    return 1.0 / (1.0 + math.sqrt(max(0.0, 2.0 - 2.0 * cos)))


def _entity_docs(store: FalkorGraphStore, filter: EntityFilter | None = None) -> list[Doc]:
    return [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities(filter)]


def _query_text(entity: Doc) -> str:
    observations = " ".join(coerce_observation(o) for o in entity.get("observations") or [])
    return f"{entity['name']} {observations}"


# Neither the status nor the entityType filter can be pushed into the ANN
# index, so a filtered call reads a candidate window and widens it by this
# factor whenever the window ran out before `limit` was met.
_CANDIDATE_GROWTH = 4


def _search_similar(
    store: FalkorGraphStore,
    query_text: str,
    limit: int,
    min_score: float | None = None,
    entity_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Similarity search over the entity vector index: embeds the query
    (QUERY prefix), asks FalkorDB's ANN index for the nearest candidates
    (``FalkorGraphStore.vector_knn``), scores 1/(1+L2) — L2 = sqrt(2-2cos) for
    the cosine the index reports — filters, and truncates.

    Only active entities are returned. A superseded or deprecated entity keeps
    its embedding (mutations invalidate, they never overwrite), so the index
    still offers it and the filter has to happen here — the same filter every
    other default read applies. Retraction is the exception: it drops the
    vector outright, so a retracted entity is not even a candidate.

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
    query_vector = get_embedder().embed_query(query_text)
    resolved: dict[str, Any] = {}
    k = max(limit, 1)
    while True:
        candidates = store.vector_knn(query_vector, k)
        results: list[dict[str, Any]] = []
        exhausted = len(candidates) < k
        scored = sorted(
            ((entity_id, _lance_score(cosine)) for entity_id, cosine in candidates),
            key=lambda pair: -pair[1],
        )
        for entity_id, score in scored:
            if min_score is not None and score < min_score:
                break  # sorted: the rest of THIS window is below; growth decides the rest
            if entity_id not in resolved:
                resolved[entity_id] = store.read_entity(entity_id)
            entity = resolved[entity_id]
            if entity is None or entity.effective_status != EntityStatus.ACTIVE:
                continue
            if entity_types and entity.entity_type.value not in entity_types:
                continue
            results.append(
                {
                    "id": entity_id,
                    "score": score,
                    "metadata": {
                        "name": entity.name,
                        "entityType": entity.entity_type.value,
                        "entryType": "entity",
                    },
                }
            )
            if len(results) >= limit:
                break
        if exhausted or len(results) >= limit:
            return results
        k *= _CANDIDATE_GROWTH


def _spread_sample(items: list[Doc], max_items: int, seed: int | None = None) -> list[Doc]:
    """A deterministic sample spread evenly across store order, not the first
    ``max_items`` records — a first-N sample only ever looks at whatever was
    written earliest, which skews systematically (oldest entity types,
    earliest sessions, …). Indices are ``offset + i*step`` (stride
    ``len(items)/max_items``, wrapped modulo ``len(items)``); ``seed`` shifts
    the phase so repeated calls can cover a different slice of the same
    graph while staying reproducible for a given seed."""
    n = len(items)
    if n <= max_items:
        return items
    step = n / max_items
    offset = (seed % n) if seed is not None else 0
    seen: set[int] = set()
    indices: list[int] = []
    for i in range(max_items):
        index = (offset + int(i * step)) % n
        if index not in seen:
            seen.add(index)
            indices.append(index)
    # Stride rounding can collide on small n/max_items ratios; fill any gap
    # by scanning forward from the offset so the sample size stays exact.
    pos = offset
    while len(indices) < max_items:
        pos = (pos + 1) % n
        if pos not in seen:
            seen.add(pos)
            indices.append(pos)
    return [items[i] for i in sorted(indices)]


def _as_type_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


# =============================================================================
# Input models
# =============================================================================


class GraphArgInput(CommandInput):
    graph: str | None = None


class EmbedEntityInput(CommandInput):
    id: UuidStr
    graph: str | None = None


class EmbedEntitiesInput(CommandInput):
    entity_type: str | None = Field(default=None, alias="entityType")
    force_reembed: bool | None = Field(default=None, alias="forceReembed")
    graph: str | None = None


class WarmEmbedderInput(CommandInput):
    pass


class EmbeddingReconcileInput(CommandInput):
    dry_run: bool | None = Field(default=None, alias="dryRun")
    clean_orphans: bool | None = Field(default=None, alias="cleanOrphans")
    graph: str | None = None


class SemanticSearchInput(CommandInput):
    query: str
    limit: int | None = Field(default=None, ge=1)
    min_score: float | None = Field(default=None, ge=0, le=1, alias="minScore")
    entity_type: EntityType | list[EntityType] | None = Field(default=None, alias="entityType")
    category: str | list[str] | None = None
    graph: str | None = None


class HybridWeights(CommandInput):
    vector: float | None = Field(default=None, ge=0, le=1)
    keyword: float | None = Field(default=None, ge=0, le=1)
    graph: float | None = Field(default=None, ge=0, le=1)


class HybridSearchInput(SemanticSearchInput):
    weights: HybridWeights | None = None
    graph_hops: int | None = Field(default=None, ge=0, le=3, alias="graphHops")
    quality_grouping: bool | None = Field(default=None, alias="qualityGrouping")
    grouping_strategy: str | None = Field(default=None, alias="groupingStrategy")
    mmr_lambda: float | None = Field(default=None, ge=0, le=1, alias="mmrLambda")
    recency_boost: bool | None = Field(default=None, alias="recencyBoost")
    recency_max_boost: float | None = Field(default=None, ge=0, le=1, alias="recencyMaxBoost")
    recency_half_life_days: float | None = Field(
        default=None, gt=0, le=365, alias="recencyHalfLifeDays"
    )
    memory_type: str | list[str] | None = Field(default=None, alias="memoryType")
    domain: str | list[str] | None = None
    durability: str | list[str] | None = None


class SemanticNeighborsInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    limit: int | None = Field(default=None, ge=1)
    min_similarity: float | None = Field(default=None, ge=0, le=1, alias="minSimilarity")
    entity_type: EntityType | list[EntityType] | None = Field(default=None, alias="entityType")
    graph: str | None = None


class FindClustersInput(CommandInput):
    similarity_threshold: float | None = Field(
        default=None, ge=0, le=1, alias="similarityThreshold"
    )
    min_cluster_size: int | None = Field(default=None, ge=1, alias="minClusterSize")
    entity_type: EntityType | list[EntityType] | None = Field(default=None, alias="entityType")
    max_entities: int | None = Field(default=None, ge=1, alias="maxEntities")
    graph: str | None = None


class SemanticGapsInput(CommandInput):
    limit: int | None = Field(default=None, ge=1)
    min_similarity: float | None = Field(default=None, ge=0, le=1, alias="minSimilarity")
    entity_type: EntityType | list[EntityType] | None = Field(default=None, alias="entityType")
    max_entities: int | None = Field(default=None, ge=1, alias="maxEntities")
    seed: int | None = None
    graph: str | None = None


class SuggestRelationsInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    limit: int | None = Field(default=None, ge=1)
    min_similarity: float | None = Field(default=None, ge=0, le=1, alias="minSimilarity")
    target_entity_type: EntityType | list[EntityType] | None = Field(
        default=None, alias="targetEntityType"
    )
    graph: str | None = None


class ResolveGapsInput(CommandInput):
    threshold: float | None = Field(default=None, ge=0, le=1)
    max_resolutions: int | None = Field(default=None, ge=1, alias="maxResolutions")
    relation_type_hint: str | None = Field(default=None, alias="relationTypeHint")
    dry_run: bool | None = Field(default=None, alias="dryRun")
    graph: str | None = None


# analyze-category lives in theloom/operations/documents.py (it
# reads document chunks, not entities).


# =============================================================================
# Embedding commands (deterministic)
# =============================================================================

_EMBED_TYPES_MSG = (
    "concept, claim, source, question, evidence, pattern, insight, tension, "
    "convergence, system, variable, loop, leverage_point, event, procedure, hypothesis"
)


def _embed_one(store: FalkorGraphStore, entity: Doc, skip_hash_check: bool) -> dict[str, Any]:
    """The pipeline's embedEntity: hash-skip, embed, store vector + metadata."""
    current_hash = compute_content_hash(entity)
    if (
        not skip_hash_check
        and entity.get("embeddingStatus") == "completed"
        and entity.get("contentHash") == current_hash
    ):
        return {"entityId": entity["id"], "status": "skipped"}
    try:
        vector = get_embedder().embed_document(build_embedding_text(entity))
        store.ensure_vector_index()
        store.set_entity_vector(entity["id"], vector)
        store.update_entity(
            entity["id"],
            {
                "embeddingStatus": "completed",
                "contentHash": current_hash,
                "lastEmbeddedAt": iso_now(),
                "embeddingVersion": EMBEDDING_VERSION,
            },
        )
        return {"entityId": entity["id"], "status": "embedded", "contentHash": current_hash}
    except Exception as exc:
        message = str(exc)
        store.update_entity(entity["id"], {"embeddingStatus": "error", "embeddingError": message})
        return {"entityId": entity["id"], "status": "error", "error": message}


def embed_entity(params: EmbedEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity = store.read_entity(params.id)
    if entity is None:
        raise NotFoundError(f"Entity not found with ID: {params.id}")
    return _embed_one(
        store, entity.model_dump(by_alias=True, exclude_unset=True), skip_hash_check=False
    )


def warm_embedder(params: WarmEmbedderInput, multi: MultiGraph) -> dict[str, Any]:
    """Pre-download the embedding model and run one warmup query, so the
    ~500MB HuggingFace fetch happens here rather than invisibly inside the
    first real embed/search command."""
    get_embedder().embed_query("warm up the embedding model")
    return {
        "warm": True,
        "model": EMBEDDING_VERSION,
        "dimensions": EMBEDDING_DIMENSIONS,
        "cacheDir": load_config().model_cache_dir,
    }


def embed_entities(params: EmbedEntitiesInput, multi: MultiGraph) -> dict[str, Any]:
    # The op-level check accepts only 16 types (the message omits
    # inference_rule/inference_trace/research_session), so those three pass the
    # input schema but fail here. 'Invalid …' classifies as VALIDATION_ERROR.
    if params.entity_type is not None and params.entity_type not in set(
        _EMBED_TYPES_MSG.split(", ")
    ):
        from theloom.errors import ValidationError

        raise ValidationError(
            f"Invalid entity type: {params.entity_type}. Must be one of: {_EMBED_TYPES_MSG}"
        )
    store = multi.get_store(params.graph)
    filter = (
        EntityFilter.model_validate({"entityType": params.entity_type})
        if params.entity_type
        else None
    )
    entities = _entity_docs(store, filter)
    batch_size = 16
    total = len(entities)
    progress = {
        "total": total,
        "completed": 0,
        "skipped": 0,
        "failed": 0,
        "currentBatch": 0,
        "totalBatches": max(1, math.ceil(total / batch_size)),
    }
    if total == 0:
        return progress
    for start in range(0, total, batch_size):
        progress["currentBatch"] += 1
        for entity in entities[start : start + batch_size]:
            if not params.force_reembed:
                current_hash = compute_content_hash(entity)
                if (
                    entity.get("embeddingStatus") == "completed"
                    and entity.get("contentHash") == current_hash
                ):
                    progress["skipped"] += 1
                    continue
            result = _embed_one(store, entity, skip_hash_check=True)
            if result["status"] == "embedded":
                progress["completed"] += 1
            elif result["status"] == "skipped":
                progress["skipped"] += 1
            else:
                progress["failed"] += 1
    return progress


def flush_pending_embeddings(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    # One-shot CLI: the in-memory queue is empty by construction.
    return dict(EMPTY_PROGRESS)


def retry_failed_embeddings(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    return {"retriedCount": 0, "results": dict(EMPTY_PROGRESS)}


def embedding_status(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    counts: dict[str, int] = {"pending": 0, "processing": 0, "completed": 0, "error": 0, "none": 0}
    entities = _entity_docs(store)
    for entity in entities:
        status = entity.get("embeddingStatus") or "none"
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(entities)
    return {"counts": counts, "pipelineStatus": _empty_pipeline_status()}


def list_dead_letters(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    return {"deadLetters": [], "count": 0}


def embedding_reconcile(params: EmbeddingReconcileInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    dry_run = params.dry_run if params.dry_run is not None else True
    entities = _entity_docs(store)
    vectors = store.get_entity_vectors()
    status_fixed_missing = 0
    status_fixed_has = 0
    for entity in entities:
        has_vector = entity["id"] in vectors
        if entity.get("embeddingStatus") == "completed" and not has_vector:
            status_fixed_missing += 1
            if not dry_run:
                store.update_entity(
                    entity["id"],
                    {
                        "embeddingStatus": None,
                        "contentHash": None,
                        "lastEmbeddedAt": None,
                        "embeddingVersion": None,
                    },
                )
        elif entity.get("embeddingStatus") != "completed" and has_vector:
            status_fixed_has += 1
            if not dry_run:
                store.update_entity(
                    entity["id"],
                    {
                        "embeddingStatus": "completed",
                        "contentHash": compute_content_hash(entity),
                        "lastEmbeddedAt": iso_now(),
                        "embeddingVersion": EMBEDDING_VERSION,
                    },
                )
    return {
        "entitiesScanned": len(entities),
        "statusFixedMissingVector": status_fixed_missing,
        "statusFixedHasVector": status_fixed_has,
        "duplicatesRemoved": 0,  # one vector property per node by construction
        "reembedFailed": 0,
        "orphanedRowsCleaned": 0,
        "dryRun": dry_run,
    }


# =============================================================================
# Search (embedding-ranked)
# =============================================================================


def _tokenize(query: str) -> list[str]:
    terms = [t for t in re.split(r"\W+", query.lower()) if len(t) >= 2 and t not in STOP_WORDS]
    return list(dict.fromkeys(terms))


def _match_term(term: str, text: str) -> bool:
    if not term or not text:
        return False
    escaped = re.escape(term)
    pattern = rf"(?<=\s|^){escaped}(?=\s|$)" if re.search(r"\W", term) else rf"\b{escaped}\b"
    return re.search(pattern, text, re.I) is not None


def _keyword_scores(
    query_terms: list[str], entities: dict[str, Doc], entity_ids: list[str]
) -> dict[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    if not query_terms:
        return matches
    for entity_id in entity_ids:
        entity = entities.get(entity_id)
        if entity is None:
            continue
        observations = " ".join(coerce_observation(o) for o in entity.get("observations") or [])
        search_text = f"{entity['name']} {observations}".strip()
        matched = [t for t in query_terms if _match_term(t, search_text)]
        if matched:
            matches[entity_id] = {
                "score": min(len(matched) / len(query_terms), 1.0),
                "matchedTerms": matched,
            }
    return matches


def _match_source(scores: dict[str, float]) -> str:
    v, k, g = scores["vector"] > 0, scores["keyword"] > 0, scores["graph"] > 0
    if v and k and g:
        return "semantic+keyword+graph"
    if v and k:
        return "semantic+keyword"
    if v and g:
        return "semantic+graph"
    if k:
        return "keyword"
    if g:
        return "graph"
    return "semantic"


def semantic_search(params: SemanticSearchInput, multi: MultiGraph) -> list[dict[str, Any]]:
    store = multi.get_store(params.graph)
    results = _search_similar(
        store,
        params.query,
        limit=params.limit or 10,
        min_score=params.min_score,
        entity_types=_as_type_list(params.entity_type),
    )
    return [
        {
            "entityId": r["id"],
            "name": r["metadata"]["name"],
            "entityType": r["metadata"]["entityType"],
            "score": r["score"],
            "scores": {"vector": r["score"], "keyword": 0, "graph": 0},
            "matchSource": "semantic",
            "entryType": r["metadata"]["entryType"],
        }
        for r in results
    ]


def hybrid_search(params: HybridSearchInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    weights = dict(DEFAULT_WEIGHTS)
    if params.weights is not None:
        for key in ("vector", "keyword", "graph"):
            value = getattr(params.weights, key)
            if value is not None:
                weights[key] = value
    graph_hops = min(params.graph_hops if params.graph_hops is not None else 1, 3)
    limit = params.limit or 10
    quality_grouping = params.quality_grouping if params.quality_grouping is not None else True
    strategy = params.grouping_strategy or "similar"

    vector_k = max(limit * 3, 30)
    vector_results = _search_similar(
        store,
        params.query,
        limit=vector_k,
        min_score=params.min_score,
        entity_types=_as_type_list(params.entity_type),
    )
    if not vector_results:
        return {
            "results": [],
            "totalCandidates": 0,
            "qualityGroups": 0,
            "query": {
                "text": params.query,
                "weights": weights,
                "graphHops": graph_hops,
                "qualityGrouping": quality_grouping,
            },
        }

    entities = {e["id"]: e for e in _entity_docs(store)}
    query_terms = _tokenize(params.query)
    keyword_matches = _keyword_scores(query_terms, entities, [r["id"] for r in vector_results])

    # Graph expansion: top seeds ranked by vector+keyword blend, 1/hop scores.
    seed_ranked = sorted(
        vector_results,
        key=lambda r: (
            -(
                weights["vector"] * float(r["score"])
                + weights["keyword"] * float(keyword_matches.get(r["id"], {}).get("score", 0))
            )
        ),
    )
    max_seeds = min(5, max(1, math.ceil(len(vector_results) * 0.5)))
    seed_ids = [r["id"] for r in seed_ranked[:max_seeds]]
    seed_set = set(seed_ids)
    graph_results: dict[str, dict[str, Any]] = {}
    if graph_hops > 0:
        for seed_id in seed_ids:
            frontier = [seed_id]
            for hop in range(1, graph_hops + 1):
                next_frontier: list[str] = []
                for node_id in frontier:
                    neighbors = store.get_neighbors(node_id)[:10]
                    for neighbor in neighbors:
                        if neighbor.id in seed_set:
                            continue
                        existing = graph_results.get(neighbor.id)
                        if not existing or existing["hopDistance"] > hop:
                            graph_results[neighbor.id] = {
                                "entityId": neighbor.id,
                                "name": neighbor.name,
                                "entityType": neighbor.entity_type.value,
                                "hopDistance": hop,
                                "expandedFrom": seed_id,
                                "score": 1.0 / hop,
                            }
                        if not existing:
                            next_frontier.append(neighbor.id)
                frontier = next_frontier

    result_map: dict[str, dict[str, Any]] = {}
    for vr in vector_results:
        km = keyword_matches.get(vr["id"])
        scores = {
            "vector": float(vr["score"]),
            "keyword": float(km["score"]) if km else 0.0,
            "graph": 0.0,
        }
        row: dict[str, Any] = {
            "entityId": vr["id"],
            "name": vr["metadata"]["name"],
            "entityType": vr["metadata"]["entityType"],
            "score": sum(weights[k] * scores[k] for k in weights),
            "scores": scores,
            "matchSource": _match_source(scores),
            "entryType": vr["metadata"]["entryType"],
        }
        if km:
            row["matchedTerms"] = km["matchedTerms"]
        result_map[vr["id"]] = row
    for gr in graph_results.values():
        existing = result_map.get(gr["entityId"])
        if existing is None:
            scores = {"vector": 0.0, "keyword": 0.0, "graph": float(gr["score"])}
            result_map[gr["entityId"]] = {
                "entityId": gr["entityId"],
                "name": gr["name"],
                "entityType": gr["entityType"],
                "score": sum(weights[k] * scores[k] for k in weights),
                "scores": scores,
                "matchSource": "graph",
                "hopDistance": gr["hopDistance"],
                "expandedFrom": gr["expandedFrom"],
            }
        else:
            existing["scores"]["graph"] = max(existing["scores"]["graph"], gr["score"])
            existing["score"] = sum(weights[k] * existing["scores"][k] for k in weights)
            existing["matchSource"] = _match_source(existing["scores"])
            existing["hopDistance"] = gr["hopDistance"]
            existing["expandedFrom"] = gr["expandedFrom"]

    combined = sorted(result_map.values(), key=lambda r: -float(r["score"]))

    if params.recency_boost:
        import datetime

        max_boost = params.recency_max_boost if params.recency_max_boost is not None else 0.15
        half_life_days = params.recency_half_life_days or 7
        now_ms = datetime.datetime.now(datetime.UTC).timestamp() * 1000
        for row in combined:
            entity = entities.get(row["entityId"])
            if entity is None:
                continue
            timestamp = entity.get("updated_at") or entity.get("created_at")
            if not timestamp:
                continue
            parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_ms = max(0.0, now_ms - parsed.timestamp() * 1000)
            half_life_ms = max(1.0, half_life_days * 86_400_000)
            decay = math.exp(-age_ms * math.log(2) / half_life_ms)
            row["score"] *= 1 + max_boost * decay
        combined.sort(key=lambda r: -float(r["score"]))

    if params.mmr_lambda is not None and len(combined) > 1:
        lam = params.mmr_lambda
        token_sets = [
            set(t for t in re.split(r"\W+", f"{r['name']} {r['entityType']}".lower()) if t)
            for r in combined
        ]
        max_score = float(combined[0]["score"])
        normalized = [(float(r["score"]) / max_score if max_score > 0 else 0.0) for r in combined]
        selected = [0]
        remaining = list(range(1, len(combined)))
        count = min(limit, len(combined))
        while len(selected) < count and remaining:
            best_index, best_mmr = remaining[0], -math.inf
            for index in remaining:
                max_sim = 0.0
                for sel in selected:
                    a, b = token_sets[index], token_sets[sel]
                    union = len(a | b)
                    sim = (len(a & b) / union) if union else 0.0
                    max_sim = max(max_sim, sim)
                mmr = lam * normalized[index] - (1 - lam) * max_sim
                if mmr > best_mmr:
                    best_index, best_mmr = index, mmr
            selected.append(best_index)
            remaining.remove(best_index)
        combined = [combined[i] for i in selected]

    truncated = combined[:limit]

    quality_groups = 0
    if quality_grouping and truncated:
        multiplier = 1.5 if strategy == "similar" else 1.0
        ordered = sorted(truncated, key=lambda r: -float(r["score"]))
        if len(ordered) == 1:
            ordered[0]["qualityGroup"] = 1
            groups = [[ordered[0]]]
        else:
            gaps = [
                float(ordered[i]["score"]) - float(ordered[i + 1]["score"])
                for i in range(len(ordered) - 1)
            ]
            mean_gap = sum(gaps) / len(gaps)
            threshold = max(mean_gap * 1.5 * multiplier, 0.05)
            groups = []
            current: list[dict[str, Any]] = []
            for i, row in enumerate(ordered):
                current.append(row)
                if i < len(gaps) and gaps[i] > threshold:
                    groups.append(current)
                    current = []
            if current:
                groups.append(current)
            for number, group in enumerate(groups, start=1):
                for row in group:
                    row["qualityGroup"] = number
        truncated = [row for group in groups for row in group]
        quality_groups = len(groups)

    return {
        "results": truncated,
        "totalCandidates": len(vector_results) + len(graph_results),
        "qualityGroups": quality_groups,
        "query": {
            "text": params.query,
            "weights": weights,
            "graphHops": graph_hops,
            "qualityGrouping": quality_grouping,
        },
    }


# =============================================================================
# Discovery (embedding-ranked) + resolve-gaps
# =============================================================================


def _connected_ids(store: FalkorGraphStore, entity_id: str) -> set[str]:
    connected = {entity_id}
    for relation in store.get_relations(entity_id):
        connected.add(relation.from_)
        connected.add(relation.to)
    return connected


def semantic_neighbors(params: SemanticNeighborsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    store = multi.get_store(params.graph)
    entity = store.read_entity(params.entity_id)
    if entity is None:
        raise NotFoundError(f"Entity not found: {params.entity_id}")
    limit = params.limit or 10
    min_similarity = params.min_similarity if params.min_similarity is not None else 0.5
    doc = entity.model_dump(by_alias=True, exclude_unset=True)
    results = _search_similar(
        store,
        _query_text(doc),
        limit=limit * 3,
        min_score=min_similarity,
        entity_types=_as_type_list(params.entity_type),
    )
    connected = _connected_ids(store, params.entity_id)
    neighbors = [
        {
            "entity": {
                "id": r["id"],
                "name": r["metadata"]["name"],
                "entityType": r["metadata"]["entityType"],
            },
            "similarity": r["score"],
        }
        for r in results
        if r["id"] not in connected
    ]
    neighbors.sort(key=lambda n: -float(n["similarity"]))
    return neighbors[:limit]


def find_clusters(params: FindClustersInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    threshold = params.similarity_threshold if params.similarity_threshold is not None else 0.7
    min_cluster_size = params.min_cluster_size or 2
    max_entities = params.max_entities or 5000
    filter = None
    if isinstance(params.entity_type, EntityType):
        filter = EntityFilter.model_validate({"entityType": params.entity_type.value})
    all_entities = _entity_docs(store, filter)
    if not all_entities:
        return {"clusters": [], "sampled": False, "totalEntities": 0, "sampledEntities": 0}
    sampled = len(all_entities) > max_entities
    if sampled:
        step = len(all_entities) / max_entities
        entities = [all_entities[int(i * step)] for i in range(max_entities)]
    else:
        entities = all_entities
    index = {
        e["id"]: {"id": e["id"], "name": e["name"], "entityType": e["entityType"]} for e in entities
    }

    parent: dict[str, str] = {e["id"]: e["id"] for e in entities}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    edge_scores: dict[str, list[float]] = {}
    for entity in entities:
        for sr in _search_similar(store, _query_text(entity), limit=20, min_score=threshold):
            if sr["id"] == entity["id"] or sr["id"] not in index:
                continue
            if float(sr["score"]) >= threshold:
                parent[find(entity["id"])] = find(sr["id"])
                key = ":".join(sorted([entity["id"], sr["id"]]))
                edge_scores.setdefault(key, []).append(float(sr["score"]))

    by_root: dict[str, list[str]] = {}
    for entity in entities:
        by_root.setdefault(find(entity["id"]), []).append(entity["id"])
    clusters = []
    cluster_id = 0
    for members in by_root.values():
        if len(members) < min_cluster_size:
            continue
        member_set = set(members)
        edges = [
            sum(scores) / len(scores)
            for key, scores in edge_scores.items()
            if all(part in member_set for part in key.split(":"))
        ]
        clusters.append(
            {
                "id": cluster_id,
                "entities": [index[m] for m in members],
                "size": len(members),
                "avgSimilarity": sum(edges) / len(edges) if edges else 0,
            }
        )
        cluster_id += 1
    clusters.sort(key=lambda c: -int(str(c["size"])))
    return {
        "clusters": clusters,
        "sampled": sampled,
        "totalEntities": len(all_entities),
        "sampledEntities": len(entities),
    }


def semantic_gaps(params: SemanticGapsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    store = multi.get_store(params.graph)
    limit = params.limit or 20
    min_similarity = params.min_similarity if params.min_similarity is not None else 0.6
    max_entities = params.max_entities or 200
    entities = _spread_sample(_entity_docs(store), max_entities, params.seed)
    if not entities:
        return []
    index = {
        e["id"]: {"id": e["id"], "name": e["name"], "entityType": e["entityType"]} for e in entities
    }
    seen: set[str] = set()
    gaps: list[dict[str, Any]] = []
    for entity in entities:
        results = _search_similar(store, _query_text(entity), limit=10, min_score=min_similarity)
        connected = _connected_ids(store, entity["id"])
        for sr in results:
            if float(sr["score"]) < min_similarity or sr["id"] in connected:
                continue
            # The sample decides which entities are *probed*, not which may be
            # the far end of a gap: a spread sample scatters co-written,
            # semantically adjacent entities into unsampled stride slots, so
            # requiring both ends in the sample would discard most real pairs.
            # The search already carries the partner's metadata.
            partner = index.get(sr["id"]) or {
                "id": sr["id"],
                "name": sr["metadata"]["name"],
                "entityType": sr["metadata"]["entityType"],
            }
            key = ":".join(sorted([entity["id"], sr["id"]]))
            if key in seen:
                continue
            seen.add(key)
            gaps.append(
                {
                    "entityA": index[entity["id"]],
                    "entityB": partner,
                    "similarity": sr["score"],
                }
            )
    gaps.sort(key=lambda g: -float(g["similarity"]))
    return gaps[:limit]


def suggest_relations(params: SuggestRelationsInput, multi: MultiGraph) -> list[dict[str, Any]]:
    store = multi.get_store(params.graph)
    entity = store.read_entity(params.entity_id)
    if entity is None:
        raise NotFoundError(f"Entity not found: {params.entity_id}")
    limit = params.limit or 10
    min_similarity = params.min_similarity if params.min_similarity is not None else 0.5
    doc = entity.model_dump(by_alias=True, exclude_unset=True)

    neighbors = semantic_neighbors(
        SemanticNeighborsInput.model_validate(
            {
                "entityId": params.entity_id,
                "limit": limit * 2,
                "minSimilarity": min_similarity,
                **(
                    {"entityType": params.target_entity_type}
                    if params.target_entity_type is not None
                    else {}
                ),
                **({"graph": params.graph} if params.graph else {}),
            }
        ),
        multi,
    )

    id_to_type = {e["id"]: e["entityType"] for e in _entity_docs(store)}
    pair_freq: dict[str, dict[str, Any]] = {}
    for relation in store.list_relations()[:500]:
        from_type = id_to_type.get(relation.from_)
        to_type = id_to_type.get(relation.to)
        if not from_type or not to_type:
            continue
        key = f"{from_type}→{to_type}"
        data = pair_freq.setdefault(key, {"total": 0, "types": {}})
        data["total"] += 1
        data["types"][relation.relation_type.value] = (
            data["types"].get(relation.relation_type.value, 0) + 1
        )

    suggestions = []
    from_info = {"id": doc["id"], "name": doc["name"], "entityType": doc["entityType"]}
    for neighbor in neighbors:
        forward = pair_freq.get(f"{doc['entityType']}→{neighbor['entity']['entityType']}")
        reverse = pair_freq.get(f"{neighbor['entity']['entityType']}→{doc['entityType']}")
        chosen: dict[str, Any] | None = (
            forward
            if forward and (not reverse or forward["total"] >= reverse["total"])
            else reverse
        )
        suggested = None
        pattern_confidence = 0.0
        if chosen and chosen["types"]:
            top_type, top_count = max(chosen["types"].items(), key=lambda item: item[1])
            suggested = top_type
            pattern_confidence = top_count / chosen["total"]
        confidence = float(neighbor["similarity"]) * (0.5 + 0.5 * pattern_confidence)
        row: dict[str, Any] = {
            "from": from_info,
            "to": neighbor["entity"],
            "similarity": neighbor["similarity"],
            "confidence": confidence,
        }
        if suggested is not None:
            row["suggestedRelationType"] = suggested
        suggestions.append(row)
    suggestions.sort(key=lambda s: -float(s["confidence"]))
    return suggestions[:limit]


def resolve_gaps(params: ResolveGapsInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    threshold = params.threshold if params.threshold is not None else 0.85
    max_resolutions = params.max_resolutions or 20
    hint = params.relation_type_hint or "related_to"
    dry_run = params.dry_run if params.dry_run is not None else True
    valid_types = {t.value for t in ALL_RELATION_TYPES}
    if hint not in valid_types:
        from theloom.errors import ValidationError

        raise ValidationError(
            f"Invalid relation type: '{hint}'. Must be one of: {', '.join(sorted(valid_types))}"
        )
    gaps = semantic_gaps(
        SemanticGapsInput.model_validate(
            {
                "minSimilarity": threshold,
                "limit": 1000,
                **({"graph": params.graph} if params.graph else {}),
            }
        ),
        multi,
    )
    resolved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for gap in gaps:
        similarity = float(gap["similarity"])
        if similarity < threshold:
            continue
        item: dict[str, Any] = {
            "sourceId": gap["entityA"]["id"],
            "sourceName": gap["entityA"]["name"],
            "targetId": gap["entityB"]["id"],
            "targetName": gap["entityB"]["name"],
            "similarity": similarity,
            "relationCreated": False,
            "confidence": similarity * 0.7,
            "relationType": hint,
        }
        if len(resolved) >= max_resolutions:
            skipped.append({**item, "skipReason": "max_resolutions_reached"})
            continue
        if (
            store.read_relation(item["sourceId"], item["targetId"]) is not None
            or store.read_relation(item["targetId"], item["sourceId"]) is not None
        ):
            skipped.append({**item, "skipReason": "relation_already_exists"})
            continue
        if not dry_run:
            from theloom.operations.relations import CreateRelationInput, create_relation

            create_relation(
                CreateRelationInput.model_validate(
                    {
                        "from": item["sourceId"],
                        "to": item["targetId"],
                        "relationType": hint,
                        "polarity": None,
                        "strength": "moderate",
                        "evidence": None,
                        **({"graph": params.graph} if params.graph else {}),
                    }
                ),
                multi,
            )
            item["relationCreated"] = True
        resolved.append(item)
    return {"analyzed": len(gaps), "resolved": resolved, "skipped": skipped, "dryRun": dry_run}
