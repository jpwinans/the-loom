"""Structural component signatures + far-analogy candidate detection.

Covers component-signature computation (single and all-components) and
signature comparison (structural comparison, semantic distance, and
far-analogy candidate detection).

A ComponentSignature captures the distribution of Weisfeiler-Leman fingerprint
hashes across a connected component, L2-normalized over the global hash ordering
so cosine similarity between components is meaningful. A far-analogy is a pair of
components with HIGH structural cosine similarity but HIGH semantic dissimilarity
(they share topology but are about different things):

    farAnalogyScore = structuralSimilarity * semanticDissimilarity

Semantic dissimilarity defaults to Jaccard distance over tokenized entity names
(the path the far-analogy composite uses); an optional embedding_manager with a
``generate_embedding`` method switches to average pairwise cosine distance.

The WL hashing primitive (hash_at_depth) is reused from
theloom.operations.reification, and connected components from
theloom.graph.analytics, to stay bit-identical with reify-patterns.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from theloom.graph.analytics import connected_components
from theloom.graph.hydrate import LoomGraph
from theloom.operations.reification import hash_at_depth
from theloom.semantic.embed import cosine_similarity

DEFAULT_MAX_DEPTH = 2
MAX_DEPTH_LIMIT = 10
HASH_DIGEST_LENGTH = 16
DEFAULT_MIN_STRUCTURAL_SIMILARITY = 0.3
DEFAULT_TOP_N = 10


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _l2_normalize(vector: list[float]) -> list[float]:
    if not vector:
        return []
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return [0.0 for _ in vector]
    return [x / norm for x in vector]


def compute_component_signature(
    component_entity_ids: list[str],
    graph: LoomGraph,
    max_depth: int | None = None,
    global_hash_order: list[str] | None = None,
) -> dict[str, Any]:
    """WL fingerprint distribution for one component -> L2-normalized vector."""
    effective_depth = min(
        max(DEFAULT_MAX_DEPTH if max_depth is None else max_depth, 0), MAX_DEPTH_LIMIT
    )

    sorted_ids = sorted(component_entity_ids)
    component_id = _sha256_hex(",".join(sorted_ids))[:HASH_DIGEST_LENGTH]

    if not component_entity_ids:
        return {
            "componentId": component_id,
            "entityCount": 0,
            "fingerprintDistribution": {},
            "signatureVector": [],
        }

    cache: dict[str, str] = {}
    fingerprint_distribution: dict[str, int] = {}
    for entity_id in component_entity_ids:
        digest = hash_at_depth(graph, entity_id, effective_depth, cache)
        fingerprint_distribution[digest] = fingerprint_distribution.get(digest, 0) + 1

    hash_order = (
        global_hash_order if global_hash_order is not None else sorted(fingerprint_distribution)
    )
    raw_vector = [float(fingerprint_distribution.get(h, 0)) for h in hash_order]
    signature_vector = _l2_normalize(raw_vector)

    return {
        "componentId": component_id,
        "entityCount": len(component_entity_ids),
        "fingerprintDistribution": fingerprint_distribution,
        "signatureVector": signature_vector,
    }


def compute_all_component_signatures(
    graph: LoomGraph, max_depth: int | None = None
) -> dict[str, Any]:
    """All component signatures over a shared global hash ordering.

    Returns the AllComponentSignaturesResult shape:
    {"signatures": [...], "globalHashOrder": [...], "componentCount": int},
    signatures sorted by entityCount desc then componentId asc.
    """
    if graph.order == 0:
        return {"signatures": [], "globalHashOrder": [], "componentCount": 0}

    effective_depth = min(
        max(DEFAULT_MAX_DEPTH if max_depth is None else max_depth, 0), MAX_DEPTH_LIMIT
    )

    cache: dict[str, str] = {}
    global_hash_set: set[str] = set()
    for node_id in graph.nodes():
        digest = hash_at_depth(graph, node_id, effective_depth, cache)
        global_hash_set.add(digest)

    global_hash_order = sorted(global_hash_set)

    components = connected_components(graph)
    signatures = [
        compute_component_signature(component, graph, effective_depth, global_hash_order)
        for component in components
    ]

    signatures.sort(key=lambda s: (-s["entityCount"], s["componentId"]))

    return {
        "signatures": signatures,
        "globalHashOrder": global_hash_order,
        "componentCount": len(components),
    }


def compare_component_signatures(sig1: dict[str, Any], sig2: dict[str, Any]) -> float:
    """Cosine similarity of two signature vectors, clamped to [0, 1]."""
    v1 = sig1["signatureVector"]
    v2 = sig2["signatureVector"]

    if len(v1) != len(v2):
        raise ValueError(f"Signature vectors have different lengths: {len(v1)} vs {len(v2)}")

    if len(v1) == 0:
        return 0.0

    dot_product = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for i in range(len(v1)):
        dot_product += v1[i] * v2[i]
        norm_a += v1[i] * v1[i]
        norm_b += v2[i] * v2[i]

    denominator = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denominator == 0:
        return 0.0

    similarity = dot_product / denominator
    return max(0.0, min(1.0, similarity))


def compute_semantic_distance(
    component1_entities: list[str],
    component2_entities: list[str],
    embedding_manager: Any | None = None,
) -> float:
    """Dissimilarity in [0, 1]: Jaccard over name tokens by default, or average
    pairwise embedding cosine distance when an embedding_manager is provided."""
    if not component1_entities and not component2_entities:
        return 0.0

    if embedding_manager is not None:
        return _compute_embedding_distance(
            component1_entities, component2_entities, embedding_manager
        )

    return _compute_jaccard_distance(component1_entities, component2_entities)


def find_far_analogy_candidates(
    signatures: list[dict[str, Any]], options: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """All pairs of signatures scored by structural similarity * semantic
    dissimilarity; skip pairs below minStructuralSimilarity, sort desc, top-N.

    Candidate shape: {"sourceComponent", "targetComponent", "structuralSimilarity",
    "semanticDissimilarity", "farAnalogyScore"} (full nested signatures, the
    FarAnalogyCandidate shape).
    """
    options = options or {}
    if len(signatures) < 2:
        return []

    min_structural = _opt(options, "minStructuralSimilarity", DEFAULT_MIN_STRUCTURAL_SIMILARITY)
    top_n = _opt_int(options, "topN", DEFAULT_TOP_N)
    component_entities = options.get("componentEntities") or {}
    embedding_manager = options.get("embeddingManager")

    candidates: list[dict[str, Any]] = []

    for i in range(len(signatures) - 1):
        for j in range(i + 1, len(signatures)):
            sig1 = signatures[i]
            sig2 = signatures[j]

            structural_similarity = compare_component_signatures(sig1, sig2)
            if structural_similarity < min_structural:
                continue

            entities1 = component_entities.get(sig1["componentId"]) or []
            entities2 = component_entities.get(sig2["componentId"]) or []

            semantic_dissimilarity = compute_semantic_distance(
                entities1, entities2, embedding_manager
            )
            far_analogy_score = structural_similarity * semantic_dissimilarity

            candidates.append(
                {
                    "sourceComponent": sig1,
                    "targetComponent": sig2,
                    "structuralSimilarity": structural_similarity,
                    "semanticDissimilarity": semantic_dissimilarity,
                    "farAnalogyScore": far_analogy_score,
                }
            )

    candidates.sort(key=lambda c: -c["farAnalogyScore"])
    return candidates[:top_n]


# =============================================================================
# Internal helpers
# =============================================================================


def _compute_embedding_distance(
    entities1: list[str], entities2: list[str], embedding_manager: Any
) -> float:
    embeddings1 = [embedding_manager.generate_embedding(name) for name in entities1]
    embeddings2 = [embedding_manager.generate_embedding(name) for name in entities2]

    total_similarity = 0.0
    pair_count = 0
    for emb1 in embeddings1:
        for emb2 in embeddings2:
            total_similarity += cosine_similarity(emb1, emb2)
            pair_count += 1

    if pair_count == 0:
        return 0.0

    average_similarity = total_similarity / pair_count
    return max(0.0, min(1.0, 1 - average_similarity))


def _compute_jaccard_distance(entities1: list[str], entities2: list[str]) -> float:
    tokens1 = _tokenize_entities(entities1)
    tokens2 = _tokenize_entities(entities2)

    if not tokens1 and not tokens2:
        return 0.0

    intersection = tokens1 & tokens2
    union = tokens1 | tokens2

    if not union:
        return 0.0

    jaccard_similarity = len(intersection) / len(union)
    return 1 - jaccard_similarity


def _tokenize_entities(entities: list[str]) -> set[str]:
    tokens: set[str] = set()
    for name in entities:
        for token in name.split():
            lower = token.lower()
            if lower:
                tokens.add(lower)
    return tokens


def _opt(options: dict[str, Any], key: str, default: float) -> float:
    value = options.get(key)
    return default if value is None else value


def _opt_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key)
    return default if value is None else value
