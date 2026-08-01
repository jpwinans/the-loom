"""Deduplication gate for hypothesis proposals.

Semantic similarity check against existing entities before proposing new ones,
in three modes: ``reject`` (default, drop near-duplicates), ``flag`` (keep but
annotate) and ``merge`` (combine observations with the existing entity).

The gate compares the proposal's embedding against the entity vectors held by
the store (``store.get_entity_vectors()``) using cosine similarity, restricted
to existing entities of the same type. When no embedding manager is supplied
(or the store holds no vectors) it falls back to case-insensitive name
matching, so the pipeline never crashes.
"""

from __future__ import annotations

import math
from typing import Any

Doc = dict[str, Any]

_ALL_STATUSES = ["active", "superseded", "deprecated", "retracted", "investigating"]

DEFAULT_SIMILARITY_THRESHOLD = 0.85
MAX_SIMILARITY_THRESHOLD = 0.99
MIN_SIMILARITY_THRESHOLD = 0.5
DEFAULT_MAX_CANDIDATES = 5


def _to_doc(obj: Any) -> Doc:
    if isinstance(obj, dict):
        return obj
    dumped: Doc = obj.model_dump(by_alias=True, exclude_unset=True)
    return dumped


def _list_entity_docs(store: Any) -> list[Doc]:
    from theloom.model import EntityFilter

    result = store.list_entities(EntityFilter.model_validate({"statusFilter": _ALL_STATUSES}))
    return [_to_doc(e) for e in result]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _embed_text(embedding_manager: Any, text: str) -> list[float]:
    """Embed proposal text via a duck-typed manager: ``generate_embedding``,
    then ``embed_query`` (the Embedder), else callable."""
    if hasattr(embedding_manager, "generate_embedding"):
        vec: list[float] = embedding_manager.generate_embedding(text)
        return vec
    if hasattr(embedding_manager, "embed_query"):
        vec = embedding_manager.embed_query(text)
        return vec
    vec = embedding_manager(text)
    return vec


def proposal_to_text(proposal: Doc) -> str:
    """Searchable text representation of a proposal for embedding comparison."""
    parts: list[str] = [
        proposal["entity"]["name"],
        f"Type: {proposal['entity']['entityType']}",
        *proposal["entity"]["observations"],
    ]
    if proposal.get("rationale"):
        parts.append(proposal["rationale"])
    return ". ".join(parts)


def deduplicate_proposals(
    proposals: list[Doc],
    embedding_manager: Any,
    store: Any,
    options: Doc | None = None,
) -> Doc:
    """Run the deduplication gate. Returns a result dict with keys ``accepted``,
    ``rejected``, ``matches``, ``beforeCount``, ``afterCount``, ``threshold``
    and ``mode``. options keys: ``similarityThreshold``, ``mode``,
    ``maxCandidates``, ``graphName``."""
    options = options or {}
    raw_threshold = options.get("similarityThreshold")
    if raw_threshold is None:
        raw_threshold = DEFAULT_SIMILARITY_THRESHOLD
    threshold = min(max(raw_threshold, MIN_SIMILARITY_THRESHOLD), MAX_SIMILARITY_THRESHOLD)
    mode = options.get("mode")
    if mode is None:
        mode = "reject"
    max_candidates = options.get("maxCandidates")
    if max_candidates is None:
        max_candidates = DEFAULT_MAX_CANDIDATES

    # No embedding manager (or no stored vectors): fall back to name matching.
    if embedding_manager is None:
        return _name_based_deduplication(proposals, store, threshold, mode)
    vectors: dict[str, list[float]] = store.get_entity_vectors()
    if not vectors:
        return _name_based_deduplication(proposals, store, threshold, mode)

    meta = {e["id"]: e for e in _list_entity_docs(store)}

    accepted: list[Doc] = []
    rejected: list[Doc] = []
    matches: list[Doc] = []

    for proposal in proposals:
        text = proposal_to_text(proposal)
        embedding = _embed_text(embedding_manager, text)
        proposal_type = proposal["entity"]["entityType"]

        candidates: list[tuple[float, str, Doc]] = []
        for entity_id, vector in vectors.items():
            existing = meta.get(entity_id)
            if existing is None or existing["entityType"] != proposal_type:
                continue
            score = _cosine_similarity(embedding, vector)
            if score >= threshold:
                candidates.append((score, entity_id, existing))

        if not candidates:
            accepted.append({**proposal, "isDuplicate": False})
            continue

        candidates.sort(key=lambda c: c[0], reverse=True)
        best_score, best_id, best_meta = candidates[:max_candidates][0]
        match: Doc = {
            "proposalName": proposal["entity"]["name"],
            "existingEntityId": best_id,
            "existingEntityName": best_meta["name"],
            "existingEntityType": best_meta["entityType"],
            "similarity": best_score,
        }
        matches.append(match)

        annotated: Doc = {**proposal, "duplicateOf": match, "isDuplicate": True}
        if mode == "reject":
            rejected.append(annotated)
        elif mode == "flag":
            accepted.append(annotated)
        elif mode == "merge":
            accepted.append(_create_merged_proposal(annotated, match))

    return {
        "accepted": accepted,
        "rejected": rejected,
        "matches": matches,
        "beforeCount": len(proposals),
        "afterCount": len(accepted),
        "threshold": threshold,
        "mode": mode,
    }


def _name_based_deduplication(proposals: list[Doc], store: Any, threshold: float, mode: str) -> Doc:
    accepted: list[Doc] = []
    rejected: list[Doc] = []
    matches: list[Doc] = []

    name_map: dict[str, Doc] = {}
    for entity in _list_entity_docs(store):
        name_map[entity["name"].lower()] = {
            "id": entity["id"],
            "name": entity["name"],
            "entityType": entity["entityType"],
        }

    for proposal in proposals:
        normalized = proposal["entity"]["name"].lower()
        existing = name_map.get(normalized)

        if existing is None:
            accepted.append({**proposal, "isDuplicate": False})
            continue

        match: Doc = {
            "proposalName": proposal["entity"]["name"],
            "existingEntityId": existing["id"],
            "existingEntityName": existing["name"],
            "existingEntityType": existing["entityType"],
            "similarity": 1.0,
        }
        matches.append(match)

        annotated: Doc = {**proposal, "duplicateOf": match, "isDuplicate": True}
        if mode == "reject":
            rejected.append(annotated)
        elif mode == "flag":
            accepted.append(annotated)
        elif mode == "merge":
            accepted.append(_create_merged_proposal(annotated, match))

    return {
        "accepted": accepted,
        "rejected": rejected,
        "matches": matches,
        "beforeCount": len(proposals),
        "afterCount": len(accepted),
        "threshold": threshold,
        "mode": mode,
    }


def _create_merged_proposal(proposal: Doc, match: Doc) -> Doc:
    return {
        **proposal,
        "isDuplicate": True,
        "duplicateOf": match,
        "entity": {
            **proposal["entity"],
            "observations": [
                *proposal["entity"]["observations"],
                (
                    f"Merged with existing entity '{match['existingEntityName']}' "
                    f"(similarity: {match['similarity']:.3f})"
                ),
            ],
        },
        "relations": [
            *proposal["relations"],
            {
                "targetId": match["existingEntityId"],
                "relationType": "related_to",
                "direction": "outgoing",
            },
        ],
        "confidence": proposal["confidence"] * 0.9,
    }
