"""Semantic operations — the command layer over retrieval and ranking.

Retrieval lives in :mod:`theloom.semantic.search` and ranking in
:mod:`theloom.semantic.ranking`; what is left here is input handling, the
keyword signal, and the discovery commands built on top.

Score semantics: every similarity that
flows through vector search is ``1/(1+L2distance)`` — the vector store returns L2, and
for L2-normalized vectors L2 = sqrt(2 - 2*cos) — NOT plain cosine. Query texts
for discovery are ``name obs…`` (no [type] prefix) embedded with the QUERY
prefix. Hybrid fusion uses raw (un-normalized) weights .6/.25/.15 over vector/
keyword/graph signals, recency decays by half-life before MMR, and quality
groups split on score gaps (mean*1.5*strategy, min 0.05).

The one-shot CLI never accumulates an in-memory queue, so pipelineStatus
reports an empty-queue shape (isProcessing, lastProcessedAt, …) — but
flush-pending-embeddings, retry-failed-embeddings and list-dead-letters are
not queue reports; they act on the embedding state machine
(theloom/semantic/embedding_state.py) directly: flush embeds everything
needs_embedding, retry re-embeds status=error entities, and dead-letters
lists status=error entities with their embeddingError.

Tier note: document vectors are stored verbatim, but live query vectors come
from fastembed — so ranked outputs are embedding-ranked, while
embed-*/status shapes are fully deterministic.
"""

from __future__ import annotations

import datetime
import math
import re
from typing import Any

from pydantic import Field

from theloom.config import load_config
from theloom.errors import NotFoundError
from theloom.graph.metadata import coerce_observation
from theloom.model import ALL_RELATION_TYPES, EmbeddingStatus, EntityFilter, EntityType
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.notices import list_envelope, notice, with_notices
from theloom.semantic import landscape
from theloom.semantic.embed import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_VERSION,
    build_embedding_text,
    compute_content_hash,
    get_embedder,
)
from theloom.semantic.embedding_state import (
    apply_reconcile_action,
    mark_completed,
    mark_error,
    needs_embedding,
    plan_reconcile,
    status_counts,
)
from theloom.semantic.ranking import (
    DEFAULT_RECENCY_HALF_LIFE_DAYS,
    DEFAULT_RECENCY_MAX_BOOST,
    apply_mmr,
    apply_recency_boost,
    assign_quality_groups,
    expand_by_graph,
    fuse_scores,
    select_seeds,
)
from theloom.semantic.search import search_entities
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

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


def _entity_docs(store: FalkorGraphStore, filter: EntityFilter | None = None) -> list[Doc]:
    return [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities(filter)]


def _query_text(entity: Doc) -> str:
    observations = " ".join(coerce_observation(o) for o in entity.get("observations") or [])
    return f"{entity['name']} {observations}"


def _search_similar(
    store: FalkorGraphStore,
    query_text: str,
    limit: int,
    min_score: float | None = None,
    entity_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """This module's binding of :func:`theloom.semantic.search.search_entities`.

    Private to ``operations.semantic``: the only thing it adds is resolving the
    embedder from *this* module's ``get_embedder``, so the five command handlers
    below share one injection point. Callers outside this module should import
    ``theloom.semantic.search.search_entities`` directly.
    """
    return search_entities(
        store,
        query_text,
        limit,
        min_score,
        entity_types,
        embedder=get_embedder(),
    )


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


class EmbedderProfileInput(CommandInput):
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


def _world_partial_notices(params: CommandInput) -> list[Doc]:
    """``WORLD_PROJECTION_PARTIAL`` (tension (a), Part 5): a world's overlay
    reconstructs entities/relations from the event log, but a vector
    attached via ``set_entity_vector`` is a direct Cypher property write
    outside it (see ``theloom.store.falkor.FalkorGraphStore``'s module
    docstring — updates snapshot, but ``_embedding`` is not part of the
    versioned ``_doc``), so ``adopt_entity``'s copy-on-write never carries
    it: a fork's vector index reflects only what was embedded *inside* that
    fork, never what its parent already had embedded. An inherited entity
    can therefore still report ``embeddingStatus: "completed"`` (a doc
    field, correctly forked) while genuinely unsearchable in this world —
    this notice says so instead of a command silently searching (or
    reporting on) less than it appears to.
    """
    if params.world in (None, "main"):
        return []
    return [
        notice(
            "WORLD_PROJECTION_PARTIAL",
            f"World '{params.world}' does not inherit its parent's embeddings — this reflects "
            "only entities embedded inside this world, not the ones inherited from its parent.",
        )
    ]


def _embed_one(store: FalkorGraphStore, entity: Doc, skip_hash_check: bool) -> dict[str, Any]:
    """The pipeline's embedEntity: hash-skip, embed, store vector + metadata.

    The skip predicate and the transition writes both live in
    :mod:`theloom.semantic.embedding_state` — this is thin over it."""
    if not skip_hash_check and not needs_embedding(entity):
        return {"entityId": entity["id"], "status": "skipped"}
    current_hash = compute_content_hash(entity)
    try:
        vector = get_embedder().embed_document(build_embedding_text(entity))
        store.ensure_vector_index()
        store.set_entity_vector(entity["id"], vector)
        mark_completed(store, entity["id"], current_hash)
        return {"entityId": entity["id"], "status": "embedded", "contentHash": current_hash}
    except Exception as exc:
        message = str(exc)
        mark_error(store, entity["id"], message)
        return {"entityId": entity["id"], "status": "error", "error": message}


def embed_entity(params: EmbedEntityInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity = store.read_entity(params.id)
    if entity is None:
        raise NotFoundError(f"Entity not found with ID: {params.id}")
    result = _embed_one(
        store, entity.model_dump(by_alias=True, exclude_unset=True), skip_hash_check=False
    )
    return with_notices(result, _world_partial_notices(params))


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


def embedder_profile(params: EmbedderProfileInput, multi: MultiGraph) -> dict[str, Any]:
    """Desire 8 (claude-desires.md): the configured embedder's own empirical
    similarity landscape, measured live against a small fixed probe corpus
    (see theloom.semantic.landscape) — never a hard-coded constant. Every
    number below is computed fresh from this invocation's embedder; editing
    the probe corpus in theloom/semantic/landscape.py changes what the next
    call reports."""
    embedder = get_embedder()
    profile = landscape.measure_landscape(embedder)
    # Live-measured, not the EMBEDDING_DIMENSIONS constant: a swapped-in
    # embedder (a test double, or a future model behind the same override
    # point) may not share that constant's width.
    dimensions = len(embedder.embed_query("dimension probe"))
    return {
        "model": EMBEDDING_VERSION,
        "dimensions": dimensions,
        "probeCorpus": {
            "unrelatedPairCount": sum(1 for p in profile.pairs if p.relation == "unrelated"),
            "relatedPairCount": sum(1 for p in profile.pairs if p.relation == "related"),
            "pairs": [landscape.pair_doc(p) for p in profile.pairs],
        },
        "unrelatedPairBaseline": landscape.band_stats_doc(profile.unrelated_baseline),
        "relatedPairRange": landscape.band_stats_doc(profile.related_range),
        "meaningfullyRelatedCutoff": profile.meaningfully_related_cutoff,
        "cutoffMethod": profile.cutoff_method,
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
        return with_notices(progress, _world_partial_notices(params))
    for start in range(0, total, batch_size):
        progress["currentBatch"] += 1
        for entity in entities[start : start + batch_size]:
            if not params.force_reembed and not needs_embedding(entity):
                progress["skipped"] += 1
                continue
            result = _embed_one(store, entity, skip_hash_check=True)
            if result["status"] == "embedded":
                progress["completed"] += 1
            elif result["status"] == "skipped":
                progress["skipped"] += 1
            else:
                progress["failed"] += 1
    return with_notices(progress, _world_partial_notices(params))


def _batch_embed(store: FalkorGraphStore, entities: list[Doc]) -> dict[str, Any]:
    """Embed every entity in ``entities`` unconditionally (the caller already
    decided the selection), batched for the same progress shape
    ``embed_entities`` reports."""
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
            result = _embed_one(store, entity, skip_hash_check=True)
            if result["status"] == "embedded":
                progress["completed"] += 1
            elif result["status"] == "skipped":
                progress["skipped"] += 1
            else:
                progress["failed"] += 1
    return progress


def flush_pending_embeddings(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    """Embed every entity the state machine says needs it. The one-shot CLI
    never accumulates an in-memory queue to drain — flushing IS embedding,
    now, over exactly the ``needs_embedding`` set."""
    store = multi.get_store(params.graph)
    pending = [e for e in _entity_docs(store) if needs_embedding(e)]
    return _batch_embed(store, pending)


def retry_failed_embeddings(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    """Re-embed every entity whose last attempt landed in ``error``."""
    store = multi.get_store(params.graph)
    failed = [
        e for e in _entity_docs(store) if e.get("embeddingStatus") == EmbeddingStatus.ERROR.value
    ]
    return {"retriedCount": len(failed), "results": _batch_embed(store, failed)}


def embedding_status(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entities = _entity_docs(store)
    counts = status_counts(entities)
    counts["total"] = len(entities)
    return with_notices(
        {"counts": counts, "pipelineStatus": _empty_pipeline_status()},
        _world_partial_notices(params),
    )


def list_dead_letters(params: GraphArgInput, multi: MultiGraph) -> dict[str, Any]:
    """Every entity currently in ``error``, with the reason it failed."""
    store = multi.get_store(params.graph)
    errored = [
        e for e in _entity_docs(store) if e.get("embeddingStatus") == EmbeddingStatus.ERROR.value
    ]
    dead_letters = [
        {
            "entityId": e["id"],
            "name": e["name"],
            "entityType": e["entityType"],
            "embeddingError": e.get("embeddingError"),
        }
        for e in errored
    ]
    return list_envelope(dead_letters)


def embedding_reconcile(params: EmbeddingReconcileInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    dry_run = params.dry_run if params.dry_run is not None else True
    entities = _entity_docs(store)
    by_id = {e["id"]: e for e in entities}
    vector_ids = set(store.get_entity_vectors())
    actions = plan_reconcile(entities, vector_ids)
    if not dry_run:
        for action in actions:
            apply_reconcile_action(store, action, by_id[action.entity_id])
    status_fixed_missing = sum(1 for a in actions if a.kind == "clear_status")
    status_fixed_has = sum(1 for a in actions if a.kind == "mark_completed")
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


def semantic_search(params: SemanticSearchInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    results = _search_similar(
        store,
        params.query,
        limit=params.limit or 10,
        min_score=params.min_score,
        entity_types=_as_type_list(params.entity_type),
    )
    return list_envelope(
        [
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
        ],
        _world_partial_notices(params),
    )


def hybrid_search(params: HybridSearchInput, multi: MultiGraph) -> dict[str, Any]:
    """Fetch, then rank: one vector fetch through the search core, then the
    pure ranking stages in :mod:`theloom.semantic.ranking` — graph expansion,
    score fusion, optional recency decay, optional MMR, optional quality
    grouping — applied in that order."""
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
    query_summary = {
        "text": params.query,
        "weights": weights,
        "graphHops": graph_hops,
        "qualityGrouping": quality_grouping,
    }

    # --- fetch ---------------------------------------------------------------
    vector_k = max(limit * 3, 30)
    hits = _search_similar(
        store,
        params.query,
        limit=vector_k,
        min_score=params.min_score,
        entity_types=_as_type_list(params.entity_type),
    )
    if not hits:
        return with_notices(
            {"results": [], "totalCandidates": 0, "qualityGroups": 0, "query": query_summary},
            _world_partial_notices(params),
        )

    vector_rows = [
        {
            "entityId": hit["id"],
            "name": hit["metadata"]["name"],
            "entityType": hit["metadata"]["entityType"],
            "entryType": hit["metadata"]["entryType"],
            "score": hit["score"],
        }
        for hit in hits
    ]
    entities = {e["id"]: e for e in _entity_docs(store)}
    keyword_matches = _keyword_scores(
        _tokenize(params.query), entities, [row["entityId"] for row in vector_rows]
    )

    def neighbors_of(entity_id: str) -> list[tuple[str, str, str]]:
        return [(n.id, n.name, n.entity_type.value) for n in store.get_neighbors(entity_id)]

    # --- rank ----------------------------------------------------------------
    graph_rows = expand_by_graph(
        select_seeds(vector_rows, keyword_matches, weights), neighbors_of, graph_hops
    )
    ranked = fuse_scores(vector_rows, keyword_matches, graph_rows, weights)

    if params.recency_boost:
        ranked = apply_recency_boost(
            ranked,
            {
                entity_id: entity.get("updated_at") or entity.get("created_at")
                for entity_id, entity in entities.items()
            },
            now_ms=datetime.datetime.now(datetime.UTC).timestamp() * 1000,
            max_boost=(
                params.recency_max_boost
                if params.recency_max_boost is not None
                else DEFAULT_RECENCY_MAX_BOOST
            ),
            half_life_days=params.recency_half_life_days or DEFAULT_RECENCY_HALF_LIFE_DAYS,
        )

    if params.mmr_lambda is not None:
        ranked = apply_mmr(ranked, params.mmr_lambda, limit)

    results = ranked[:limit]
    quality_groups = 0
    if quality_grouping and results:
        results, quality_groups = assign_quality_groups(results, strategy)

    return with_notices(
        {
            "results": results,
            "totalCandidates": len(vector_rows) + len(graph_rows),
            "qualityGroups": quality_groups,
            "query": query_summary,
        },
        _world_partial_notices(params),
    )


# =============================================================================
# Discovery (embedding-ranked) + resolve-gaps
# =============================================================================


def _connected_ids(store: FalkorGraphStore, entity_id: str) -> set[str]:
    connected = {entity_id}
    for relation in store.get_relations(entity_id):
        connected.add(relation.from_)
        connected.add(relation.to)
    return connected


def semantic_neighbors(params: SemanticNeighborsInput, multi: MultiGraph) -> dict[str, Any]:
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
    return list_envelope(neighbors[:limit], _world_partial_notices(params))


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


def semantic_gaps(params: SemanticGapsInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    limit = params.limit or 20
    min_similarity = params.min_similarity if params.min_similarity is not None else 0.6
    max_entities = params.max_entities or 200
    entities = _spread_sample(_entity_docs(store), max_entities, params.seed)
    if not entities:
        return list_envelope([])
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
    return list_envelope(gaps[:limit])


def suggest_relations(params: SuggestRelationsInput, multi: MultiGraph) -> dict[str, Any]:
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
    )["items"]

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
    # suggest_relations calls semantic_neighbors directly (see above), which
    # already computes and would otherwise silently drop this notice on the
    # `["items"]` extraction above — emit it explicitly here too, since this
    # command inherits the same partial-embeddings-in-a-fork exposure.
    return list_envelope(suggestions[:limit], _world_partial_notices(params))


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
    for gap in gaps["items"]:
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
